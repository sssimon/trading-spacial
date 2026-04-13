#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║   CRYPTO SCANNER — WATCHDOG v2                               ║
║   - Instancia única garantizada (archivo .pid)               ║
║   - Libera puertos ocupados antes de arrancar                ║
║   - Mata versiones anteriores de los mismos scripts          ║
║   - Reinicia servicios caídos automáticamente                ║
╚══════════════════════════════════════════════════════════════╝
"""

import subprocess
import sys
import os
import time
import logging
from datetime import datetime, timezone

SCRIPT_DIR     = os.path.dirname(os.path.abspath(__file__))
PYTHON         = sys.executable
LOG_FILE       = os.path.join(SCRIPT_DIR, "logs", "watchdog.log")
os.makedirs(os.path.join(SCRIPT_DIR, "logs"), exist_ok=True)
WATCHDOG_PID   = os.path.join(SCRIPT_DIR, "watchdog.pid")

SERVICES = [
    {
        "name":     "Crypto API",
        "script":   os.path.join(SCRIPT_DIR, "btc_api.py"),
        "port":     8000,
        "pid_file": os.path.join(SCRIPT_DIR, "btc_api.pid"),
        "process":  None,
    },
]

CHECK_INTERVAL = 15   # segundos entre comprobaciones
RESTART_DELAY  = 3    # espera antes de reiniciar

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("watchdog")


# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS DE PROCESO / PUERTO
# ─────────────────────────────────────────────────────────────────────────────

def pid_alive(pid: int) -> bool:
    """Comprueba si un PID sigue activo en Windows."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=5,
        )
        return str(pid) in result.stdout
    except Exception:
        return False


def kill_pid(pid: int, name: str = ""):
    """Mata un proceso por PID."""
    try:
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True, timeout=5,
        )
        log.info(f"Proceso PID {pid} ({name}) terminado.")
    except Exception as e:
        log.warning(f"No se pudo matar PID {pid}: {e}")


def pids_on_port(port: int) -> list:
    """Retorna lista de PIDs escuchando en el puerto dado."""
    try:
        out = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        pids = []
        for line in out.splitlines():
            # Busca líneas con ":PORT " en estado LISTENING o TIME_WAIT
            if f":{port} " in line or f":{port}\t" in line:
                parts = line.split()
                if parts:
                    try:
                        pids.append(int(parts[-1]))
                    except ValueError:
                        pass
        return list(set(pids))
    except Exception as e:
        log.warning(f"Error consultando puerto {port}: {e}")
        return []


def free_port(port: int):
    """Mata todos los procesos que usan el puerto, excepto el propio watchdog."""
    my_pid = os.getpid()
    victims = [p for p in pids_on_port(port) if p != my_pid and p != 0]
    if not victims:
        return
    log.warning(f"Puerto {port} ocupado por PID(s) {victims} — liberando...")
    for pid in victims:
        kill_pid(pid, f"puerto {port}")
    time.sleep(1)   # dar tiempo al SO para liberar el socket


def kill_script_instances(script_path: str):
    """
    Mata cualquier proceso Python que esté ejecutando `script_path`,
    excepto el propio watchdog.
    """
    my_pid    = os.getpid()
    base_name = os.path.basename(script_path)
    try:
        out = subprocess.run(
            ["wmic", "process", "where",
             f"name='python.exe' or name='pythonw.exe'",
             "get", "ProcessId,CommandLine", "/FORMAT:CSV"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        for line in out.splitlines():
            if base_name in line:
                parts = line.split(",")
                try:
                    pid = int(parts[-1].strip())
                    if pid != my_pid:
                        log.warning(f"Instancia previa de {base_name} encontrada (PID {pid}) — terminando…")
                        kill_pid(pid, base_name)
                except (ValueError, IndexError):
                    pass
    except Exception as e:
        log.debug(f"wmic no disponible para buscar {base_name}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  INSTANCIA ÚNICA DEL WATCHDOG
# ─────────────────────────────────────────────────────────────────────────────

def ensure_single_watchdog():
    """
    Si hay otro watchdog.py corriendo, lo termina.
    Escribe el PID propio en watchdog.pid.
    """
    my_pid = os.getpid()
    if os.path.exists(WATCHDOG_PID):
        try:
            old_pid = int(open(WATCHDOG_PID).read().strip())
            if old_pid != my_pid and pid_alive(old_pid):
                log.warning(f"Watchdog anterior activo (PID {old_pid}) — terminándolo…")
                kill_pid(old_pid, "watchdog anterior")
                time.sleep(2)
        except Exception:
            pass
    with open(WATCHDOG_PID, "w") as f:
        f.write(str(my_pid))
    log.info(f"Watchdog único confirmado — PID {my_pid}")


# ─────────────────────────────────────────────────────────────────────────────
#  GESTIÓN DE SERVICIOS
# ─────────────────────────────────────────────────────────────────────────────

def start_service(svc: dict):
    """
    Prepara el entorno (mata instancias previas, libera puerto)
    y arranca el servicio.
    """
    # 1. Matar instancias previas del mismo script
    kill_script_instances(svc["script"])

    # 2. Liberar el puerto
    free_port(svc["port"])

    # 3. Limpiar PID file anterior
    pid_file = svc["pid_file"]
    if os.path.exists(pid_file):
        try:
            old_pid = int(open(pid_file).read().strip())
            if pid_alive(old_pid):
                kill_pid(old_pid, svc["name"])
        except Exception:
            pass
        os.remove(pid_file)

    # 4. Arrancar proceso
    log.info(f"Iniciando: {svc['name']}  ({os.path.basename(svc['script'])})")
    proc = subprocess.Popen(
        [PYTHON, svc["script"]],
        cwd=SCRIPT_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    svc["process"] = proc

    # 5. Guardar PID
    with open(pid_file, "w") as f:
        f.write(str(proc.pid))

    log.info(f"  PID {proc.pid}  →  {svc['name']}  (puerto {svc['port']})")


def check_and_restart(svc: dict):
    """Comprueba si el proceso sigue vivo; si no, libera y reinicia."""
    proc = svc.get("process")
    alive = proc is not None and proc.poll() is None

    if not alive:
        code = proc.returncode if proc else "N/A"
        log.warning(
            f"Servicio caído (exit={code}): {svc['name']}  "
            f"→  reiniciando en {RESTART_DELAY}s…"
        )
        time.sleep(RESTART_DELAY)
        start_service(svc)


def stop_all():
    """Termina todos los servicios y limpia archivos PID."""
    for svc in SERVICES:
        proc = svc.get("process")
        if proc and proc.poll() is None:
            proc.terminate()
            log.info(f"Terminado: {svc['name']} (PID {proc.pid})")
        pid_file = svc["pid_file"]
        if os.path.exists(pid_file):
            os.remove(pid_file)
    # Limpiar PID del watchdog
    if os.path.exists(WATCHDOG_PID):
        os.remove(WATCHDOG_PID)


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    log.info("=" * 60)
    log.info("  CRYPTO SCANNER WATCHDOG v2 — iniciado")
    log.info(f"  Python: {PYTHON}")
    log.info(f"  Directorio: {SCRIPT_DIR}")
    log.info("=" * 60)

    # Garantizar instancia única del watchdog
    ensure_single_watchdog()

    # Arranque inicial: limpia estado y lanza servicios
    for svc in SERVICES:
        start_service(svc)
        time.sleep(2)

    log.info(f"Todos los servicios iniciados. Comprobando cada {CHECK_INTERVAL}s.")

    try:
        while True:
            time.sleep(CHECK_INTERVAL)
            for svc in SERVICES:
                check_and_restart(svc)
    except KeyboardInterrupt:
        log.info("Watchdog detenido por el usuario.")
        stop_all()
        log.info("Watchdog cerrado limpiamente.")


if __name__ == "__main__":
    main()
