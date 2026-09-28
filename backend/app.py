"""Servidor web (Flask + SocketIO) do sistema de balanças.

Expõe:

* a interface web (frontend) em ``/``;
* uma API REST em ``/api/*`` para configuração e comandos;
* um canal WebSocket (SocketIO) para atualização do peso em tempo real.

O servidor pode escutar em ``0.0.0.0`` para ser acessado de qualquer máquina
da rede, ou apenas em ``127.0.0.1`` para uso local.
"""

from __future__ import annotations

import os
import sys
import threading
from datetime import datetime

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO, emit

# Garante que o pacote "scale" seja importável quando executado diretamente.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scale.config import ConfigManager  # noqa: E402
from scale.connections import list_serial_ports  # noqa: E402
from scale.manager import ScaleManager  # noqa: E402
from scale.protocols import available_protocols  # noqa: E402

# ---------------------------------------------------------------------------
# Caminhos
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)
FRONTEND_DIR = os.path.join(PROJECT_DIR, "frontend")
CONFIG_PATH = os.path.join(PROJECT_DIR, "config", "config.json")

# ---------------------------------------------------------------------------
# Inicialização
# ---------------------------------------------------------------------------
app = Flask(__name__, static_folder=None)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

config = ConfigManager(CONFIG_PATH)

_logs = []
_log_lock = threading.Lock()


def log(msg: str, level: str = "info") -> None:
    """Registra uma mensagem de log e a envia via SocketIO."""
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "level": level,
        "message": msg,
    }
    with _log_lock:
        _logs.append(entry)
        if len(_logs) > 500:
            del _logs[: len(_logs) - 500]
    try:
        socketio.emit("log", entry)
    except Exception:
        pass


manager = ScaleManager(config, log)


def _broadcast(message: dict) -> None:
    """Envia uma mensagem do manager para todos os clientes conectados."""
    event = message.get("event", "message")
    payload = {k: v for k, v in message.items() if k != "event"}
    socketio.emit(event, payload)


manager.add_listener(_broadcast)


# ---------------------------------------------------------------------------
# Frontend estático
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/<path:path>")
def static_files(path):
    return send_from_directory(FRONTEND_DIR, path)


# ---------------------------------------------------------------------------
# API REST
# ---------------------------------------------------------------------------
@app.route("/api/status")
def api_status():
    return jsonify({
        "ok": True,
        "state": manager.state(),
        "cloud": manager.cloud.stats(),
        "server_time": datetime.now().isoformat(timespec="seconds"),
    })


@app.route("/api/state")
def api_state():
    return jsonify({"ok": True, "state": manager.state()})


@app.route("/api/config", methods=["GET"])
def api_get_config():
    return jsonify({"ok": True, "config": config.get()})


@app.route("/api/config", methods=["POST"])
def api_set_config():
    patch = request.get_json(force=True, silent=True) or {}
    new_cfg = config.update(patch)
    # Reinicia a conexão para aplicar mudanças de conexão/protocolo.
    manager.restart()
    log("Configuração atualizada", "info")
    return jsonify({"ok": True, "config": new_cfg})


@app.route("/api/config/reset", methods=["POST"])
def api_reset_config():
    new_cfg = config.reset()
    manager.restart()
    log("Configuração restaurada ao padrão", "warning")
    return jsonify({"ok": True, "config": new_cfg})


@app.route("/api/ports")
def api_ports():
    return jsonify({"ok": True, "ports": list_serial_ports()})


@app.route("/api/protocols")
def api_protocols():
    return jsonify({"ok": True, "protocols": available_protocols()})


@app.route("/api/history")
def api_history():
    return jsonify({"ok": True, "history": manager.history()})


@app.route("/api/history", methods=["DELETE"])
def api_clear_history():
    manager.clear_history()
    log("Histórico limpo", "warning")
    return jsonify({"ok": True})


@app.route("/api/logs")
def api_logs():
    with _log_lock:
        return jsonify({"ok": True, "logs": list(_logs)})


@app.route("/api/command/<name>", methods=["POST"])
def api_command(name):
    body = request.get_json(force=True, silent=True) or {}
    try:
        if name == "tare":
            value = body.get("value")
            state = manager.set_tare(
                float(value) if value is not None and value != "" else None
            )
        elif name == "clear_tare":
            state = manager.clear_tare()
        elif name == "zero":
            state = manager.zero()
        elif name == "record":
            rec = manager.record_weighing(auto=False)
            return jsonify({"ok": True, "record": rec, "state": manager.state()})
        elif name == "connect":
            manager.restart()
            state = manager.state()
        elif name == "disconnect":
            manager.stop()
            state = manager.state()
        else:
            return jsonify({"ok": False, "error": f"Comando desconhecido: {name}"}), 400
        return jsonify({"ok": True, "state": state})
    except Exception as exc:  # noqa: BLE001
        log(f"Erro no comando '{name}': {exc}", "error")
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/cloud/test", methods=["POST"])
def api_cloud_test():
    result = manager.test_cloud()
    if result.get("ok"):
        log("Teste de nuvem bem-sucedido", "info")
    else:
        log(f"Teste de nuvem falhou: {result.get('error')}", "error")
    return jsonify(result)


# ---------------------------------------------------------------------------
# SocketIO
# ---------------------------------------------------------------------------
@socketio.on("connect")
def on_connect():
    emit("weight", manager.state())
    emit("status", {"status": manager.state()["status"],
                    "connected": manager.state()["connected"]})
    with _log_lock:
        emit("log_history", {"logs": list(_logs)})


@socketio.on("request_state")
def on_request_state():
    emit("weight", manager.state())


# ---------------------------------------------------------------------------
# Execução
# ---------------------------------------------------------------------------
def main():
    server_cfg = config.get_section("server")
    host = server_cfg.get("host", "0.0.0.0")
    port = int(server_cfg.get("port", 5000))

    log("=" * 60, "info")
    log("Sistema de Balanças Integra-O iniciado", "info")
    log(f"Servidor em http://{host}:{port}", "info")
    if host == "0.0.0.0":
        log("Acessível na rede local pelo IP desta máquina.", "info")
    log("=" * 60, "info")

    manager.start()
    try:
        socketio.run(app, host=host, port=port, allow_unsafe_werkzeug=True)
    finally:
        manager.stop()


if __name__ == "__main__":
    main()
