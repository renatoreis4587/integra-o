#!/usr/bin/env bash
# Gera um arquivo ZIP com todo o sistema, pronto para distribuição.
set -e
cd "$(dirname "$0")/.."

NAME="integra-o-sistema-balancas"
OUT="dist/${NAME}.zip"
mkdir -p dist

echo "Gerando ${OUT}..."
zip -r "${OUT}" \
    backend frontend config docs scripts \
    run.py requirements.txt \
    iniciar_windows.bat iniciar_linux.sh \
    README.md \
    -x "*/__pycache__/*" "*.pyc" ".venv/*" "dist/*" "*.git/*"

echo "Pronto: ${OUT}"
