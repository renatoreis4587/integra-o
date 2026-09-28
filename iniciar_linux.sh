#!/usr/bin/env bash
# ============================================================
#  Integra-O - Sistema de Balancas
#  Inicializacao no Linux / macOS
# ============================================================
set -e
cd "$(dirname "$0")"

echo "============================================================"
echo "  Integra-O - Sistema de Balancas"
echo "============================================================"
echo

# Verifica Python.
if ! command -v python3 >/dev/null 2>&1; then
    echo "[ERRO] Python 3 nao encontrado. Instale o Python 3.9+."
    exit 1
fi

# Cria ambiente virtual na primeira execucao.
if [ ! -d ".venv" ]; then
    echo "[1/3] Criando ambiente virtual..."
    python3 -m venv .venv
fi

echo "[2/3] Ativando ambiente e instalando dependencias..."
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip >/dev/null
pip install -r requirements.txt

echo "[3/3] Iniciando servidor..."
echo
echo "  Acesse no navegador: http://localhost:5000"
echo "  Pressione CTRL+C para encerrar."
echo
python run.py
