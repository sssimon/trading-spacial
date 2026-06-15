"""Entrypoint del trading-scanner.service: el scanner desacoplado de la API.
Dueño del schema (DDL + bootstrap), notifica readiness a systemd (Type=notify)
tras migrar, arranca los threads, para limpio en SIGTERM. NO arranca uvicorn.
Ver spec §4.2."""
from __future__ import annotations

import os
import signal
import socket
import sys

os.environ.setdefault("RUN_AS_SERVICE", "1")
os.environ.setdefault("RUN_SCANNER", "1")

import logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("scanner_main")


def _sd_notify(state: str) -> None:
    """sd_notify sin dependencia: escribe `state` al $NOTIFY_SOCKET si existe.
    No-op fuera de systemd Type=notify."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.sendall(state.encode("utf-8"))
    except OSError as e:
        log.warning("sd_notify falló: %s", e)


def main() -> int:
    # Imports diferidos: el módulo debe importar sin tocar la DB.
    from db.connection import init_db
    from db.transaction import transaction
    # Rutas verificadas en btc_api.py líneas 39-43:
    #   from db.auth_schema import (has_any_user, init_auth_db, init_system_state, ...)
    from db.auth_schema import init_auth_db, init_system_state
    from btc_api import _bootstrap_first_user
    from scanner.runtime import (
        start_scanner_thread, stop_managed_threads, _thread_stop_event,
    )

    log.info("scanner-service: migrando schema…")
    init_db()
    with transaction() as con:
        init_auth_db(con)
        init_system_state(con)
    _bootstrap_first_user()
    _sd_notify("READY=1")

    def _handler(signum, _frame):
        log.info("señal %s — parando threads…", signum)
        _thread_stop_event.set()
        stop_managed_threads()
    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)

    log.info("scanner-service: arrancando threads…")
    start_scanner_thread()
    _thread_stop_event.wait()
    log.info("scanner-service: salida limpia.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
