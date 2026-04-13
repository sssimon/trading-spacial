@echo off
title Crypto Scanner — Top 20 USDT  1H + Gatillo 5M

:: ─────────────────────────────────────────────────────────────
::  CRYPTO SCANNER  |  Ultimate Macro & Order Flow V6.0
::  Top 20 pares USDT  |  Señal 1H + Gatillo 5M
::  Revisa cada 5 minutos. Cierra esta ventana para detener.
:: ─────────────────────────────────────────────────────────────

set ROOT=%~dp0..

echo.
echo  =========================================================
echo   CRYPTO SCANNER  ^|  Top 20 USDT  ^|  1H + 5M
echo  =========================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python no encontrado.
    pause & exit /b 1
)

echo  Verificando dependencias...
pip install -r "%ROOT%\requirements_scanner.txt" --quiet

echo  Iniciando scanner...
echo  Log: logs\signals_log.txt
echo.

python "%ROOT%\btc_scanner.py"

echo.
echo  Scanner detenido.
pause
