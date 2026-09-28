#!/usr/bin/env python3
"""Testes rápidos dos módulos internos do sistema de balanças."""

import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))

from scale.config import ConfigManager  # noqa: E402
from scale.protocols import build_protocol, available_protocols  # noqa: E402


def test_config():
    cfg = ConfigManager(os.path.join(BASE, "config", "config.json"))
    assert cfg.get()["connection"]["type"] in ("serial", "tcp")
    cfg.update({"weighing": {"unit": "g"}})
    assert cfg.get()["weighing"]["unit"] == "g"
    cfg.update({"weighing": {"unit": "kg"}})
    print("[OK] ConfigManager")


def test_protocols():
    cases = [
        ("generic", {"decimal_places": 0}, "  12.345\r\n", 12.345),
        ("generic", {"decimal_places": 3}, "  12345\r\n", 12.345),
        ("toledo", {"decimal_places": 0}, "ST,GS,+  12.345 kg\r\n", 12.345),
        ("cas", {"decimal_places": 0}, "US,NT,   0.000kg\r\n", 0.0),
        ("aandd", {"decimal_places": 0}, "ST,+0012.345 kg\r\n", 12.345),
        ("filizola", {"decimal_places": 3}, "00012345\r\n", 12.345),
        ("custom_regex", {"regex": r"(?P<weight>[-+]?\d+[\.,]?\d*)", "decimal_places": 0},
         "PESO=7.250kg", 7.25),
    ]
    for proto_type, extra, line, expected in cases:
        cfg = {"type": proto_type, "encoding": "ascii", "line_terminator": "\r\n"}
        cfg.update(extra)
        proto = build_protocol(cfg)
        reading = proto.parse(line)
        assert reading is not None, f"{proto_type}: nenhum parsing para {line!r}"
        assert abs(reading.weight - expected) < 1e-6, \
            f"{proto_type}: esperado {expected}, obtido {reading.weight}"
        print(f"[OK] Protocolo {proto_type}: {line!r} -> {reading.weight}")
    print(f"[OK] {len(available_protocols())} protocolos disponíveis")


if __name__ == "__main__":
    test_config()
    test_protocols()
    print("\nTodos os testes passaram.")
