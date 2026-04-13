# ==============================================================
#  ORGANIZAR CARPETA - Crypto Scanner
#  Limpia archivos duplicados, logs huerfanos y cache vieja.
#  Ejecutar una sola vez desde PowerShell.
# ==============================================================

$RootDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Definition)

Write-Host ""
Write-Host "  ================================================" -ForegroundColor Cyan
Write-Host "   ORGANIZANDO CARPETA - Crypto Scanner"           -ForegroundColor Cyan
Write-Host "  ================================================" -ForegroundColor Cyan
Write-Host "  Raiz: $RootDir" -ForegroundColor Gray
Write-Host ""

# 1. Logs huerfanos en raiz
Write-Host "  [1/4] Limpiando logs duplicados en raiz..." -ForegroundColor Yellow

$rootLogs = @("watchdog.log", "webhook.log", "signals_log.txt", "watchdog_stdout.log", "watchdog_stderr.log")
foreach ($f in $rootLogs) {
    $path = Join-Path $RootDir $f
    if (Test-Path $path) {
        $dest = Join-Path $RootDir "logs\$f"
        if (-not (Test-Path $dest)) {
            Move-Item $path $dest -Force
            Write-Host "        Movido: $f  ->  logs\" -ForegroundColor Gray
        } else {
            Remove-Item $path -Force
            Write-Host "        Eliminado (ya existe en logs\): $f" -ForegroundColor Gray
        }
    }
}

# 2. __pycache__ en raiz
Write-Host "  [2/4] Limpiando __pycache__ de raiz..." -ForegroundColor Yellow

$pycache = Join-Path $RootDir "__pycache__"
if (Test-Path $pycache) {
    Remove-Item $pycache -Recurse -Force
    Write-Host "        Eliminado: __pycache__\" -ForegroundColor Gray
} else {
    Write-Host "        No existe __pycache__\ en raiz." -ForegroundColor Gray
}

# 3. Renombrar Backtesting_BTCUSDT -> backtesting
Write-Host "  [3/4] Renombrando Backtesting_BTCUSDT -> backtesting..." -ForegroundColor Yellow

$oldBT = Join-Path $RootDir "Backtesting_BTCUSDT"
$newBT = Join-Path $RootDir "backtesting"
if (Test-Path $oldBT) {
    if (-not (Test-Path $newBT)) {
        Rename-Item $oldBT $newBT
        Write-Host "        Renombrado correctamente." -ForegroundColor Gray
    } else {
        Get-ChildItem $oldBT | Move-Item -Destination $newBT -Force
        Remove-Item $oldBT -Recurse -Force
        Write-Host "        Contenido movido a backtesting\." -ForegroundColor Gray
    }
} else {
    Write-Host "        No encontrado (quizas ya fue renombrado)." -ForegroundColor Gray
}

# 4. Informe VPN fuera de contexto
Write-Host "  [4/4] Revisando docs\..." -ForegroundColor Yellow

$vpnDoc = Join-Path $RootDir "docs\informe_vpn_toronto.md"
if (Test-Path $vpnDoc) {
    Write-Host "        AVISO: docs\informe_vpn_toronto.md no esta relacionado" -ForegroundColor Yellow
    Write-Host "               con el scanner. Puedes borrarlo manualmente."   -ForegroundColor Yellow
} else {
    Write-Host "        docs\ limpio." -ForegroundColor Gray
}

Write-Host ""
Write-Host "  === RESULTADO - estructura final =================" -ForegroundColor Green
Write-Host ""
Write-Host "  Trading\"                                          -ForegroundColor White
Write-Host "  |-- btc_scanner.py        (core scanner)"         -ForegroundColor Gray
Write-Host "  |-- btc_api.py            (API FastAPI :8000)"    -ForegroundColor Gray
Write-Host "  |-- trading_webhook.py    (webhook receiver :9000)"-ForegroundColor Gray
Write-Host "  |-- watchdog.py           (gestor de procesos)"   -ForegroundColor Gray
Write-Host "  |-- config.json           (configuracion)"        -ForegroundColor Gray
Write-Host "  |-- docker-compose.yml    (n8n + frontend)"       -ForegroundColor Gray
Write-Host "  |-- signals.db            (base de datos)"        -ForegroundColor Gray
Write-Host "  |"                                                 -ForegroundColor DarkGray
Write-Host "  |-- logs\                 (todos los logs)"       -ForegroundColor Cyan
Write-Host "  |-- data\                 (xlsx / calculadoras)"  -ForegroundColor Cyan
Write-Host "  |-- backtesting\          (historico BTC)"        -ForegroundColor Cyan
Write-Host "  |-- docs\                 (documentacion)"        -ForegroundColor Cyan
Write-Host "  |-- frontend\             (React + Docker)"       -ForegroundColor Cyan
Write-Host "  |-- n8n\                  (workflow n8n)"         -ForegroundColor Cyan
Write-Host "  |-- scripts\              (PS1 / BAT)"            -ForegroundColor Cyan
Write-Host "  |-- tests\                (pytest)"               -ForegroundColor Cyan
Write-Host ""
Write-Host "  Listo. Los .py en raiz se mantienen para que los imports funcionen." -ForegroundColor Green
Write-Host ""

pause
