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


def _ti400_p03_frame(weight: float, tare: float, stable: bool) -> bytes:
    """Monta um frame binário do protocolo P03 do Mettler Toledo TI400.

    Estrutura: STX SWA SWB SWC IIIIII TTTTTT CR CS  (18 bytes).

    Usa posição decimal x0.001 (SWA bits 0-2 = 101).
    """
    # SWA: bits6,5=01 (SEMPRE), bits4,3=01 (incremento 1), bits2,1,0=101 (x0.001)
    swa = 0x20 | 0x08 | 0x05
    # SWB: bit4=1, bit5=1 (SEMPRE); bit3 = movimento (instável)
    swb = 0x30 | (0x00 if stable else 0x08)
    # SWC: bits5,6=1 (SEMPRE)
    swc = 0x60
    w_digits = f"{int(round(abs(weight) * 1000)):06d}".encode("ascii")
    t_digits = f"{int(round(abs(tare) * 1000)):06d}".encode("ascii")
    body = bytes([swa, swb, swc]) + w_digits + t_digits + b"\r"
    checksum = sum(body) & 0xFF
    return bytes([0x02]) + body + bytes([checksum])


def format_reading(weight: float, protocol: str, stable: bool,
                   tare: float = 0.0) -> bytes:
    """Gera a mensagem do simulador (bytes) no protocolo escolhido."""
    status = "ST" if stable else "US"
    if protocol == "ti400_p03":
        return _ti400_p03_frame(weight, tare, stable)
    if protocol == "ti400_p10":
        w = f"{weight:.3f}".replace(".", ",")
        g = f"{weight + tare:.3f}".replace(".", ",")
        t = f"{tare:.3f}".replace(".", ",")
        st = "LPF" + ("E" if stable else "I") + "ZKp"
        body = f"TI400 {w} {st} {g} {t}".encode("ascii", errors="ignore")
        checksum = sum(body) & 0xFF
        return bytes([0x02]) + body + b"\r" + bytes([checksum])
    if protocol == "ti400_p08":
        if not stable:
            return b"S I\r\n"
        return f"S   {weight:07.3f} kg\r\n".encode("ascii")
    if protocol == "toledo":
        return f"{status},GS,+{weight:8.3f} kg\r\n".encode("ascii")
    if protocol == "cas":
        return f"{status},GS,{weight:8.3f}kg\r\n".encode("ascii")
    if protocol == "aandd":
        return f"{status},+{weight:8.3f} kg\r\n".encode("ascii")
    if protocol == "filizola":
        return f"{int(round(weight * 1000)):07d}\r\n".encode("ascii")
    # generic
    return f"{weight:10.3f}\r\n".encode("ascii")


def handle_client(conn, protocol: str):
    print(f"[simulador] Cliente conectado: {conn.getpeername()}")
    t0 = time.time()
    try:
        while True:
            t = time.time() - t0
            weight = generate_weight(t)
            stable = (t % 4) < 3  # estável 3s a cada 4s
            payload = format_reading(weight, protocol, stable)
            conn.sendall(payload)
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
                        choices=["generic", "toledo", "cas", "aandd", "filizola",
                                 "ti400_p03", "ti400_p10", "ti400_p08"])
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
