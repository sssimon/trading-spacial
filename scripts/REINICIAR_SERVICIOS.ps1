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
$DockerTimeout = 180  # segundos maximos esperando a que Docker arranque

Write-Host ""
Write-Host "  ===================================================" -ForegroundColor Cyan
Write-Host "   REINICIO LIMPIO - Crypto Scanner"                   -ForegroundColor Cyan
Write-Host "  ===================================================" -ForegroundColor Cyan
Write-Host ""

# ── Funciones de deteccion Docker ─────────────────────────────
function Test-DockerProcessRunning {
    $proc = Get-Process "Docker Desktop" -ErrorAction SilentlyContinue
    return ($null -ne $proc)
}

function Test-DockerDaemonReady {
    try {
        $result = docker info 2>&1
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

# ── PASO 0: Verificar / arrancar Docker Desktop ───────────────
Write-Host "  [0/5] Verificando Docker Desktop..." -ForegroundColor Yellow

# Activar auto-inicio con Windows (fix permanente, una sola vez)
$regKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$regVal = Get-ItemProperty -Path $regKey -Name "Docker Desktop" -ErrorAction SilentlyContinue
if (-not $regVal -and (Test-Path $DockerExe)) {
    Set-ItemProperty -Path $regKey -Name "Docker Desktop" -Value "`"$DockerExe`""
    Write-Host "        [OK] Docker Desktop configurado para iniciar con Windows." -ForegroundColor Green
}

if (Test-DockerDaemonReady) {
    Write-Host "        Docker ya estaba listo." -ForegroundColor Gray
} else {
    # Si el proceso no esta corriendo, iniciarlo
    if (-not (Test-DockerProcessRunning)) {
        if (Test-Path $DockerExe) {
            Write-Host "        Iniciando Docker Desktop..." -ForegroundColor Gray
            Start-Process $DockerExe
        } else {
            Write-Host "        [ERROR] Docker Desktop no encontrado en: $DockerExe" -ForegroundColor Red
            Write-Host "                Instala Docker Desktop desde https://www.docker.com/products/docker-desktop" -ForegroundColor Yellow
        }
    } else {
        Write-Host "        Docker Desktop esta iniciando (proceso activo, daemon no listo aun)..." -ForegroundColor Gray
    }

    # Esperar al daemon con dos fases:
    # Fase 1: esperar que el proceso exista (max 30s)
    $elapsed = 0
    while (-not (Test-DockerProcessRunning) -and $elapsed -lt 30) {
        Start-Sleep -Seconds 3
        $elapsed += 3
    }

    # Fase 2: esperar que el daemon responda (max DockerTimeout)
    $elapsed = 0
    Write-Host "        Esperando que Docker este listo..." -ForegroundColor Gray
    while (-not (Test-DockerDaemonReady) -and $elapsed -lt $DockerTimeout) {
        Start-Sleep -Seconds 5
        $elapsed += 5
        $dots = "." * [int]($elapsed / 10)
        Write-Host "        $($elapsed)s$dots" -ForegroundColor DarkGray -NoNewline
        Write-Host "`r" -NoNewline
    }

    if (Test-DockerDaemonReady) {
        Write-Host "        Docker listo ($elapsed s).                    " -ForegroundColor Green
    } else {
        Write-Host "        [AVISO] Docker no respondio en ${DockerTimeout}s." -ForegroundColor Yellow
        Write-Host "                El frontend (puerto 3000) no estara disponible." -ForegroundColor Yellow
        Write-Host "                Alternativa: usa npm run dev en la carpeta frontend (puerto 5173)." -ForegroundColor Yellow
    }
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
if (Test-DockerDaemonReady) {
    Push-Location $RootDir
    docker compose up -d 2>&1 | ForEach-Object { Write-Host "        $_" -ForegroundColor DarkGray }
    Pop-Location
    # Verificar que el contenedor quedo corriendo
    $containers = docker ps --format "{{.Names}}" 2>&1
    if ($containers -match "crypto-scanner-frontend") {
        Write-Host "        Frontend container activo." -ForegroundColor Green
    } else {
        Write-Host "        [AVISO] El contenedor frontend no aparece en docker ps." -ForegroundColor Yellow
    }
} else {
    Write-Host "        [AVISO] Docker no disponible, se omite docker compose." -ForegroundColor Yellow
}

Write-Host ""
if ($task) {
    Write-Host "  Arrancando watchdog via Task Scheduler..." -ForegroundColor Cyan
    Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 5
    $status = (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue).State
    $color  = if ($status -eq "Running") { "Green" } else { "Yellow" }
    Write-Host "  Estado tarea watchdog: $status" -ForegroundColor $color
} else {
    Write-Host "  [AVISO] Tarea '$TaskName' no encontrada." -ForegroundColor Yellow
    Write-Host "          Ejecuta INSTALAR_AUTOSTART.ps1 primero para registrarla." -ForegroundColor Yellow
}

# ── VERIFICACION FINAL ────────────────────────────────────────
Write-Host ""
Write-Host "  Verificando servicios..." -ForegroundColor Cyan
Start-Sleep -Seconds 3

# API
try {
    $apiResp = Invoke-WebRequest -Uri "http://localhost:8000/health" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
    $apiOk = ($apiResp.StatusCode -eq 200)
} catch { $apiOk = $false }

# Frontend
try {
    $feResp = Invoke-WebRequest -Uri "http://localhost:3000" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
    $feOk = ($feResp.StatusCode -eq 200)
} catch { $feOk = $false }

Write-Host ""
Write-Host "  ===================================================" -ForegroundColor Cyan
Write-Host "   ESTADO FINAL" -ForegroundColor Cyan
Write-Host "  ===================================================" -ForegroundColor Cyan
$apiColor = if ($apiOk) { "Green" } else { "Red" }
$feColor  = if ($feOk)  { "Green" } else { "Yellow" }
$apiMark  = if ($apiOk) { "[OK]" }  else { "[NO RESPONDE]" }
$feMark   = if ($feOk)  { "[OK]" }  else { "[NO RESPONDE]" }
Write-Host "  API      http://localhost:8000   $apiMark" -ForegroundColor $apiColor
Write-Host "  Docs     http://localhost:8000/docs" -ForegroundColor Gray
Write-Host "  Frontend http://localhost:3000   $feMark" -ForegroundColor $feColor
if (-not $feOk) {
    Write-Host "           Alternativa dev: cd frontend && npm run dev  (puerto 5173)" -ForegroundColor DarkGray
}
$logPath = Join-Path $RootDir "logs\watchdog.log"
Write-Host "  Log      $logPath" -ForegroundColor Gray
Write-Host "  ===================================================" -ForegroundColor Cyan
Write-Host ""
