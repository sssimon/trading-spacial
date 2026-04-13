# ==============================================================
#  CRYPTO SCANNER - Desinstalador de inicio automatico
#  Detiene el watchdog y elimina la tarea del Task Scheduler.
# ==============================================================

$TaskName = "BTCScannerWatchdog"

Write-Host ""
Write-Host "  ===================================================" -ForegroundColor Cyan
Write-Host "   CRYPTO SCANNER - Desinstalando inicio automatico" -ForegroundColor Cyan
Write-Host "  ===================================================" -ForegroundColor Cyan
Write-Host ""

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "  [AVISO] La tarea '$TaskName' no existe." -ForegroundColor Yellow
    pause; exit 0
}

if ($task.State -eq "Running") {
    Write-Host "  Deteniendo tarea en ejecucion..." -ForegroundColor Yellow
    Stop-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 2
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false

$response = Read-Host "  Detener tambien los procesos Python del scanner? (S/N)"
if ($response -match '^[Ss]') {
    $procs = Get-WmiObject Win32_Process | Where-Object {
        $_.Name -match "python" -and $_.CommandLine -match "(btc_api|trading_webhook|watchdog)"
    }
    if ($procs) {
        foreach ($p in $procs) {
            Write-Host "  Terminando PID $($p.ProcessId)..." -ForegroundColor Gray
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        }
        Write-Host "  Procesos terminados." -ForegroundColor Green
    } else {
        Write-Host "  No se encontraron procesos activos." -ForegroundColor Gray
    }
}

Write-Host ""
Write-Host "  [OK] Inicio automatico desinstalado." -ForegroundColor Green
Write-Host "  Los servicios NO arrancaran mas al iniciar sesion." -ForegroundColor Gray
Write-Host ""
pause
