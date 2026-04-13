# ==============================================================
#  REINICIO LIMPIO DE SERVICIOS - Crypto Scanner
#  Mata todos los procesos anteriores y arranca desde cero.
#  Incluye arranque automatico de Docker Desktop si esta apagado.
# ==============================================================

$TaskName      = "BTCScannerWatchdog"
$RootDir       = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Definition)
$Ports         = @(8000)
$Scripts       = @("btc_api.py", "watchdog.py")
$PidFiles      = @("btc_api.pid", "watchdog.pid")
$DockerExe     = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
$DockerTimeout = 90   # segundos maximos esperando a que Docker arranque

Write-Host ""
Write-Host "  ===================================================" -ForegroundColor Cyan
Write-Host "   REINICIO LIMPIO - Crypto Scanner"                   -ForegroundColor Cyan
Write-Host "  ===================================================" -ForegroundColor Cyan
Write-Host ""

# ── PASO 0: Verificar / arrancar Docker Desktop ───────────────
Write-Host "  [0/5] Verificando Docker Desktop..." -ForegroundColor Yellow

function Test-DockerRunning {
    try {
        $result = docker info 2>&1
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

if (-not (Test-DockerRunning)) {
    if (Test-Path $DockerExe) {
        Write-Host "        Docker no esta corriendo. Iniciando Docker Desktop..." -ForegroundColor Gray
        Start-Process $DockerExe
        $elapsed = 0
        while (-not (Test-DockerRunning) -and $elapsed -lt $DockerTimeout) {
            Start-Sleep -Seconds 5
            $elapsed += 5
            Write-Host "        Esperando Docker... ($elapsed s)" -ForegroundColor DarkGray
        }
        if (Test-DockerRunning) {
            Write-Host "        Docker listo." -ForegroundColor Green
        } else {
            Write-Host "        [AVISO] Docker no respondio en $DockerTimeout s. Continua de todos modos." -ForegroundColor Yellow
        }
    } else {
        Write-Host "        [AVISO] Docker Desktop no encontrado en: $DockerExe" -ForegroundColor Yellow
    }
} else {
    Write-Host "        Docker ya estaba corriendo." -ForegroundColor Gray
}

Write-Host "  [1/5] Deteniendo tarea del Programador..." -ForegroundColor Yellow
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task -and $task.State -eq "Running") {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    Write-Host "        Tarea detenida." -ForegroundColor Gray
} else {
    Write-Host "        Tarea no estaba corriendo." -ForegroundColor Gray
}

Write-Host "  [2/5] Terminando procesos Python..." -ForegroundColor Yellow
$killed = 0
foreach ($script in $Scripts) {
    $procs = Get-WmiObject Win32_Process |
             Where-Object { $_.Name -match "python" -and $_.CommandLine -like "*$script*" }
    foreach ($p in $procs) {
        Write-Host "        Matando PID $($p.ProcessId) ($script)" -ForegroundColor Gray
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        $killed++
    }
}
if ($killed -eq 0) { Write-Host "        No habia procesos activos." -ForegroundColor Gray }
else { Start-Sleep -Seconds 2 }

Write-Host "  [3/5] Liberando puertos $($Ports -join ', ')..." -ForegroundColor Yellow
foreach ($port in $Ports) {
    $connections = netstat -ano 2>$null | Select-String ":$port\s"
    foreach ($line in $connections) {
        $parts  = ($line.ToString().Trim()) -split '\s+'
        $pidNum = $parts[-1]
        if ($pidNum -match '^\d+$' -and [int]$pidNum -gt 0) {
            Write-Host "        Puerto $port liberado (PID $pidNum)" -ForegroundColor Gray
            Stop-Process -Id ([int]$pidNum) -Force -ErrorAction SilentlyContinue
        }
    }
}
Start-Sleep -Seconds 1

Write-Host "  [4/5] Limpiando archivos .pid..." -ForegroundColor Yellow
foreach ($pidFile in $PidFiles) {
    $path = Join-Path $RootDir $pidFile
    if (Test-Path $path) {
        Remove-Item $path -Force
        Write-Host "        Eliminado: $pidFile" -ForegroundColor Gray
    }
}

Write-Host ""
Write-Host "  [5/5] Levantando contenedores Docker..." -ForegroundColor Yellow
if (Test-DockerRunning) {
    Push-Location $RootDir
    docker compose up -d 2>&1 | ForEach-Object { Write-Host "        $_" -ForegroundColor DarkGray }
    Pop-Location
    Write-Host "        Contenedores levantados." -ForegroundColor Green
} else {
    Write-Host "        [AVISO] Docker no disponible, se omite docker compose." -ForegroundColor Yellow
}

Write-Host ""
if ($task) {
    Write-Host "  Arrancando watchdog via Task Scheduler..." -ForegroundColor Cyan
    Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 4
    $status = (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue).State
    $color  = if ($status -eq "Running") { "Green" } else { "Yellow" }
    Write-Host "  Estado tarea: $status" -ForegroundColor $color
} else {
    Write-Host "  [AVISO] Tarea no encontrada. Ejecuta INSTALAR_AUTOSTART.ps1 primero." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "  === REINICIO COMPLETO =============================" -ForegroundColor Green
Write-Host "  API:      http://localhost:8000"                     -ForegroundColor Gray
Write-Host "  Docs:     http://localhost:8000/docs"                -ForegroundColor Gray
Write-Host "  Frontend: http://localhost:3000"                     -ForegroundColor Gray
$logPath = Join-Path $RootDir "logs\watchdog.log"
Write-Host "  Log:      $logPath"                                  -ForegroundColor Gray
Write-Host ""

