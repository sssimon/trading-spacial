import os, glob, sqlite3


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
    assert facts["scans_total"] == 1
    assert facts["signals_total"] == 1
    assert facts["last_scan_ts"] is not None


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
