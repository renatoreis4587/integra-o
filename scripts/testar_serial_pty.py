#!/usr/bin/env python3
"""Valida a leitura serial (RS232) usando um pseudo-terminal (pty).

Cria um par mestre/escravo, abre o escravo com pyserial (como se fosse uma
porta RS232) e injeta frames TI400 P03 no mestre, verificando se o sistema
lê e interpreta corretamente.
"""

import os
import pty
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from scale.connections import SerialConnection  # noqa: E402
from scale.protocols import Ti400P03Protocol  # noqa: E402

FAILED = 0


def check(label, cond):
    global FAILED
    if not cond:
        FAILED += 1
    print(f"[{'OK' if cond else 'FALHOU'}] {label}")


def build_p03(weight, swa_bits=0b101, stable=True):
    swa = 0x20 | 0x08 | (swa_bits & 0x07)
    swb = 0x30 | (0x00 if stable else 0x08)
    swc = 0x60
    factor = {1: 10, 2: 1, 3: 0.1, 4: 0.01, 5: 0.001, 6: 0.0001}[swa_bits]
    w = f"{int(round(abs(weight) / factor)):06d}".encode()
    body = bytes([swa, swb, swc]) + w + b"000000\r"
    return bytes([0x02]) + body + bytes([sum(body) & 0xFF])


def main():
    master, slave = pty.openpty()
    slave_name = os.ttyname(slave)

    conn = SerialConnection({
        "port": slave_name, "baudrate": 9600, "bytesize": 8,
        "parity": "N", "stopbits": 1, "timeout": 1.0,
    })
    conn.open()
    check("Abre porta serial (pty)", conn.is_open)

    proto = Ti400P03Protocol({})

    def writer():
        time.sleep(0.3)
        for w in (5.27, 12.34, 7.0):
            os.write(master, build_p03(w))
            time.sleep(0.4)

    t = threading.Thread(target=writer, daemon=True)
    t.start()

    got = []
    deadline = time.time() + 4.0
    while time.time() < deadline and len(got) < 3:
        data = conn.read_raw()
        if data:
            got.extend(proto.feed(data))

    conn.close()
    check("Leu 3 frames P03 via serial", len(got) >= 3)
    if len(got) >= 3:
        check("Peso 1 = 5.270", abs(got[0].weight - 5.27) < 1e-6)
        check("Peso 2 = 12.34", abs(got[1].weight - 12.34) < 1e-6)
        check("Peso 3 = 7.0", abs(got[2].weight - 7.0) < 1e-6)

    os.close(master)
    os.close(slave)

    print()
    if FAILED:
        print(f"{FAILED} teste(s) falharam.")
        sys.exit(1)
    print("Teste serial (pty) passou.")


if __name__ == "__main__":
    main()
