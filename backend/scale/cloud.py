"""Sincronização das pesagens com a nuvem.

Provedores suportados:

* ``local``            – grava em uma pasta local;
* ``onedrive_folder``  – grava na pasta local sincronizada pelo OneDrive;
* ``gdrive_folder``    – grava na pasta local sincronizada pelo Google Drive;
* ``onedrive_api``     – envia via Microsoft Graph (OneDrive) usando OAuth2;
* ``gdrive_api``       – envia via Google Drive API (service account / OAuth);
* ``webdav``           – envia via WebDAV (Nextcloud, ownCloud, etc.).

O envio é feito por uma fila assíncrona em thread separada, de modo que a
leitura da balança nunca seja bloqueada por latência de rede. Se a nuvem
estiver indisponível, as pesagens ficam em fila para reenvio.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import os
import queue
import threading
import time
from datetime import datetime
from typing import Any, Dict, Optional


class CloudSync:
    """Gerencia o envio das pesagens para o destino configurado."""

    def __init__(self, config_manager, logger=None):
        self.cfg = config_manager
        self.logger = logger or (lambda msg, level="info": None)
        self._queue: "queue.Queue[dict]" = queue.Queue(
            maxsize=int(self.cfg.get_section("cloud").get("queue_max", 1000))
        )
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._stats = {"saved": 0, "failed": 0, "queued": 0, "last_error": ""}
        self._lock = threading.Lock()
        # Cache do token de acesso do OneDrive.
        self._od_token: Optional[str] = None
        self._od_token_exp: float = 0.0
        # Cache do serviço do Google Drive.
        self._gdrive_service = None
        # Cache do gdrive folder id resolvido por nome.
        self._gdrive_folder_id: Optional[str] = None

    # -- Ciclo de vida -----------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._worker, name="CloudSync", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def stats(self) -> dict:
        with self._lock:
            return dict(self._stats)

    # -- API pública -------------------------------------------------------
    def enqueue(self, record: Dict[str, Any]) -> bool:
        """Adiciona uma pesagem à fila de envio (não bloqueante)."""
        cloud = self.cfg.get_section("cloud")
        if not cloud.get("enabled", True):
            return False
        record = dict(record)
        record.setdefault("timestamp", datetime.now().isoformat(timespec="seconds"))
        try:
            self._queue.put_nowait(record)
            with self._lock:
                self._stats["queued"] += 1
            return True
        except queue.Full:
            self.logger("Fila de nuvem cheia; pesagem descartada.", "warning")
            return False

    def save_now(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Salva uma pesagem imediatamente (síncrono). Retorna o resultado."""
        cloud = self.cfg.get_section("cloud")
        if not cloud.get("enabled", True):
            return {"ok": False, "error": "Sincronização desabilitada"}
        record = dict(record)
        record.setdefault("timestamp", datetime.now().isoformat(timespec="seconds"))
        try:
            self._deliver(record, cloud)
            with self._lock:
                self._stats["saved"] += 1
            return {"ok": True}
        except Exception as exc:
            with self._lock:
                self._stats["failed"] += 1
                self._stats["last_error"] = str(exc)
            self.logger(f"Falha ao salvar na nuvem: {exc}", "error")
            return {"ok": False, "error": str(exc)}

    # -- Worker assíncrono -------------------------------------------------
    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                record = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            cloud = self.cfg.get_section("cloud")
            try:
                self._deliver(record, cloud)
                with self._lock:
                    self._stats["saved"] += 1
            except Exception as exc:
                with self._lock:
                    self._stats["failed"] += 1
                    self._stats["last_error"] = str(exc)
                self.logger(f"Falha ao salvar na nuvem: {exc}", "error")
                # Reenfileira uma vez (com pequeno atraso) para retry.
                time.sleep(1.0)
                try:
                    self._queue.put_nowait(record)
                except queue.Full:
                    pass
            finally:
                self._queue.task_done()

    # -- Roteamento de provedores -----------------------------------------
    def _deliver(self, record: Dict[str, Any], cloud: dict) -> None:
        provider = cloud.get("provider", "local")
        fmt = cloud.get("format", "csv")
        filename = cloud.get("filename", "pesagens.csv")

        if provider in ("local", "onedrive_folder", "gdrive_folder"):
            self._save_local(cloud, record, fmt, filename)
        elif provider == "webdav":
            self._save_webdav(cloud, record, fmt, filename)
        elif provider == "onedrive_api":
            self._save_onedrive_api(cloud, record, fmt, filename)
        elif provider == "gdrive_api":
            self._save_gdrive_api(cloud, record, fmt, filename)
        else:
            self._save_local(cloud, record, fmt, filename)

    # -- Serialização ------------------------------------------------------
    def _serialize(self, record: Dict[str, Any], fmt: str) -> str:
        if fmt == "json":
            return json.dumps(record, ensure_ascii=False) + "\n"
        if fmt == "txt":
            peso = record.get("weight", "")
            ts = record.get("timestamp", "")
            return f"{ts}\t{peso}\n"
        # CSV (padrão)
        fields = ["timestamp", "weight", "unit", "tare", "gross",
                  "net", "stable", "operator", "scale"]
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
        writer.writerow({k: record.get(k, "") for k in fields})
        return buf.getvalue()

    def _csv_header(self) -> str:
        fields = ["timestamp", "weight", "unit", "tare", "gross",
                  "net", "stable", "operator", "scale"]
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fields)
        writer.writeheader()
        return buf.getvalue()

    # -- Provedor: pasta local / pasta sincronizada -----------------------
    def _save_local(self, cloud: dict, record: dict, fmt: str,
                    filename: str) -> None:
        folder = cloud.get("local_folder") or "./backend/data"
        folder = os.path.abspath(os.path.expanduser(folder))
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, filename)
        content = self._serialize(record, fmt)
        new_file = not os.path.exists(path)
        with open(path, "a", encoding="utf-8", newline="") as fh:
            if fmt == "csv" and new_file:
                fh.write(self._csv_header())
            fh.write(content)
        self.logger(f"Pesagem salva em {path}", "info")

    # -- Provedor: WebDAV --------------------------------------------------
    def _save_webdav(self, cloud: dict, record: dict, fmt: str,
                     filename: str) -> None:
        import urllib.request

        wd = cloud.get("webdav", {})
        base_url = (wd.get("url") or "").rstrip("/")
        if not base_url:
            raise ValueError("URL do WebDAV não configurada")
        username = wd.get("username", "")
        password = wd.get("password", "")

        # Baixa o arquivo atual (se existir) para concatenar.
        url = f"{base_url}/{filename}"
        existing = ""
        req = urllib.request.Request(url, method="GET")
        if username:
            token = base64.b64encode(f"{username}:{password}".encode()).decode()
            req.add_header("Authorization", f"Basic {token}")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                existing = resp.read().decode("utf-8", errors="ignore")
        except Exception:
            existing = ""

        new_file = not existing
        content = existing
        if fmt == "csv" and new_file:
            content += self._csv_header()
        content += self._serialize(record, fmt)

        put = urllib.request.Request(url, data=content.encode("utf-8"),
                                     method="PUT")
        put.add_header("Content-Type", "text/plain; charset=utf-8")
        if username:
            token = base64.b64encode(f"{username}:{password}".encode()).decode()
            put.add_header("Authorization", f"Basic {token}")
        with urllib.request.urlopen(put, timeout=15) as resp:
            resp.read()
        self.logger(f"Pesagem enviada via WebDAV para {url}", "info")

    # -- Provedor: OneDrive via Microsoft Graph ---------------------------
    def _get_od_token(self, api: dict) -> str:
        """Obtém (e cacheia) um access token do OneDrive via refresh token."""
        if self._od_token and time.time() < self._od_token_exp - 60:
            return self._od_token
        import urllib.parse
        import urllib.request

        tenant = api.get("tenant_id", "common")
        token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
        data = urllib.parse.urlencode({
            "client_id": api.get("client_id", ""),
            "client_secret": api.get("client_secret", ""),
            "refresh_token": api.get("refresh_token", ""),
            "grant_type": "refresh_token",
            "scope": "https://graph.microsoft.com/.default offline_access",
        }).encode()
        req = urllib.request.Request(token_url, data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode())
        self._od_token = payload["access_token"]
        self._od_token_exp = time.time() + int(payload.get("expires_in", 3600))
        return self._od_token

    def _save_onedrive_api(self, cloud: dict, record: dict, fmt: str,
                           filename: str) -> None:
        import urllib.request

        api = cloud.get("onedrive_api", {})
        if not api.get("client_id") or not api.get("refresh_token"):
            raise ValueError(
                "OneDrive API requer client_id, client_secret e refresh_token"
            )
        token = self._get_od_token(api)
        folder = (api.get("folder") or "").strip("/")
        remote_path = f"{folder}/{filename}" if folder else filename

        # Baixa conteúdo atual (se existir) para concatenar.
        url = f"https://graph.microsoft.com/v1.0/me/drive/root:/{remote_path}:/content"
        existing = ""
        get_req = urllib.request.Request(url, method="GET")
        get_req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(get_req, timeout=15) as resp:
                existing = resp.read().decode("utf-8", errors="ignore")
        except Exception:
            existing = ""

        new_file = not existing
        content = existing
        if fmt == "csv" and new_file:
            content += self._csv_header()
        content += self._serialize(record, fmt)

        put_req = urllib.request.Request(
            url, data=content.encode("utf-8"), method="PUT"
        )
        put_req.add_header("Authorization", f"Bearer {token}")
        put_req.add_header("Content-Type", "text/plain; charset=utf-8")
        with urllib.request.urlopen(put_req, timeout=20) as resp:
            resp.read()
        self.logger(f"Pesagem enviada ao OneDrive ({remote_path})", "info")

    # -- Provedor: Google Drive API ---------------------------------------
    def _get_gdrive_service(self, api: dict):
        if self._gdrive_service is not None:
            return self._gdrive_service
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise ImportError(
                "Google Drive API requer: pip install google-api-python-client "
                "google-auth google-auth-httplib2"
            ) from exc

        creds_file = api.get("credentials_file", "")
        token_file = api.get("token_file", "")
        scopes = ["https://www.googleapis.com/auth/drive"]

        creds = None
        if creds_file and os.path.exists(creds_file):
            creds = service_account.Credentials.from_service_account_file(
                creds_file, scopes=scopes
            )
        elif token_file and os.path.exists(token_file):
            from google.oauth2.credentials import Credentials
            creds = Credentials.from_authorized_user_file(token_file, scopes)
        else:
            raise ValueError(
                "Google Drive API requer credentials_file (service account) "
                "ou token_file (OAuth) válido"
            )
        self._gdrive_service = build("drive", "v3", credentials=creds,
                                     cache_discovery=False)
        return self._gdrive_service

    def _resolve_gdrive_folder(self, service, folder_id: str, folder_name: str):
        if folder_id:
            return folder_id
        if self._gdrive_folder_id:
            return self._gdrive_folder_id
        if not folder_name:
            return None
        # Procura pasta por nome.
        query = (
            "mimeType='application/vnd.google-apps.folder' and "
            f"name='{folder_name}' and trashed=false"
        )
        res = service.files().list(q=query, spaces="drive",
                                   fields="files(id,name)").execute()
        files = res.get("files", [])
        if files:
            self._gdrive_folder_id = files[0]["id"]
            return self._gdrive_folder_id
        # Cria a pasta.
        meta = {"name": folder_name,
                "mimeType": "application/vnd.google-apps.folder"}
        created = service.files().create(body=meta, fields="id").execute()
        self._gdrive_folder_id = created["id"]
        return self._gdrive_folder_id

    def _save_gdrive_api(self, cloud: dict, record: dict, fmt: str,
                         filename: str) -> None:
        from googleapiclient.http import MediaIoBaseUpload

        api = cloud.get("gdrive_api", {})
        service = self._get_gdrive_service(api)
        folder_name = (api.get("folder") or "").strip("/")
        folder_id = self._resolve_gdrive_folder(
            service, api.get("folder_id", ""), folder_name
        )

        # Procura arquivo existente.
        query = f"name='{filename}' and trashed=false"
        if folder_id:
            query += f" and '{folder_id}' in parents"
        res = service.files().list(q=query, spaces="drive",
                                   fields="files(id,name)").execute()
        files = res.get("files", [])

        # Constrói conteúdo (concatena se existir).
        existing = ""
        if files:
            try:
                existing = service.files().get_media(
                    fileId=files[0]["id"]
                ).execute().decode("utf-8", errors="ignore")
            except Exception:
                existing = ""
        new_file = not existing
        content = existing
        if fmt == "csv" and new_file:
            content += self._csv_header()
        content += self._serialize(record, fmt)

        media = MediaIoBaseUpload(
            io.BytesIO(content.encode("utf-8")),
            mimetype="text/plain", resumable=False,
        )
        if files:
            service.files().update(fileId=files[0]["id"], media_body=media).execute()
        else:
            meta = {"name": filename}
            if folder_id:
                meta["parents"] = [folder_id]
            service.files().create(body=meta, media_body=media,
                                   fields="id").execute()
        self.logger(f"Pesagem enviada ao Google Drive ({filename})", "info")

    # -- Teste de conexão --------------------------------------------------
    def test(self) -> Dict[str, Any]:
        """Testa o destino configurado gravando um registro de teste."""
        record = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "weight": 0.0, "unit": "test", "tare": 0.0,
            "gross": 0.0, "net": 0.0, "stable": True,
            "operator": "test", "scale": "test",
        }
        return self.save_now(record)
