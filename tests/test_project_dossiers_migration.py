"""Tests de la migración project_dossiers (caché global del dossier). Spec §4."""
import os
import sqlite3

import pytest


def _fresh_db(tmp_path) -> sqlite3.Connection:
    db_path = tmp_path / "project_dossiers_test.db"
    os.environ["MIGRATE_QTY_ALLOW_BULK_QUARANTINE"] = "1"
    try:
        import btc_api
        original = btc_api.DB_FILE
        btc_api.DB_FILE = str(db_path)
        try:
            from db.schema import init_db
            init_db()
            con = sqlite3.connect(str(db_path))
            con.isolation_level = None  # autocommit — los INSERTs de test se ven solos
            return con
        finally:
            btc_api.DB_FILE = original
    finally:
        os.environ.pop("MIGRATE_QTY_ALLOW_BULK_QUARANTINE", None)


@pytest.fixture
def fresh_db_con(tmp_path):
    con = _fresh_db(tmp_path)
    yield con
    con.close()


class TestMigracionProjectDossiers:
    def test_tabla_existe_y_es_global(self, fresh_db_con):
        con = fresh_db_con
        row = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='project_dossiers'"
        ).fetchone()
        assert row is not None
        cols = {r[1] for r in con.execute("PRAGMA table_info(project_dossiers)")}
        assert {"symbol", "dossier_json", "generated_at"} <= cols
        assert "tenant_id" not in cols   # global por diseño

    def test_upsert_por_symbol(self, fresh_db_con):
        con = fresh_db_con
        con.execute("INSERT INTO project_dossiers(symbol, dossier_json, generated_at) "
                    "VALUES ('ADAUSDT', '{}', '2026-06-12T00:00:00+00:00')")
        con.execute("INSERT OR REPLACE INTO project_dossiers(symbol, dossier_json, generated_at) "
                    "VALUES ('ADAUSDT', '{\"x\":1}', '2026-06-12T01:00:00+00:00')")
        n = con.execute("SELECT COUNT(*) FROM project_dossiers WHERE symbol='ADAUSDT'").fetchone()[0]
        assert n == 1   # replace, no duplica

    def test_migracion_idempotente(self, tmp_path):
        from db.schema import init_db
        import btc_api
        db_path = tmp_path / "idempotent_test.db"
        os.environ["MIGRATE_QTY_ALLOW_BULK_QUARANTINE"] = "1"
        original = btc_api.DB_FILE
        try:
            btc_api.DB_FILE = str(db_path)
            init_db()
            init_db()
        finally:
            btc_api.DB_FILE = original
            os.environ.pop("MIGRATE_QTY_ALLOW_BULK_QUARANTINE", None)
