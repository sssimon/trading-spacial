"""Tests de la migración conduct_episodes (instrumento, Fase 1).

Verifica que init_db() crea la tabla con las columnas correctas y que el
CHECK de procedencia rechaza valores inválidos. Patrón: test_project_dossiers_migration.py.
"""
import os
import sqlite3

import pytest


def _fresh_db(tmp_path) -> sqlite3.Connection:
    db_path = tmp_path / "conduct_episodes_test.db"
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


class TestMigracionConductEpisodes:
    def test_tabla_existe_con_columnas_esperadas(self, fresh_db_con):
        con = fresh_db_con
        row = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='conduct_episodes'"
        ).fetchone()
        assert row is not None, "conduct_episodes table must exist after init_db()"
        cols = {r[1] for r in con.execute("PRAGMA table_info(conduct_episodes)")}
        expected = {
            "id", "position_id", "symbol", "tenant_id", "entry_ts", "exit_ts",
            "procedencia", "entry_en_zona", "sl_respetado", "adherencia_be",
            "rungs_honrados", "cierre_en_plan", "hold_hours", "close_reason",
            "plan_json", "reproduced", "created_ts",
        }
        assert expected <= cols

    def test_procedencia_check_rechaza_valor_invalido(self, fresh_db_con):
        con = fresh_db_con
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                """INSERT INTO conduct_episodes
                   (position_id, symbol, tenant_id, entry_ts, procedencia, reproduced, created_ts)
                   VALUES (1, 'BTCUSDT', 2, '2026-01-01T00:00:00+00:00', 'inventada', 0, '2026-06-12T00:00:00+00:00')"""
            )

    def test_procedencia_observado_aceptado(self, fresh_db_con):
        con = fresh_db_con
        con.execute(
            """INSERT INTO conduct_episodes
               (position_id, symbol, tenant_id, entry_ts, procedencia, reproduced, created_ts)
               VALUES (1, 'BTCUSDT', 2, '2026-01-01T00:00:00+00:00', 'observado', 0, '2026-06-12T00:00:00+00:00')"""
        )
        n = con.execute("SELECT COUNT(*) FROM conduct_episodes").fetchone()[0]
        assert n == 1

    def test_migracion_idempotente(self, tmp_path):
        from db.schema import init_db
        import btc_api
        db_path = tmp_path / "idempotent_conduct_test.db"
        os.environ["MIGRATE_QTY_ALLOW_BULK_QUARANTINE"] = "1"
        original = btc_api.DB_FILE
        try:
            btc_api.DB_FILE = str(db_path)
            init_db()
            init_db()  # segunda ejecución — debe ser idempotente
        finally:
            btc_api.DB_FILE = original
            os.environ.pop("MIGRATE_QTY_ALLOW_BULK_QUARANTINE", None)
