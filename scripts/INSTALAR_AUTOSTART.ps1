# ==============================================================
#  CRYPTO SCANNER - Instalador de inicio automatico
#  Registra el watchdog en el Task Scheduler de Windows.
#
#  Ejecutar con PowerShell como Administrador:
#    Click derecho -> "Ejecutar con PowerShell"
# ==============================================================

$TaskName  = "BTCScannerWatchdog"
$RootDir   = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Definition)
$Watchdog  = Join-Path $RootDir "watchdog.py"
$Python    = (Get-Command python -ErrorAction SilentlyContinue).Source

if (-not $Python) {
    Write-Host "[ERROR] Python no encontrado en el PATH." -ForegroundColor Red
    pause; exit 1
}

$PythonW = Join-Path (Split-Path $Python) "pythonw.exe"
if (-not (Test-Path $PythonW)) {
    Write-Host "[AVISO] pythonw.exe no encontrado. Se usara python.exe." -ForegroundColor Yellow
    $PythonW = $Python
}

if (-not (Test-Path $Watchdog)) {
    Write-Host "[ERROR] watchdog.py no encontrado en: $RootDir" -ForegroundColor Red
    pause; exit 1
}

Write-Host ""
Write-Host "  ===================================================" -ForegroundColor Cyan
Write-Host "   CRYPTO SCANNER - Instalando inicio automatico"     -ForegroundColor Cyan
Write-Host "  ===================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Python  : $PythonW"  -ForegroundColor Gray
Write-Host "  Watchdog: $Watchdog" -ForegroundColor Gray
Write-Host "  Tarea   : $TaskName" -ForegroundColor Gray
Write-Host ""

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "  Eliminando tarea anterior..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$Action   = New-ScheduledTaskAction -Execute $PythonW -Argument "`"$Watchdog`"" -WorkingDirectory $RootDir
$Trigger  = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -RestartCount 10 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable:$false `
    -MultipleInstances IgnoreNew
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Settings $Settings -Principal $Principal `
    -Description "Crypto Scanner Watchdog - mantiene corriendo btc_api.py" `
    -Force | Out-Null

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Write-Host "  [OK] Tarea registrada correctamente." -ForegroundColor Green
    $response = Read-Host "  Iniciar los servicios ahora? (S/N)"
    if ($response -match '^[Ss]') {
        Start-ScheduledTask -TaskName $TaskName
        Start-Sleep -Seconds 3
        $status = (Get-ScheduledTask -TaskName $TaskName).State
        $color  = if ($status -eq "Running") { "Green" } else { "Yellow" }
        Write-Host "  Estado: $status" -ForegroundColor $color
    }
    Write-Host ""
    Write-Host "  === INSTALACION COMPLETA =========================" -ForegroundColor Green
    Write-Host "  Servicios arrancan automaticamente al iniciar sesion." -ForegroundColor Green
    $logPath = Join-Path $RootDir "logs\watchdog.log"
    Write-Host "  Log:          $logPath"                               -ForegroundColor Gray
    Write-Host "  Para desinstalar: scripts\DESINSTALAR_AUTOSTART.ps1"  -ForegroundColor Gray
    Write-Host ""
} else {
    Write-Host "  [ERROR] No se pudo registrar la tarea." -ForegroundColor Red
}

pause
