"""Tests de la migración lifecycle_states (instrumento F3a).

Verifica que init_db() crea la tabla con las columnas correctas (incluido
close_reason), que el CHECK de estado_vivo rechaza valores inválidos, y que
una doble llamada a init_db() es idempotente. Patrón: test_conduct_episodes_migration.py.
"""
import os
import sqlite3

import pytest


def _fresh_db(tmp_path) -> sqlite3.Connection:
    db_path = tmp_path / "lifecycle_states_test.db"
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


class TestMigracionLifecycleStates:
    def test_tabla_existe_con_columnas_esperadas(self, fresh_db_con):
        con = fresh_db_con
        row = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='lifecycle_states'"
        ).fetchone()
        assert row is not None, "lifecycle_states table must exist after init_db()"
        cols = {r[1] for r in con.execute("PRAGMA table_info(lifecycle_states)")}
        expected = {
            "id", "position_id", "symbol", "tenant_id", "estado_vivo",
            "plan_json", "entry_price", "qty_original", "fase",
            "rungs_llenos_json", "consumed_orders_json", "sl_actual",
            "be_movido", "size_restante_frac", "close_reason",
            "events_json", "prev_observed_json", "prev_qty",
            "confirmed_at", "updated_at",
        }
        assert expected <= cols

    def test_estado_vivo_check_rechaza_valor_invalido(self, fresh_db_con):
        con = fresh_db_con
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                """INSERT INTO lifecycle_states
                   (symbol, tenant_id, estado_vivo, plan_json, entry_price,
                    fase, rungs_llenos_json, consumed_orders_json, be_movido,
                    confirmed_at, updated_at)
                   VALUES ('BTCUSDT', 1, 'fantasma', '{}', 100.0,
                           'CONFIRMED', '[]', '[]', 0,
                           '2026-06-13T00:00:00+00:00', '2026-06-13T00:00:00+00:00')"""
            )

    def test_estado_vivo_activo_aceptado(self, fresh_db_con):
        con = fresh_db_con
        con.execute(
            """INSERT INTO lifecycle_states
               (symbol, tenant_id, estado_vivo, plan_json, entry_price,
                fase, rungs_llenos_json, consumed_orders_json, be_movido,
                confirmed_at, updated_at)
               VALUES ('BTCUSDT', 1, 'activo', '{}', 100.0,
                       'CONFIRMED', '[]', '[]', 0,
                       '2026-06-13T00:00:00+00:00', '2026-06-13T00:00:00+00:00')"""
        )
        n = con.execute("SELECT COUNT(*) FROM lifecycle_states").fetchone()[0]
        assert n == 1

    def test_migracion_idempotente(self, tmp_path):
        from db.schema import init_db
        import btc_api
        db_path = tmp_path / "idempotent_lifecycle_test.db"
        os.environ["MIGRATE_QTY_ALLOW_BULK_QUARANTINE"] = "1"
        original = btc_api.DB_FILE
        try:
            btc_api.DB_FILE = str(db_path)
            init_db()
            init_db()  # segunda ejecución — debe ser idempotente
        finally:
            btc_api.DB_FILE = original
            os.environ.pop("MIGRATE_QTY_ALLOW_BULK_QUARANTINE", None)
