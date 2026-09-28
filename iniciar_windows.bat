@echo off
REM ============================================================
REM  Integra-O - Sistema de Balancas
REM  Inicializacao no Windows
REM ============================================================
setlocal
cd /d "%~dp0"

echo ============================================================
echo   Integra-O - Sistema de Balancas
echo ============================================================
echo.

REM Verifica se o Python esta instalado.
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Python nao encontrado. Instale o Python 3.9+ em:
    echo        https://www.python.org/downloads/
    echo        Marque a opcao "Add Python to PATH" durante a instalacao.
    pause
    exit /b 1
)

REM Cria ambiente virtual na primeira execucao.
if not exist ".venv" (
    echo [1/3] Criando ambiente virtual...
    python -m venv .venv
)

echo [2/3] Ativando ambiente e instalando dependencias...
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
pip install -r requirements.txt

echo [3/3] Iniciando servidor...
echo.
echo   Acesse no navegador: http://localhost:5000
echo   Pressione CTRL+C para encerrar.
echo.
python run.py

pause
endlocal
