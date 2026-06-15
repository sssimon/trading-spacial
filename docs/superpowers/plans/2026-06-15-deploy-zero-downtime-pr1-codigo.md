# Deploy zero-downtime — PR1 (código) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]` checkboxes.

**Goal:** Preparar el código para desacoplar el scanner del proceso de la API, **100% backward-compatible** (mergeable sin cambiar nada en prod: el unit viejo no setea env → defaults = como hoy). El cutover de infra es PR2 + manual.

**Arquitectura:** Gates de entorno (`RUN_SCANNER`, `SKIP_DB_INIT`) en el lifespan; un entrypoint de scanner-service (`scanner_main.py`); endpoints que derivan la liveness del scanner de la **DB** (no de `_scanner_state` en memoria); `/health/live` de readiness; `backup_db` atómico.

**Tech stack:** Python / FastAPI / SQLite (WAL) / pytest.

**Spec:** [[docs/superpowers/specs/es/2026-06-15-deploy-zero-downtime-decouple-scanner-spec.md]] (v2, tras críticos).

**Invariantes (no romper):** todo cambio default-preserva el comportamiento actual. `python btc_api.py` y los tests (sin env) arrancan el scanner + corren el DDL como hoy. Gate: `python -m pytest tests/ -m "not network" -n auto -q`.

---

## Task 1: `backup_db` atómico (tmpfile + rename)

**Files:** Modify `db/connection.py:100-124`. Test: `tests/test_deploy_decouple.py` (NUEVO).

Hoy `backup_db` escribe directo a `signals_<ts>.db`; un kill a media-`.backup()` deja un backup truncado que la rotación trata como válido (Halberg #6). Fix: escribir a `.tmp` y `os.rename` al completar (atómico).

- [ ] **Step 1: Failing test**
```python
# tests/test_deploy_decouple.py
import os, glob, sqlite3, importlib


def test_backup_db_atomic_no_partial_on_failure(tmp_path, monkeypatch):
    import db.connection as conn
    # DB fuente mínima
    src = tmp_path / "signals.db"
    sqlite3.connect(str(src)).executescript("CREATE TABLE t(x); INSERT INTO t VALUES(1);")
    bdir = tmp_path / "backups"
    monkeypatch.setattr(conn, "_resolve_db_file", lambda: str(src))
    monkeypatch.setattr(conn, "_BACKUP_DIR", str(bdir))
    # Forzar fallo a mitad del backup: el .backup real lanza
    real_connect = sqlite3.connect
    def boom_connect(path, *a, **k):
        c = real_connect(path, *a, **k)
        if str(path).endswith(".tmp"):
            orig = c.backup
            def boom(*aa, **kk): raise RuntimeError("kill a media backup")
            c.backup = boom
        return c
    monkeypatch.setattr(conn.sqlite3, "connect", boom_connect)
    conn.backup_db()  # no debe lanzar (captura) y NO debe dejar un signals_*.db válido
    finals = glob.glob(os.path.join(str(bdir), "signals_*.db"))
    assert finals == [], "un backup fallido no debe dejar un .db final (solo .tmp basura, si acaso)"
```

- [ ] **Step 2: Run → FAIL** `python -m pytest tests/test_deploy_decouple.py::test_backup_db_atomic_no_partial_on_failure -v`

- [ ] **Step 3: Implement** — en `db/connection.py::backup_db`, escribir a `backup_path + ".tmp"` y renombrar:
```python
    backup_path = os.path.join(_BACKUP_DIR, f"signals_{timestamp}.db")
    tmp_path = backup_path + ".tmp"
    try:
        with closing(sqlite3.connect(db_file)) as src:
            with closing(sqlite3.connect(tmp_path)) as dst:
                src.backup(dst)
        os.replace(tmp_path, backup_path)   # rename atómico; solo aparece el .db completo
        log.info(f"DB backup: {backup_path}")
        backups = sorted(glob.glob(os.path.join(_BACKUP_DIR, "signals_*.db")))
        for old in backups[:-_BACKUP_MAX_FILES]:
            os.remove(old)
            log.info(f"DB backup removed: {old}")
    except Exception as e:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        log.warning(f"DB backup failed: {e}")
```

- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Commit** `git commit -m "fix(deploy): backup_db escribe a tmpfile + rename atómico"`

---

## Task 2: Helper de frescura del scanner desde la DB

**Files:** Create `api/scanner_liveness.py`. Test: `tests/test_deploy_decouple.py`.

Deriva la liveness del scanner de la **DB** (no de `_scanner_state` en memoria), para que una API web-only reporte la verdad del `trading-scanner.service`. Lee el último scan ts de la tabla `scans` + counts, y lo envuelve en `LiveSnapshot`.

> Antes de implementar: leer `db/signals.py` para la firma exacta — la tabla `scans`, cómo obtener el último ts (hoy `get_latest_scan` ordena por `id DESC`; la columna de tiempo es `ts`), y un COUNT de scans/signals. Usar `snapshot_connection` (lector WAL, sin writer-lock).

- [ ] **Step 1: Failing test**
```python
def test_scanner_liveness_from_db(monkeypatch):
    from api import scanner_liveness
    from datetime import datetime, timezone, timedelta
    reciente = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    monkeypatch.setattr(scanner_liveness, "_query_scanner_facts",
                        lambda: {"last_scan_ts": reciente, "scans_total": 10, "signals_total": 2})
    snap = scanner_liveness.scanner_liveness(umbral_seg=900)
    assert snap["frescura"]["estado"] == "fresco"
    assert snap["scans_total"] == 10


def test_scanner_liveness_muerto_sin_scans(monkeypatch):
    from api import scanner_liveness
    monkeypatch.setattr(scanner_liveness, "_query_scanner_facts",
                        lambda: {"last_scan_ts": None, "scans_total": 0, "signals_total": 0})
    snap = scanner_liveness.scanner_liveness(umbral_seg=900)
    assert snap["frescura"]["estado"] == "muerto"
```

- [ ] **Step 2: Run → FAIL** (ImportError).

- [ ] **Step 3: Implement** `api/scanner_liveness.py`:
```python
"""Liveness del scanner derivada de la DB (no de memoria de proceso).

Para que una API web-only (sin el thread scanner) reporte la verdad del
trading-scanner.service: el último scan ts vive en la tabla `scans`, no en
`btc_api._scanner_state`. Cumple el non-negotiable #8 (frescura en el
contrato, cross-proceso)."""
from __future__ import annotations

from db.transaction import snapshot_connection
from freshness import LiveSnapshot


def _query_scanner_facts() -> dict:
    """Último scan ts + totales, leídos de la DB (snapshot, WAL-concurrente)."""
    with snapshot_connection() as con:
        row = con.execute("SELECT MAX(ts) FROM scans").fetchone()
        last_scan_ts = row[0] if row else None
        scans_total = con.execute("SELECT COUNT(*) FROM scans").fetchone()[0]
        signals_total = con.execute(
            "SELECT COUNT(*) FROM scans WHERE \"señal\" = 1").fetchone()[0]
    return {"last_scan_ts": last_scan_ts,
            "scans_total": scans_total, "signals_total": signals_total}


def scanner_liveness(*, umbral_seg: float = 900.0) -> dict:
    """Payload de liveness del scanner con su frescura (fresco/rancio/muerto).
    `umbral_seg` por defecto = scan_interval_sec*3 (300*3)."""
    facts = _query_scanner_facts()
    payload = {"scans_total": facts["scans_total"],
               "signals_total": facts["signals_total"],
               "last_scan_ts": facts["last_scan_ts"]}
    return LiveSnapshot(payload=payload, generated_at=facts["last_scan_ts"],
                        umbral_seg=umbral_seg).to_response()
```
> NOTA: verificar el nombre real de la columna de señal en `scans` (`señal`/`signal`) leyendo `db/signals.py`/`db/schema.py` y ajustar la query. Si `ts` no es ISO-8601, adaptar la derivación.

- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Commit** `git commit -m "feat(deploy): helper de liveness del scanner desde la DB (#8)"`

---

## Task 3: `GET /health/live` (readiness desacoplado)

**Files:** Modify `api/health.py`. Test: `tests/test_deploy_decouple.py`.

Readiness que NO toca el scanner: 200 si uvicorn responde + schema presente. Valida una tabla canónica (`users`), no `SELECT 1` pelado (Halberg #7), para detectar schema incompleto.

- [ ] **Step 1: Failing test**
```python
def test_health_live_ok_without_scanner(monkeypatch):
    from fastapi.testclient import TestClient
    import btc_api
    monkeypatch.setenv("RUN_SCANNER", "0")
    monkeypatch.setenv("SKIP_DB_INIT", "1")
    monkeypatch.setenv("RUN_AS_SERVICE", "0")  # evita el guard anti-pytest
    with TestClient(btc_api.app) as c:
        r = c.get("/health/live")
    assert r.status_code == 200
    assert r.json()["ready"] is True
```
> (Este test ejercita el lifespan con los gates; depende de Task 7. Si se ejecuta antes, marcar xfail temporal o reordenar: Task 7 antes que 3. Recomendado: implementar Task 7 primero y este test al final.)

- [ ] **Step 2: Run → FAIL** (404).

- [ ] **Step 3: Implement** — en `api/health.py`, añadir:
```python
@router.get("/health/live", summary="Readiness: proceso + schema listos (sin scanner)")
def health_live():
    """200 si uvicorn responde y el schema está presente. NO toca el scanner —
    es el endpoint que pollea el blue-green deploy. Ver spec §3(A)."""
    try:
        with snapshot_connection() as con:
            con.execute("SELECT 1 FROM users LIMIT 1")
        return {"ready": True}
    except Exception as e:
        return JSONResponse(content={"ready": False, "detail": str(e)}, status_code=503)
```

- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Commit** `git commit -m "feat(deploy): GET /health/live readiness desacoplado del scanner"`

---

## Task 4: `/health` deriva liveness del scanner de la DB + gate 200/503 sobre frescura

**Files:** Modify `api/health.py:107-153`. Test: `tests/test_deploy_decouple.py`.

`/health` deja de leer `_scanner_state` (memoria) y de gatear sobre `running`; pasa a frescura DB (`fresco`→200, `rancio`/`muerto`→503). Quita `errors` (solo-memoria, sin fuente DB).

- [ ] **Step 1: Failing test**
```python
def test_health_503_when_scanner_stale(monkeypatch):
    from fastapi.testclient import TestClient
    import btc_api
    from api import scanner_liveness
    monkeypatch.setenv("RUN_SCANNER", "0"); monkeypatch.setenv("SKIP_DB_INIT", "1"); monkeypatch.setenv("RUN_AS_SERVICE", "0")
    # frescura muerta → 503
    monkeypatch.setattr(scanner_liveness, "_query_scanner_facts",
                        lambda: {"last_scan_ts": None, "scans_total": 0, "signals_total": 0})
    with TestClient(btc_api.app) as c:
        r = c.get("/health")
    assert r.status_code == 503
    assert "errors" not in r.json()["checks"]
```

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Implement** — reescribir `health_check()` para usar `scanner_liveness` en vez de `_btc_api._scanner_state`:
```python
@router.get("/health", summary="Health check (liveness del scanner vía DB)")
def health_check():
    from api.scanner_liveness import scanner_liveness
    checks = {}
    try:
        with snapshot_connection() as con:
            con.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"
    snap = scanner_liveness()              # DB-backed (#8)
    fr = snap["frescura"]["estado"]        # fresco | rancio | muerto
    checks["scanner"] = fr
    checks["scan_freshness"] = fr
    checks["scans_total"] = snap.get("scans_total", 0)
    checks["signals_total"] = snap.get("signals_total", 0)
    healthy = checks["database"] == "ok" and fr == "fresco"
    return JSONResponse(content={"healthy": healthy, "checks": checks},
                        status_code=200 if healthy else 503)
```
(Eliminar el `import btc_api as _btc_api` y el bloque de `_scanner_state`.)

- [ ] **Step 4: Run → PASS** + correr `tests/test_api.py` (si hay tests de /health, actualizarlos al nuevo contrato).
- [ ] **Step 5: Commit** `git commit -m "feat(deploy): /health gatea sobre frescura del scanner desde la DB (#8)"`

---

## Task 5: `/` y `/status` derivan la liveness del scanner de la DB

**Files:** Modify `btc_api.py:317-326` (`/`) y `:473-479` (`/status`). Test: `tests/test_deploy_decouple.py`.

> Leer los handlers exactos. `/` usa `_scanner_state.get("symbols_active", [])` (:323) y `_scanner_state` entero (:326); `/status` usa `_scanner_state` (:479). Reemplazar la fuente del estado del scanner por `scanner_liveness()` (DB) y el `symbols` por `get_active_symbols()`. Preservar el resto de la forma de respuesta.

- [ ] **Step 1: Failing test** — montar la app web-only (RUN_SCANNER=0), mock `scanner_liveness` con ts viejo, GET `/status` → el `scanner_state.frescura.estado` refleja la DB (`muerto`), no `Iniciando...` de memoria.
- [ ] **Step 2: Run → FAIL**
- [ ] **Step 3: Implement** — en `/`: `"symbols": get_active_symbols()`, `"scanner": scanner_liveness()`. En `/status`: `"scanner_state": scanner_liveness()`. (Import lazy de `scanner_liveness` y `get_active_symbols` si hace falta evitar ciclos.)
- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Commit** `git commit -m "feat(deploy): / y /status leen liveness del scanner de la DB"`

---

## Task 6: `/ticker` y `/symbols` usan `get_active_symbols()` directo

**Files:** Modify `btc_api.py:356` y `:441`. Test: cubierto por no-regresión.

Hoy: `symbols = _scanner_state.get("symbols_active") or get_active_symbols()`. La web-only nunca puebla `symbols_active` → cae al fallback igual, pero el read de memoria es ruido y divergiría si una instancia tuviera estado parcial. Quitar el read.

- [ ] **Step 1:** cambiar ambas líneas a `symbols = get_active_symbols()`.
- [ ] **Step 2: Run** `python -m pytest tests/test_api.py -m "not network" -q` → verde (la fuente de verdad es la config).
- [ ] **Step 3: Commit** `git commit -m "feat(deploy): /ticker y /symbols leen el watchlist de la config, no de _scanner_state"`

---

## Task 7: Gates `SKIP_DB_INIT` (incl. `_bootstrap_first_user`) + `RUN_SCANNER` en el lifespan

**Files:** Modify `btc_api.py:247-258`. Test: `tests/test_deploy_decouple.py`.

- [ ] **Step 1: Failing tests**
```python
def test_run_scanner_0_no_threads(monkeypatch):
    import scanner.runtime as rt
    from fastapi.testclient import TestClient
    import btc_api
    monkeypatch.setenv("RUN_SCANNER", "0"); monkeypatch.setenv("SKIP_DB_INIT", "1"); monkeypatch.setenv("RUN_AS_SERVICE", "0")
    rt._managed_threads.clear()
    with TestClient(btc_api.app):
        pass
    assert rt._managed_threads == [], "RUN_SCANNER=0 NO debe arrancar threads"


def test_skip_db_init_no_ddl_no_bootstrap(monkeypatch):
    import btc_api
    from fastapi.testclient import TestClient
    calls = {"init_db": 0, "bootstrap": 0}
    monkeypatch.setattr(btc_api, "init_db", lambda *a, **k: calls.__setitem__("init_db", calls["init_db"]+1))
    monkeypatch.setattr(btc_api, "_bootstrap_first_user", lambda *a, **k: calls.__setitem__("bootstrap", calls["bootstrap"]+1))
    monkeypatch.setenv("RUN_SCANNER", "0"); monkeypatch.setenv("SKIP_DB_INIT", "1"); monkeypatch.setenv("RUN_AS_SERVICE", "0")
    with TestClient(btc_api.app):
        pass
    assert calls["init_db"] == 0 and calls["bootstrap"] == 0
```

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Implement** — reescribir el bloque del lifespan (`btc_api.py:247-258`) según spec §4.1:
```python
    if os.getenv("SKIP_DB_INIT") != "1":
        log.info("Initializing DB schema…")
        init_db()
        with transaction() as con:
            init_auth_db(con)
            init_system_state(con)
        _bootstrap_first_user()
    else:
        log.info("SKIP_DB_INIT=1 — schema y bootstrap los hace trading-scanner.service")
    if os.getenv("RUN_SCANNER", "1") == "1":
        log.info("Starting scanner thread…")
        start_scanner_thread()
    else:
        log.info("RUN_SCANNER=0 — instancia web-only, scanner desacoplado")
```
(`_jwt_secret()` en :245 queda FUERA del gate.)

- [ ] **Step 4: Run → PASS** + el suite completo (los tests existentes corren sin env → defaults → scanner+DDL como hoy).
- [ ] **Step 5: Commit** `git commit -m "feat(deploy): gates RUN_SCANNER y SKIP_DB_INIT en el lifespan (backward-compatible)"`

---

## Task 8: `/scan` → 409 cuando `RUN_SCANNER != 1`

**Files:** Modify `btc_api.py:493+` (el handler POST `/scan`). Test: `tests/test_deploy_decouple.py`.

- [ ] **Step 1: Failing test**
```python
def test_scan_409_on_web_only(monkeypatch):
    from fastapi.testclient import TestClient
    import btc_api
    monkeypatch.setenv("RUN_SCANNER", "0"); monkeypatch.setenv("SKIP_DB_INIT", "1"); monkeypatch.setenv("RUN_AS_SERVICE", "0")
    with TestClient(btc_api.app) as c:
        r = c.post("/scan")   # ajustar a la firma real (params/auth si aplica)
    assert r.status_code == 409
```
> Leer el handler `/scan` para su firma/auth real y ajustar el test (puede requerir api-key/role).

- [ ] **Step 2: Run → FAIL**
- [ ] **Step 3: Implement** — al inicio del handler `/scan`:
```python
    if os.getenv("RUN_SCANNER", "1") != "1":
        raise HTTPException(status_code=409, detail="scan corre en trading-scanner.service")
```
- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Commit** `git commit -m "feat(deploy): /scan devuelve 409 en instancia web-only (RUN_SCANNER=0)"`

---

## Task 9: `scanner_main.py` — entrypoint del scanner-service

**Files:** Create `scanner_main.py` (raíz). Test: `tests/test_deploy_decouple.py`.

Corre el DDL (dueño del schema), envía `sd_notify(READY=1)`, arranca los threads, instala el handler de SIGTERM/SIGINT, y bloquea. NO arranca uvicorn.

- [ ] **Step 1: Failing test** (importable + el handler para el stop event)
```python
def test_scanner_main_importable_and_sd_notify_noop_without_socket(monkeypatch):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    import importlib, scanner_main
    importlib.reload(scanner_main)
    scanner_main._sd_notify("READY=1")  # sin NOTIFY_SOCKET → no-op, no lanza
```

- [ ] **Step 2: Run → FAIL** (ModuleNotFoundError).

- [ ] **Step 3: Implement** `scanner_main.py`:
```python
"""Entrypoint del trading-scanner.service: corre el scanner desacoplado de la
API. Dueño del schema (DDL + bootstrap), notifica readiness a systemd
(Type=notify) tras migrar, arranca los 5 threads, y para limpio en SIGTERM.
NO arranca uvicorn. Ver spec §4.2."""
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
    """sd_notify sin dependencia: escribe `state` al $NOTIFY_SOCKET (si existe).
    No-op cuando no corre bajo systemd Type=notify."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):              # abstract namespace
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.sendall(state.encode("utf-8"))
    except OSError as e:
        log.warning("sd_notify falló: %s", e)


def main() -> int:
    from db.connection import init_db
    from db.transaction import transaction
    from db.schema import init_system_state
    from auth.db import init_auth_db
    from btc_api import _bootstrap_first_user
    from scanner.runtime import start_scanner_thread, stop_managed_threads, _thread_stop_event

    log.info("scanner-service: migrando schema…")
    init_db()
    with transaction() as con:
        init_auth_db(con)
        init_system_state(con)
    _bootstrap_first_user()
    _sd_notify("READY=1")                 # ← systemd marca el service "ready" SOLO acá (post-DDL)

    def _handler(signum, _frame):
        log.info("señal %s — parando threads…", signum)
        _thread_stop_event.set()
        stop_managed_threads()
    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)

    log.info("scanner-service: arrancando threads…")
    start_scanner_thread()
    _thread_stop_event.wait()             # bloquea hasta SIGTERM
    log.info("scanner-service: salida limpia.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```
> Verificar los imports reales: `init_system_state` (en `db/schema.py`?), `init_auth_db` (en `auth/db.py`? o donde lo importa btc_api), `_bootstrap_first_user` (en btc_api). Ajustar las rutas de import a las reales (leer los imports de `btc_api.py`).

- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Commit** `git commit -m "feat(deploy): scanner_main.py entrypoint del scanner-service (sd_notify + SIGTERM)"`

---

## Task 10: Cierre — suite completa + auto-review

**Files:** —

- [ ] **Step 1:** `python -m pytest tests/ -m "not network" -n auto -q` → todo verde (incluidos los tests existentes con defaults = comportamiento de hoy).
- [ ] **Step 2:** Verificar manualmente: `RUN_SCANNER=0 SKIP_DB_INIT=1 RUN_AS_SERVICE=0 python -c "import btc_api"` importa; y `python scanner_main.py` (sin systemd) arranca el scanner como standalone (Ctrl+C para limpio).
- [ ] **Step 3: Commit** cualquier ajuste.

---

## Self-Review

| Spec | Task |
|---|---|
| backup_db atómico (§8) | 1 |
| frescura desde DB (§3C, #8) | 2, 4, 5 |
| /health/live (§3A) | 3 |
| /health 200/503 + sin `errors` (§3C) | 4 |
| /ticker,/symbols sin _scanner_state (§3C) | 6 |
| gates lifespan + _bootstrap gateado (§4.1) | 7 |
| /scan 409 (§4.1) | 8 |
| scanner_main.py: sd_notify+SIGTERM (§4.2) | 9 |
| backward-compatible (defaults=hoy) | 7, 10 |

**Nota:** los units systemd, nginx, deploy.yml y el cutover son **PR2 + manual** (no en este plan). Este PR no cambia el runtime de prod hasta que PR2 + el cutover activen los gates.
