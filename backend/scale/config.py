"""Gerenciamento de configuração do sistema de balanças.

Responsável por carregar, validar, salvar e fornecer acesso thread-safe à
configuração persistida em ``config/config.json``. A configuração cobre:

* Conexão com a balança (RS232/serial ou TCP/IP);
* Protocolo de comunicação e parâmetros de parsing;
* Ajustes de pesagem (tara, casas decimais, unidade, estabilização);
* Destino de salvamento em nuvem (local, OneDrive, Google Drive, WebDAV).
"""

from __future__ import annotations

import copy
import json
import os
import threading
from typing import Any, Dict

# ---------------------------------------------------------------------------
# Configuração padrão
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: Dict[str, Any] = {
    "connection": {
        # "serial" (RS232) ou "tcp"
        "type": "serial",
        "serial": {
            "port": "COM1",          # COM1, COM3... (Windows) ou /dev/ttyUSB0 (Linux)
            "baudrate": 9600,
            "bytesize": 8,           # 5, 6, 7, 8
            "parity": "N",           # N, E, O, M, S
            "stopbits": 1,           # 1, 1.5, 2
            "timeout": 1.0,
        },
        "tcp": {
            "host": "192.168.0.100",
            "port": 4001,
            "timeout": 3.0,
        },
        # Reconexão automática
        "auto_reconnect": True,
        "reconnect_delay": 3.0,      # segundos
    },
    "protocol": {
        # Protocolos: generic, toledo, filizola, cas, aandd, custom_regex
        "type": "generic",
        "encoding": "ascii",
        "line_terminator": "\r\n",
        # Usado quando type = custom_regex. Deve conter um grupo nomeado
        # "weight" (e opcionalmente "unit"/"stable").
        "regex": r"(?P<weight>[-+]?\d+[\.,]?\d*)",
        # Extrai o sinal negativo de forma separada (alguns protocolos usam
        # um caractere de status em vez do sinal no número).
        "negative_char": "",
        # Casas decimais quando o protocolo não envia o ponto decimal.
        "decimal_places": 0,
        # Escala aplicada ao valor lido (ex.: 0.001 se vier em gramas).
        "multiplier": 1.0,
    },
    "weighing": {
        "unit": "kg",                # kg, g, t, lb
        "decimal_places": 3,
        # Diferença mínima (na unidade) para considerar o peso "estável".
        "stability_threshold": 0.002,
        # Tempo (s) que o peso deve permanecer dentro do limiar para estabilizar.
        "stability_time": 1.0,
        # Tara padrão aplicada ao iniciar (em unidade de pesagem).
        "default_tare": 0.0,
        # Peso mínimo para considerar uma pesagem válida (evita ruído/zero).
        "min_weight": 0.0,
        # Salvar automaticamente quando o peso estabilizar.
        "auto_save": True,
        # Intervalo mínimo entre salvamentos automáticos (segundos).
        "auto_save_interval": 2.0,
    },
    "cloud": {
        # Provedores: local, onedrive_folder, gdrive_folder, onedrive_api,
        # gdrive_api, webdav
        "provider": "local",
        # Salvar sempre que houver um novo peso (estabilizado).
        "enabled": True,
        "format": "csv",             # csv, json, txt
        "filename": "pesagens.csv",
        # Pasta local (ou pasta sincronizada do OneDrive/Google Drive).
        "local_folder": "./backend/data",
        # OneDrive / Google Drive via pasta sincronizada usam local_folder.
        # WebDAV:
        "webdav": {
            "url": "",
            "username": "",
            "password": "",
        },
        # API OneDrive (Microsoft Graph)
        "onedrive_api": {
            "tenant_id": "common",
            "client_id": "",
            "client_secret": "",
            "refresh_token": "",
            "folder": "IntegraO/Pesagens",
        },
        # API Google Drive
        "gdrive_api": {
            "credentials_file": "",
            "token_file": "",
            "folder_id": "",
        },
        # Envio assíncrono em fila (não bloqueia a leitura da balança).
        "queue_max": 1000,
    },
    "server": {
        "host": "0.0.0.0",
        "port": 5000,
        # Permite acesso de outras máquinas da rede.
        "allow_network": True,
    },
    "ui": {
        "company_name": "Integra-O",
        "operator": "",
        "theme": "dark",
    },
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Mescla recursivamente ``override`` sobre ``base`` (sem mutar as origens)."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


class ConfigManager:
    """Gerencia a configuração persistida em disco com acesso thread-safe."""

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self._lock = threading.RLock()
        self._config: Dict[str, Any] = copy.deepcopy(DEFAULT_CONFIG)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.load()

    # -- Persistência ------------------------------------------------------
    def load(self) -> Dict[str, Any]:
        """Carrega a configuração do disco, aplicando defaults ausentes."""
        with self._lock:
            if os.path.exists(self.path):
                try:
                    with open(self.path, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    self._config = _deep_merge(DEFAULT_CONFIG, data)
                except (json.JSONDecodeError, OSError):
                    # Config corrompida: mantém defaults e recria o arquivo.
                    self._config = copy.deepcopy(DEFAULT_CONFIG)
                    self.save()
            else:
                self.save()
            return copy.deepcopy(self._config)

    def save(self) -> None:
        """Grava a configuração atual no disco de forma atômica."""
        with self._lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._config, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)

    # -- Acesso ------------------------------------------------------------
    def get(self) -> Dict[str, Any]:
        """Retorna uma cópia da configuração completa."""
        with self._lock:
            return copy.deepcopy(self._config)

    def get_section(self, section: str) -> Dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._config.get(section, {}))

    def update(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        """Aplica um patch parcial (merge profundo) e persiste."""
        with self._lock:
            self._config = _deep_merge(self._config, patch)
            self.save()
            return copy.deepcopy(self._config)

    def reset(self) -> Dict[str, Any]:
        """Restaura a configuração padrão."""
        with self._lock:
            self._config = copy.deepcopy(DEFAULT_CONFIG)
            self.save()
            return copy.deepcopy(self._config)
