@echo off
title Crypto Scanner API — localhost:8000

:: ══════════════════════════════════════════════════════════════
::  CRYPTO SCANNER API  |  Ultimate Macro & Order Flow V6.0
::  Top 20 pares USDT  |  Señal 1H + Gatillo 5M  |  Webhook Push
::
::  Documentación interactiva: http://localhost:8000/docs
:: ══════════════════════════════════════════════════════════════

set ROOT=%~dp0..

echo.
echo  ═══════════════════════════════════════════════════════
echo   CRYPTO SCANNER API  ^|  http://localhost:8000
echo   Documentacion  ^|  http://localhost:8000/docs
echo  ═══════════════════════════════════════════════════════
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python no encontrado.
    pause & exit /b 1
)

echo  Instalando/verificando dependencias...
pip install fastapi uvicorn requests pandas numpy --quiet

if not exist "%ROOT%\config.json" (
    echo  [AVISO] config.json no encontrado en %ROOT%
)

echo  Iniciando servidor API...
echo  Presiona Ctrl+C para detener.
echo.

python "%ROOT%\btc_api.py"

echo.
echo  API detenida.
pause
