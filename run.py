#!/usr/bin/env python3
"""Ponto de entrada do Sistema de Balanças Integra-O.

Uso::

    python run.py

O servidor web é iniciado e a interface fica disponível em
``http://localhost:5000`` (ou no IP da máquina na rede local).
"""

import os
import sys

# Garante que o pacote backend seja importável.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE_DIR, "backend"))

from app import main  # noqa: E402

if __name__ == "__main__":
    main()
