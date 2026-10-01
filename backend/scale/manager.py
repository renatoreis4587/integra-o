"""Gerenciador central da balança.

O :class:`ScaleManager` é responsável por:

* abrir/manter a conexão (serial ou TCP) com reconexão automática;
* ler continuamente as linhas, interpretá-las via protocolo e manter o
  estado atual (peso bruto, tara, líquido, estabilidade);
* aplicar comandos de **tara** e **zeragem**;
* disparar callbacks de atualização em tempo real (usados pelo SocketIO);
* acionar o salvamento em nuvem quando uma pesagem estabiliza;
* manter um histórico em memória das últimas pesagens.

O ciclo de vida da conexão é implementado por :meth:`_cycle`, que executa
**um** ciclo completo (conectar → ler → encerrar). O laço externo
:meth:`_run` chama :meth:`_cycle` repetidamente, tratando reconexão e
mudanças de configuração. Isso torna a troca de IP/porta robusta: quando a
configuração muda, :meth:`restart` sinaliza a thread de leitura a interromper
o ciclo atual e reconectar imediatamente com os novos parâmetros.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime
from typing import Any, Callable, Deque, Dict, List, Optional

from .cloud import CloudSync
from .connections import BaseConnection, ConnectionError_, build_connection
from .protocols import build_protocol


class ScaleManager:
    """Orquestra a leitura da balança e o estado de pesagem."""

    def __init__(self, config_manager, logger=None):
        self.cfg = config_manager
        self.logger = logger or (lambda msg, level="info": None)
        self.cloud = CloudSync(config_manager, logger)

        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        # Sinaliza que a configuração mudou e a conexão deve ser refeita.
        self._reconnect = threading.Event()
        self._connection: Optional[BaseConnection] = None
        self._protocol = None

        # Estado atual.
        self._connected = False
        self._status = "desconectado"
        self._raw = ""
        self._gross = 0.0          # peso bruto atual (sem tara)
        self._tare = 0.0           # tara atual
        self._net = 0.0            # peso líquido (bruto - tara)
        self._stable = False
        self._overload = False
        self._negative = False
        self._unit = "kg"
        self._last_update = 0.0

        # Estabilização.
        self._stable_since: Optional[float] = None
        self._last_stable_value: Optional[float] = None
        self._last_autosave = 0.0

        # Callbacks (socketio).
        self._listeners: List[Callable[[dict], None]] = []

        # Histórico.
        self._history: Deque[dict] = deque(maxlen=500)

        # Contador de pesagens.
        self._counter = 0

    # -- Listeners ---------------------------------------------------------
    def add_listener(self, callback: Callable[[dict], None]) -> None:
        self._listeners.append(callback)

    def _emit(self, event: str, payload: dict) -> None:
        message = {"event": event, **payload}
        for cb in list(self._listeners):
            try:
                cb(message)
            except Exception:
                pass

    def _set_status(self, status: str, connected: bool,
                    connection: str = "") -> None:
        """Atualiza o status interno e notifica os clientes."""
        with self._lock:
            self._status = status
            self._connected = connected
        payload = {"status": status, "connected": connected}
        if connection:
            payload["connection"] = connection
        self._emit("status", payload)

    # -- Estado ------------------------------------------------------------
    def state(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "connected": self._connected,
                "status": self._status,
                "raw": self._raw,
                "gross": round(self._gross, 6),
                "tare": round(self._tare, 6),
                "net": round(self._net, 6),
                "stable": self._stable,
                "overload": self._overload,
                "negative": self._negative,
                "unit": self._unit,
                "counter": self._counter,
                "last_update": self._last_update,
                "connection": self._connection.describe()
                if self._connection else "",
            }

    def history(self) -> List[dict]:
        with self._lock:
            return list(self._history)

    def clear_history(self) -> None:
        with self._lock:
            self._history.clear()
            self._counter = 0

    # -- Ciclo de vida -----------------------------------------------------
    def start(self) -> None:
        """Inicia a thread de leitura e a thread de nuvem."""
        self.cloud.start()
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._reconnect.clear()
        self._thread = threading.Thread(
            target=self._run, name="ScaleManager", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._reconnect.set()
        self._close_connection()
        if self._thread:
            self._thread.join(timeout=3.0)
        self.cloud.stop()

    def restart(self) -> None:
        """Solicita a reconexão com a configuração atual.

        Sinaliza a thread de leitura a interromper o ciclo atual e reconectar
        imediatamente, aplicando qualquer mudança de IP/porta/protocolo. É
        seguro chamar mesmo se a thread não estiver rodando.
        """
        self._reconnect.set()
        # Fecha a conexão atual para desbloquear uma leitura em andamento.
        self._close_connection()
        # Garante que a thread esteja viva (ex.: após um stop).
        if not (self._thread and self._thread.is_alive()):
            self.start()

    def _close_connection(self) -> None:
        """Fecha a conexão atual sem manter o lock durante o fechamento."""
        with self._lock:
            conn = self._connection
            self._connection = None
            self._connected = False
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    # -- Loop principal ----------------------------------------------------
    def _run(self) -> None:
        """Laço externo: executa ciclos de conexão/leitura continuamente."""
        while not self._stop.is_set():
            try:
                action = self._cycle()
            except Exception as exc:  # noqa: BLE001 — nunca deixar a thread morrer
                self.logger(f"Erro inesperado no ciclo de leitura: {exc}",
                            "error")
                self._set_status(f"erro: {exc}", False)
                self._close_connection()
                action = "retry"

            if action == "stop":
                break
            if self._stop.is_set():
                break
            # "immediate" = reconectar já (config mudou); "retry" = com atraso.
            if action == "retry":
                delay = self.cfg.get_section("connection").get(
                    "reconnect_delay", 3.0)
                self._sleep(delay)

        self._close_connection()
        self._set_status("desconectado", False)

    def _cycle(self) -> str:
        """Executa um ciclo completo. Retorna a ação para o laço externo.

        Valores de retorno:
        * ``"immediate"`` — reconectar imediatamente (configuração mudou);
        * ``"retry"``     — reconectar após o atraso configurado;
        * ``"stop"``      — encerrar o laço (parada ou sem reconexão).
        """
        conn_cfg = self.cfg.get_section("connection")
        proto_cfg = self.cfg.get_section("protocol")
        weigh_cfg = self.cfg.get_section("weighing")

        self._unit = weigh_cfg.get("unit", "kg")
        self._protocol = build_protocol(proto_cfg)

        # Aplica tara padrão se ainda não houver tara ativa.
        with self._lock:
            if self._tare == 0.0 and weigh_cfg.get("default_tare", 0.0):
                self._tare = float(weigh_cfg.get("default_tare", 0.0))

        # Limpa o pedido de reconexão (a config já foi relida acima).
        self._reconnect.clear()

        # --- Conectar -----------------------------------------------------
        self._set_status("conectando...", False)
        try:
            conn = build_connection(conn_cfg, proto_cfg)
            conn.open()
        except ConnectionError_ as exc:
            self.logger(f"Falha de conexão: {exc}", "error")
            self._set_status(f"erro: {exc}", False)
            if not conn_cfg.get("auto_reconnect", True):
                return "stop"
            return "retry"

        with self._lock:
            self._connection = conn
            self._connected = True
            self._status = "conectado"
        self.logger(f"Conectado: {conn.describe()}", "info")
        self._emit("status", {
            "status": "conectado", "connected": True,
            "connection": conn.describe(),
        })

        # --- Ler ----------------------------------------------------------
        # Usa referência local `conn` para que um restart() que zere
        # self._connection não cause AttributeError.
        while not self._stop.is_set() and not self._reconnect.is_set():
            try:
                line = conn.read_line()
            except ConnectionError_ as exc:
                self.logger(f"Conexão perdida: {exc}", "error")
                self._set_status(f"erro: {exc}", False)
                break

            if line is None:
                if not conn.is_open:
                    break
                continue

            self._handle_line(line, weigh_cfg)

        # --- Encerrar ciclo ----------------------------------------------
        self._close_connection()

        if self._stop.is_set():
            return "stop"

        if self._reconnect.is_set():
            # Configuração mudou: reconecta imediatamente com os novos dados.
            self.logger("Reconectando com a nova configuração...", "info")
            self._set_status("reconectando...", False)
            return "immediate"

        if not conn_cfg.get("auto_reconnect", True):
            self._set_status("desconectado", False)
            return "stop"

        self._set_status("reconectando...", False)
        return "retry"

    def _sleep(self, seconds: float) -> None:
        """Sleep interrompível por stop() ou restart()."""
        deadline = time.time() + max(0.0, seconds)
        while time.time() < deadline:
            if self._stop.wait(0.05):
                return
            if self._reconnect.is_set():
                return

    # -- Tratamento de linha ----------------------------------------------
    def _handle_line(self, line: str, weigh_cfg: dict) -> None:
        reading = self._protocol.parse(line) if self._protocol else None
        if reading is None:
            return

        now = time.time()
        with self._lock:
            self._raw = reading.raw
            self._gross = reading.weight
            self._net = self._gross - self._tare
            self._overload = reading.overload
            self._negative = reading.negative
            self._last_update = now
            self._stable = self._compute_stability(reading.weight, weigh_cfg, now)

        self._emit("weight", self.state())

        # Salvamento automático quando estabiliza.
        if (weigh_cfg.get("auto_save", True) and self._stable
                and not self._overload):
            self._maybe_autosave(weigh_cfg)

    def _compute_stability(self, value: float, weigh_cfg: dict,
                           now: float) -> bool:
        """Determina estabilidade pelo tempo dentro do limiar de variação."""
        threshold = float(weigh_cfg.get("stability_threshold", 0.002))
        stable_time = float(weigh_cfg.get("stability_time", 1.0))
        if self._last_stable_value is None:
            self._last_stable_value = value
            self._stable_since = now
            return False
        if abs(value - self._last_stable_value) <= threshold:
            if self._stable_since is None:
                self._stable_since = now
            if now - self._stable_since >= stable_time:
                self._last_stable_value = value
                return True
        else:
            self._stable_since = now
            self._last_stable_value = value
        return False

    def _maybe_autosave(self, weigh_cfg: dict) -> None:
        now = time.time()
        interval = float(weigh_cfg.get("auto_save_interval", 2.0))
        min_weight = float(weigh_cfg.get("min_weight", 0.0))
        if now - self._last_autosave < interval:
            return
        if abs(self._net) < min_weight:
            return
        self._last_autosave = now
        self.record_weighing(auto=True)

    # -- Comandos ----------------------------------------------------------
    def set_tare(self, value: Optional[float] = None) -> Dict[str, Any]:
        """Define a tara. Se ``value`` for None, usa o peso bruto atual."""
        with self._lock:
            if value is None:
                self._tare = self._gross
            else:
                self._tare = float(value)
            self._net = self._gross - self._tare
            state = self.state()
        self.logger(f"Tara definida: {self._tare}", "info")
        self._emit("weight", state)
        self._emit("command", {"command": "tare", "value": self._tare})
        return state

    def clear_tare(self) -> Dict[str, Any]:
        """Zera a tara (tara = 0)."""
        with self._lock:
            self._tare = 0.0
            self._net = self._gross
            state = self.state()
        self.logger("Tara removida", "info")
        self._emit("weight", state)
        self._emit("command", {"command": "clear_tare"})
        return state

    def zero(self) -> Dict[str, Any]:
        """Zera a balança (ajusta o offset para o peso bruto atual = 0).

        Implementado por software: guarda o offset atual e subtrai das
        leituras. A tara é preservada separadamente.
        """
        with self._lock:
            self._zero_offset = getattr(self, "_zero_offset", 0.0) + self._gross
            self._gross = 0.0
            self._net = self._gross - self._tare
            state = self.state()
        self.logger("Balança zerada", "info")
        self._emit("weight", state)
        self._emit("command", {"command": "zero"})
        return state

    def record_weighing(self, auto: bool = False) -> Dict[str, Any]:
        """Registra a pesagem atual no histórico e envia para a nuvem."""
        ui_cfg = self.cfg.get_section("ui")
        with self._lock:
            self._counter += 1
            record = {
                "id": self._counter,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "weight": round(self._net, 6),
                "unit": self._unit,
                "tare": round(self._tare, 6),
                "gross": round(self._gross, 6),
                "net": round(self._net, 6),
                "stable": self._stable,
                "operator": ui_cfg.get("operator", ""),
                "scale": self._connection.describe() if self._connection else "",
                "auto": auto,
            }
            self._history.append(record)
        # Envia para a nuvem (assíncrono).
        self.cloud.enqueue(record)
        self._emit("saved", record)
        self.logger(f"Pesagem registrada: {record['net']} {record['unit']}",
                    "info")
        return record

    def test_cloud(self) -> Dict[str, Any]:
        return self.cloud.test()
