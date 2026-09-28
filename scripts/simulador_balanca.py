#!/usr/bin/env python3
"""Simulador de balança TCP para testes.

Sobe um servidor TCP que emite leituras de peso contínuas no formato
configurável, permitindo testar o sistema sem hardware real.

Uso::

    python scripts/simulador_balanca.py --port 4001 --protocol generic

Depois, no painel, configure a conexão como TCP/IP apontando para
127.0.0.1:4001.
"""

import argparse
import math
import random
import socket
import threading
import time


def generate_weight(t: float) -> float:
    """Gera um peso com "degraus" estáveis, simulando uma balança real.

    O peso muda a cada ~5s e permanece constante (com ruído mínimo) entre as
    mudanças, permitindo que a detecção de estabilidade seja exercitada.
    """
    step = int(t // 5.0)
    base = 5.0 + (step % 5) * 2.0   # 5, 7, 9, 11, 13 kg ciclando
    noise = random.uniform(-0.0015, 0.0015)
    return max(0.0, base + noise)


def format_reading(weight: float, protocol: str, stable: bool) -> str:
    status = "ST" if stable else "US"
    if protocol == "toledo":
        return f"{status},GS,+{weight:8.3f} kg\r\n"
    if protocol == "cas":
        return f"{status},GS,{weight:8.3f}kg\r\n"
    if protocol == "aandd":
        return f"{status},+{weight:8.3f} kg\r\n"
    if protocol == "filizola":
        return f"{int(round(weight * 1000)):07d}\r\n"
    # generic
    return f"{weight:10.3f}\r\n"


def handle_client(conn, protocol: str):
    print(f"[simulador] Cliente conectado: {conn.getpeername()}")
    t0 = time.time()
    try:
        while True:
            t = time.time() - t0
            weight = generate_weight(t)
            stable = (t % 4) < 3  # estável 3s a cada 4s
            line = format_reading(weight, protocol, stable)
            conn.sendall(line.encode("ascii", errors="ignore"))
            time.sleep(0.2)
    except (BrokenPipeError, ConnectionResetError):
        print("[simulador] Cliente desconectado")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Simulador de balança TCP")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=4001)
    parser.add_argument("--protocol", default="generic",
                        choices=["generic", "toledo", "cas", "aandd", "filizola"])
    args = parser.parse_args()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.host, args.port))
    srv.listen(5)
    print(f"[simulador] Balança simulada em {args.host}:{args.port} "
          f"(protocolo: {args.protocol})")
    print("[simulador] Ctrl+C para encerrar.")
    try:
        while True:
            conn, _ = srv.accept()
            threading.Thread(target=handle_client, args=(conn, args.protocol),
                             daemon=True).start()
    except KeyboardInterrupt:
        print("\n[simulador] Encerrado.")
    finally:
        srv.close()


if __name__ == "__main__":
    main()
