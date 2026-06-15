import os, glob, sqlite3


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
