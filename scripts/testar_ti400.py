#!/usr/bin/env python3
"""Testes dos protocolos Mettler Toledo TI400.

Valida o framing binário (P03), a decodificação da posição decimal (SWA),
os parsers ASCII (P10/P08) e o modo automático.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from scale.protocols import (  # noqa: E402
    Ti400P03Protocol,
    Ti400P10Protocol,
    Ti400P08Protocol,
    Ti400AutoProtocol,
    hexdump,
)

FAILED = 0


def check(label, cond):
    global FAILED
    status = "OK" if cond else "FALHOU"
    if not cond:
        FAILED += 1
    print(f"[{status}] {label}")


def build_p03(weight, tare, swa_bits, stable=True, negative=False, overload=False):
    swa = 0x20 | 0x08 | (swa_bits & 0x07)
    swb = 0x30
    if not stable:
        swb |= 0x08
    if negative:
        swb |= 0x02
    if overload:
        swb |= 0x04
    swc = 0x60
    # peso em dígitos crus conforme a posição decimal
    factor = {1: 10, 2: 1, 3: 0.1, 4: 0.01, 5: 0.001, 6: 0.0001}[swa_bits]
    w_digits = f"{int(round(abs(weight) / factor)):06d}".encode()
    t_digits = f"{int(round(abs(tare) / factor)):06d}".encode()
    body = bytes([swa, swb, swc]) + w_digits + t_digits + b"\r"
    return bytes([0x02]) + body + bytes([sum(body) & 0xFF])


def main():
    print("== TI400 P03 (binário) ==")
    p03 = Ti400P03Protocol({})

    # 5.270 kg com posição decimal x0.001
    frame = build_p03(5.270, 0.0, 0b101)
    r = p03.parse_bytes(frame)
    check("P03 peso 5.270 (x0.001)", r is not None and abs(r.weight - 5.270) < 1e-6)
    check("P03 estável", r is not None and r.stable is True)

    # 12.34 kg com x0.01
    r = p03.parse_bytes(build_p03(12.34, 0.0, 0b100))
    check("P03 peso 12.34 (x0.01)", r is not None and abs(r.weight - 12.34) < 1e-6)

    # 7 kg com x1
    r = p03.parse_bytes(build_p03(7.0, 0.0, 0b010))
    check("P03 peso 7 (x1)", r is not None and abs(r.weight - 7.0) < 1e-6)

    # instável
    r = p03.parse_bytes(build_p03(3.5, 0.0, 0b101, stable=False))
    check("P03 instável", r is not None and r.stable is False)

    # negativo
    r = p03.parse_bytes(build_p03(2.0, 0.0, 0b101, negative=True))
    check("P03 negativo", r is not None and r.weight < 0)

    # sobrecarga
    r = p03.parse_bytes(build_p03(0.0, 0.0, 0b101, overload=True))
    check("P03 sobrecarga", r is not None and r.overload is True)

    # tara informada
    r = p03.parse_bytes(build_p03(5.0, 1.5, 0b101))
    check("P03 tara 1.5", r is not None and abs(r.tare - 1.5) < 1e-6)

    print("\n== TI400 P03 (framing em fluxo) ==")
    p03b = Ti400P03Protocol({})
    stream = build_p03(5.0, 0, 0b101) + build_p03(6.0, 0, 0b101)
    readings = p03b.feed(stream)
    check("P03 2 frames em um bloco", len(readings) == 2)
    # entrega byte a byte
    p03c = Ti400P03Protocol({})
    out = []
    for b in stream:
        out.extend(p03c.feed(bytes([b])))
    check("P03 2 frames byte a byte", len(out) == 2 and abs(out[1].weight - 6.0) < 1e-6)
    # lixo antes do STX
    p03d = Ti400P03Protocol({})
    out = p03d.feed(b"\x00\xff\x01" + build_p03(9.0, 0, 0b101))
    check("P03 lixo antes do STX", len(out) == 1 and abs(out[0].weight - 9.0) < 1e-6)

    print("\n== TI400 P10 (string) ==")
    p10 = Ti400P10Protocol({})
    r = p10.parse("\x02TI400 0,269 LPFEZKp 0,627 0,358\r\x00")
    check("P10 peso 0,269", r is not None and abs(r.weight - 0.269) < 1e-6)
    check("P10 estável", r is not None and r.stable is True)
    r = p10.parse("\x02TI400 5,000 LPFIZKp 5,000 0,000\r\x00")
    check("P10 instável", r is not None and r.stable is False)

    print("\n== TI400 P08 (ASCII) ==")
    p08 = Ti400P08Protocol({})
    r = p08.parse("S   09.076 kg")
    check("P08 peso 9.076", r is not None and abs(r.weight - 9.076) < 1e-6)
    r = p08.parse("S I")
    check("P08 instável (S I)", r is not None and r.stable is False)

    print("\n== TI400 Automático ==")
    auto = Ti400AutoProtocol({})
    out = auto.feed(build_p03(4.25, 0, 0b101))
    check("Auto detecta P03", len(out) == 1 and abs(out[0].weight - 4.25) < 1e-6)
    auto2 = Ti400AutoProtocol({})
    out = auto2.feed(b"\x02TI400 0,269 LPFEZKp 0,627 0,358\r\x00")
    check("Auto detecta P10", len(out) == 1 and abs(out[0].weight - 0.269) < 1e-6)

    print("\n== hexdump ==")
    print("   ", hexdump(build_p03(5.27, 0, 0b101)))

    print()
    if FAILED:
        print(f"{FAILED} teste(s) falharam.")
        sys.exit(1)
    print("Todos os testes TI400 passaram.")


if __name__ == "__main__":
    main()
