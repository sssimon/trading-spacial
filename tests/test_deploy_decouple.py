import os, glob, sqlite3


def test_scanner_liveness_from_db(monkeypatch):
    from api import scanner_liveness
    from datetime import datetime, timezone, timedelta
    reciente = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    monkeypatch.setattr(scanner_liveness, "_query_scanner_facts",
                        lambda: {"last_scan_ts": reciente})
    snap = scanner_liveness.scanner_liveness(umbral_seg=900)
    assert snap["frescura"]["estado"] == "fresco"
    assert snap["last_scan_ts"] == reciente


def test_scanner_liveness_muerto_sin_scans(monkeypatch):
    from api import scanner_liveness
    monkeypatch.setattr(scanner_liveness, "_query_scanner_facts",
                        lambda: {"last_scan_ts": None, "scans_total": 0, "signals_total": 0})
    snap = scanner_liveness.scanner_liveness(umbral_seg=900)
    assert snap["frescura"]["estado"] == "muerto"


def test_normalize_scan_ts_handles_utc_suffix():
    from api.scanner_liveness import _normalize_scan_ts
    from datetime import datetime
    # formato real de prod: 'YYYY-MM-DD HH:MM:SS UTC'
    norm = _normalize_scan_ts("2026-06-15 15:06:38 UTC")
    assert datetime.fromisoformat(norm)  # ahora parsea
    assert _normalize_scan_ts(None) is None
    # idempotente para ISO
    assert _normalize_scan_ts("2026-06-15T15:06:38+00:00") == "2026-06-15T15:06:38+00:00"


def test_scanner_liveness_prod_ts_format_is_fresco(monkeypatch):
    # El bug que tumbó el deploy: el ts de scans viene como ' UTC' (no ISO),
    # LiveSnapshot no lo parseaba → 'muerto' permanente aunque el scan fuera
    # reciente. Reproduce el formato exacto de prod.
    import sqlite3
    from contextlib import contextmanager
    from datetime import datetime, timezone, timedelta
    from api import scanner_liveness

    mem = sqlite3.connect(":memory:")
    mem.execute("CREATE TABLE scans (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL)")
    reciente = (datetime.now(timezone.utc) - timedelta(seconds=120)).strftime("%Y-%m-%d %H:%M:%S UTC")
    mem.execute("INSERT INTO scans (ts) VALUES (?)", (reciente,))
    mem.commit()

    @contextmanager
    def fake_snapshot():
        yield mem

    monkeypatch.setattr(scanner_liveness, "_snapshot_connection", fake_snapshot)
    snap = scanner_liveness.scanner_liveness(umbral_seg=900)
    assert snap["frescura"]["estado"] == "fresco", snap["frescura"]


def test_scanner_liveness_query_is_cheap_no_full_scan():
    # Regresión PR1: /health corría COUNT(*)+MAX(ts) (~8s c/u) sobre la tabla
    # scans grande de prod → timeout del health probe. El query DEBE ser barato
    # (ORDER BY id DESC LIMIT 1, id-indexed). Guard contra que vuelva el full-scan.
    import inspect
    from api import scanner_liveness
    # Aislar el CUERPO (sin docstring, que menciona COUNT/MAX en prosa).
    src = inspect.getsource(scanner_liveness._query_scanner_facts)
    body = src.split('"""', 2)[-1].upper()   # todo tras el docstring
    assert "ORDER BY ID DESC LIMIT 1" in body
    assert "COUNT(" not in body, "COUNT(*) es full-scan en la tabla scans grande"
    assert "MAX(" not in body, "MAX(ts) es full-scan"


def test_health_live_is_public():
    # El readiness probe lo pollea el deploy blue-green (PR2) sin auth y un
    # monitor externo — DEBE estar exento del AuthMiddleware o da 401 (lo dio
    # en prod tras PR1).
    from auth.middleware import _PUBLIC_PATHS_EXACT
    assert "/health/live" in _PUBLIC_PATHS_EXACT


def test_scanner_liveness_query_sql_valida(tmp_path, monkeypatch):
    """Valida que _query_scanner_facts usa los nombres de columna reales del schema:
    ts (columna de tiempo) y señal (columna de señal). Crea un in-memory DB con el
    schema real de scans y verifica que la query no falla.
    Columnas verificadas en db/schema.py línea 126-142 y db/signals.py línea 39-44."""
    import sqlite3 as _sqlite3
    import db.connection as conn_mod
    import db.transaction as tx_mod
    from contextlib import contextmanager

    # Crear una DB in-memory con el schema real de scans
    mem_con = _sqlite3.connect(":memory:")
    mem_con.row_factory = _sqlite3.Row
    mem_con.execute("""
        CREATE TABLE scans (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT    NOT NULL,
            symbol      TEXT    NOT NULL DEFAULT 'BTCUSDT',
            estado      TEXT    NOT NULL,
            señal       INTEGER NOT NULL DEFAULT 0,
            setup       INTEGER NOT NULL DEFAULT 0,
            price       REAL,
            lrc_pct     REAL,
            rsi_1h      REAL,
            score       INTEGER,
            score_label TEXT,
            macro_ok    INTEGER,
            gatillo     INTEGER,
            payload     TEXT
        )
    """)
    # Insertar una fila de señal activa
    from datetime import datetime, timezone, timedelta
    ts_reciente = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    mem_con.execute(
        "INSERT INTO scans (ts, symbol, estado, señal) VALUES (?, 'BTCUSDT', 'ok', 1)",
        (ts_reciente,)
    )
    mem_con.commit()

    # Monkeypatch snapshot_connection para devolver la DB en memoria
    from api import scanner_liveness

    @contextmanager
    def fake_snapshot():
        yield mem_con

    monkeypatch.setattr(scanner_liveness, "_snapshot_connection", fake_snapshot)

    facts = scanner_liveness._query_scanner_facts()
    # Query barato (ORDER BY id DESC LIMIT 1): solo el último ts, sin COUNT/MAX.
    assert facts["last_scan_ts"] is not None
    assert "scans_total" not in facts   # full-scans eliminados (regresión PR1)


def test_backup_db_atomic_no_partial_on_failure(tmp_path, monkeypatch):
    import db.connection as conn
    src = tmp_path / "signals.db"
    sqlite3.connect(str(src)).executescript("CREATE TABLE t(x); INSERT INTO t VALUES(1);")
    bdir = tmp_path / "backups"
    monkeypatch.setattr(conn, "_resolve_db_file", lambda: str(src))
    monkeypatch.setattr(conn, "_BACKUP_DIR", str(bdir))
    real_connect = sqlite3.connect
    def boom_connect(path, *a, **k):
        c = real_connect(path, *a, **k)
        if str(path).endswith(".tmp"):
            def boom(*aa, **kk): raise RuntimeError("kill a media backup")
            c.backup = boom
        return c
    monkeypatch.setattr(conn.sqlite3, "connect", boom_connect)
    conn.backup_db()  # captura el error, NO debe dejar un signals_*.db final válido
    finals = glob.glob(os.path.join(str(bdir), "signals_*.db"))
    assert finals == [], f"un backup fallido no debe dejar un .db final, encontré: {finals}"


def test_backup_db_success_creates_final(tmp_path, monkeypatch):
    import db.connection as conn
    src = tmp_path / "signals.db"
    sqlite3.connect(str(src)).executescript("CREATE TABLE t(x); INSERT INTO t VALUES(1);")
    bdir = tmp_path / "backups"
    monkeypatch.setattr(conn, "_resolve_db_file", lambda: str(src))
    monkeypatch.setattr(conn, "_BACKUP_DIR", str(bdir))
    conn.backup_db()
    finals = glob.glob(os.path.join(str(bdir), "signals_*.db"))
    assert len(finals) == 1 and not finals[0].endswith(".tmp")


# ---------------------------------------------------------------------------
# TASK 7: Gates RUN_SCANNER y SKIP_DB_INIT
# ---------------------------------------------------------------------------

def test_run_scanner_0_no_threads(monkeypatch):
    import scanner.runtime as rt
    from fastapi.testclient import TestClient
    import btc_api
    monkeypatch.setenv("RUN_SCANNER", "0")
    monkeypatch.setenv("SKIP_DB_INIT", "1")
    monkeypatch.setenv("RUN_AS_SERVICE", "0")
    rt._managed_threads.clear()
    with TestClient(btc_api.app):
        pass
    assert rt._managed_threads == [], "RUN_SCANNER=0 NO debe arrancar threads"


def test_skip_db_init_no_ddl_no_bootstrap(monkeypatch):
    import btc_api
    from fastapi.testclient import TestClient
    calls = {"init_db": 0, "bootstrap": 0}
    monkeypatch.setattr(btc_api, "init_db", lambda *a, **k: calls.__setitem__("init_db", calls["init_db"] + 1))
    monkeypatch.setattr(btc_api, "_bootstrap_first_user", lambda *a, **k: calls.__setitem__("bootstrap", calls["bootstrap"] + 1))
    monkeypatch.setenv("RUN_SCANNER", "0")
    monkeypatch.setenv("SKIP_DB_INIT", "1")
    monkeypatch.setenv("RUN_AS_SERVICE", "0")
    with TestClient(btc_api.app):
        pass
    assert calls["init_db"] == 0 and calls["bootstrap"] == 0


def test_defaults_preserve_today(monkeypatch):
    # Sin env → DDL corre + scanner arranca (comportamiento de hoy).
    import btc_api
    from fastapi.testclient import TestClient
    calls = {"init_db": 0}
    monkeypatch.setattr(btc_api, "init_db", lambda *a, **k: calls.__setitem__("init_db", calls["init_db"] + 1))
    monkeypatch.setattr(btc_api, "start_scanner_thread", lambda *a, **k: None)
    monkeypatch.delenv("RUN_SCANNER", raising=False)
    monkeypatch.delenv("SKIP_DB_INIT", raising=False)
    monkeypatch.setenv("RUN_AS_SERVICE", "0")
    with TestClient(btc_api.app):
        pass
    assert calls["init_db"] == 1


# ---------------------------------------------------------------------------
# TASK 3: /health/live — readiness probe (proceso + schema, sin scanner)
# TASK 4: /health — gatea sobre frescura DB del scanner
# ---------------------------------------------------------------------------

def test_health_live_ok_without_scanner(monkeypatch):
    from fastapi.testclient import TestClient
    import btc_api
    monkeypatch.setenv("RUN_SCANNER", "0"); monkeypatch.setenv("SKIP_DB_INIT", "1"); monkeypatch.setenv("RUN_AS_SERVICE", "0")
    with TestClient(btc_api.app) as c:
        r = c.get("/health/live")
    assert r.status_code == 200 and r.json()["ready"] is True


def test_health_503_when_scanner_stale(monkeypatch):
    from fastapi.testclient import TestClient
    import btc_api
    from api import scanner_liveness
    monkeypatch.setenv("RUN_SCANNER", "0"); monkeypatch.setenv("SKIP_DB_INIT", "1"); monkeypatch.setenv("RUN_AS_SERVICE", "0")
    monkeypatch.setattr(scanner_liveness, "_query_scanner_facts",
                        lambda: {"last_scan_ts": None, "scans_total": 0, "signals_total": 0})
    with TestClient(btc_api.app) as c:
        r = c.get("/health")
    assert r.status_code == 503
    assert "errors" not in r.json()["checks"]


def test_health_200_when_scanner_fresh(monkeypatch):
    from fastapi.testclient import TestClient
    import btc_api
    from api import scanner_liveness
    from datetime import datetime, timezone
    monkeypatch.setenv("RUN_SCANNER", "0"); monkeypatch.setenv("SKIP_DB_INIT", "1"); monkeypatch.setenv("RUN_AS_SERVICE", "0")
    monkeypatch.setattr(scanner_liveness, "_query_scanner_facts",
                        lambda: {"last_scan_ts": datetime.now(timezone.utc).isoformat(), "scans_total": 5, "signals_total": 1})
    with TestClient(btc_api.app) as c:
        r = c.get("/health")
    assert r.status_code == 200 and r.json()["checks"]["scanner"] == "fresco"


# ---------------------------------------------------------------------------
# DEPLOY DECOUPLE: /scan 409 + /status scanner_state de la DB
# ---------------------------------------------------------------------------

def test_scan_409_on_web_only(monkeypatch):
    """RUN_SCANNER=0 → POST /scan devuelve 409 antes de ejecutar nada."""
    from fastapi.testclient import TestClient
    import btc_api
    from api.deps import verify_api_key
    from auth.dependencies import get_current_user
    from auth.models import User

    monkeypatch.setenv("RUN_SCANNER", "0")
    monkeypatch.setenv("SKIP_DB_INIT", "1")
    monkeypatch.setenv("RUN_AS_SERVICE", "0")

    fake_admin = User(
        id=1, email="admin@test.com", role="admin",
        is_active=True, created_at="2026-01-01T00:00:00",
        password_changed_at="2026-01-01T00:00:00",
    )
    btc_api.app.dependency_overrides[verify_api_key] = lambda: None
    btc_api.app.dependency_overrides[get_current_user] = lambda: fake_admin
    try:
        with TestClient(btc_api.app) as c:
            r = c.post("/scan")
    finally:
        btc_api.app.dependency_overrides.pop(verify_api_key, None)
        btc_api.app.dependency_overrides.pop(get_current_user, None)

    assert r.status_code == 409


def test_status_scanner_from_db(monkeypatch):
    """RUN_SCANNER=0 → GET /status devuelve scanner_state con frescura (de la DB, no de memoria)."""
    from fastapi.testclient import TestClient
    import btc_api
    from api import scanner_liveness
    from api.deps import verify_api_key

    # RUN_SCANNER=0 (web-only) pero SIN SKIP_DB_INIT: el DDL crea el schema, así
    # /status puede leer `scans` (get_latest_scan) sin depender de que OTRO test
    # haya creado la tabla en la DB compartida — fragilidad order-dependent que
    # solo se veía en CI serial (no en -n auto). El punto del test (scanner_state
    # con frescura desde la DB) se valida con RUN_SCANNER=0 + el monkeypatch.
    monkeypatch.setenv("RUN_SCANNER", "0")
    monkeypatch.setenv("RUN_AS_SERVICE", "0")
    monkeypatch.setattr(scanner_liveness, "_query_scanner_facts",
                        lambda: {"last_scan_ts": None, "scans_total": 0, "signals_total": 0})

    btc_api.app.dependency_overrides[verify_api_key] = lambda: None
    try:
        with TestClient(btc_api.app) as c:
            r = c.get("/status")
    finally:
        btc_api.app.dependency_overrides.pop(verify_api_key, None)

    assert r.status_code == 200
    # el estado del scanner ahora trae 'frescura' (DB), no el dict de memoria con 'running'
    assert "frescura" in r.json()["scanner_state"]


# ---------------------------------------------------------------------------
# TASK 9: scanner_main.py — entrypoint del scanner-service (sd_notify)
# ---------------------------------------------------------------------------

def test_scanner_main_importable_and_sd_notify_noop(monkeypatch):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    import importlib
    import scanner_main
    importlib.reload(scanner_main)
    # sin NOTIFY_SOCKET, _sd_notify es no-op y no lanza
    scanner_main._sd_notify("READY=1")


def test_scanner_main_import_does_not_leak_run_as_service(monkeypatch):
    # Importar scanner_main NO debe setear RUN_AS_SERVICE en el proceso. Si lo
    # hiciera (setdefault a nivel de módulo), bajo CI serial filtraría a
    # test_setup.py → el guard anti-pytest del lifespan revienta. Regresión real.
    import os, importlib
    monkeypatch.delenv("RUN_AS_SERVICE", raising=False)
    import scanner_main
    importlib.reload(scanner_main)
    assert os.environ.get("RUN_AS_SERVICE") is None, "import scanner_main filtró RUN_AS_SERVICE"


def test_scanner_liveness_missing_table_is_muerto(monkeypatch):
    # Una API web-only que consulta antes de que el scanner cree el schema
    # debe degradar a 'muerto', no lanzar 500.
    import sqlite3
    from api import scanner_liveness

    class _Con:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a): raise sqlite3.OperationalError("no such table: scans")

    monkeypatch.setattr(scanner_liveness, "_snapshot_connection", lambda: _Con())
    snap = scanner_liveness.scanner_liveness()
    assert snap["frescura"]["estado"] == "muerto"


def test_scanner_main_deferred_imports_resolve():
    # main() difiere sus imports; el test del módulo no los ejercita, así que
    # un import roto (p.ej. init_db de db.connection en vez de db.schema)
    # pasaba desapercibido y crasheaba el scanner-service en prod. Este test
    # importa exactamente los nombres que main() necesita.
    from db.schema import init_db          # noqa: F401
    from db.transaction import transaction  # noqa: F401
    from db.auth_schema import init_auth_db, init_system_state  # noqa: F401
    from btc_api import _bootstrap_first_user  # noqa: F401
    from scanner.runtime import (  # noqa: F401
        start_scanner_thread, stop_managed_threads, _thread_stop_event,
    )


def test_scanner_main_sd_notify_writes_to_socket(tmp_path, monkeypatch):
    import sys
    import importlib
    import pytest

    # AF_UNIX SOCK_DGRAM puede no estar disponible en algunos runners de Windows.
    socket = pytest.importorskip("socket")
    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("AF_UNIX no disponible en este runner")

    import scanner_main
    importlib.reload(scanner_main)

    sock_path = str(tmp_path / "notify.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        srv.bind(sock_path)
        srv.settimeout(2)
        monkeypatch.setenv("NOTIFY_SOCKET", sock_path)
        scanner_main._sd_notify("READY=1")
        data, _ = srv.recvfrom(64)
        assert data == b"READY=1"
    finally:
        srv.close()
