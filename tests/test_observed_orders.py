"""Tests de Binance v0.3 — SL/TP observados (spec 2026-06-11).

Cubre: migración observed_orders, clasificación pura, snapshot + resumen.
"""
from __future__ import annotations

import os
import sqlite3

import pytest

from binance_sync import classify_open_orders, apply_observed_orders


def _fresh_db(tmp_path) -> sqlite3.Connection:
    db_path = tmp_path / "observed_orders_test.db"
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


class TestMigracionObservedOrders:
    def test_tabla_existe_con_checks(self, fresh_db_con):
        con = fresh_db_con
        row = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='observed_orders'"
        ).fetchone()
        assert row is not None

        # CHECK kind: solo SL/TP
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO observed_orders "
                "(tenant_id, symbol, kind, price, qty, order_id, observed_at) "
                "VALUES (1, 'BTCUSDT', 'XX', 100, 1, 1, '2026-06-11T00:00:00')"
            )

        # CHECK price > 0
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO observed_orders "
                "(tenant_id, symbol, kind, price, qty, order_id, observed_at) "
                "VALUES (1, 'BTCUSDT', 'SL', 0, 1, 2, '2026-06-11T00:00:00')"
            )

        # CHECK qty > 0
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO observed_orders "
                "(tenant_id, symbol, kind, price, qty, order_id, observed_at) "
                "VALUES (1, 'BTCUSDT', 'SL', 50000, 0, 3, '2026-06-11T00:00:00')"
            )

        # UNIQUE (tenant_id, order_id)
        con.execute(
            "INSERT INTO observed_orders "
            "(tenant_id, symbol, kind, price, qty, order_id, observed_at) "
            "VALUES (1, 'BTCUSDT', 'SL', 50000, 0.5, 7, '2026-06-11T00:00:00')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO observed_orders "
                "(tenant_id, symbol, kind, price, qty, order_id, observed_at) "
                "VALUES (1, 'ETHUSDT', 'TP', 3000, 1, 7, '2026-06-11T00:00:00')"
            )

    def test_insert_valido(self, fresh_db_con):
        """Fila válida se inserta sin error."""
        con = fresh_db_con
        con.execute(
            "INSERT INTO observed_orders "
            "(tenant_id, symbol, kind, price, qty, order_id, observed_at) "
            "VALUES (1, 'BTCUSDT', 'SL', 50000.0, 0.1, 100, '2026-06-11T12:00:00')"
        )
        n = con.execute("SELECT COUNT(*) FROM observed_orders WHERE order_id=100").fetchone()[0]
        assert n == 1

        row = con.execute(
            "SELECT pct_holding, oco_group FROM observed_orders WHERE order_id=100"
        ).fetchone()
        assert row[0] is None  # pct_holding — NULL es estado semántico (spec §4: "se abstiene, no inventa")
        assert row[1] is None  # oco_group — NULL es estado semántico

    def test_migracion_idempotente(self, tmp_path):
        """Correr init_db dos veces no debe fallar (CREATE IF NOT EXISTS)."""
        db_path = tmp_path / "idempotent_test.db"
        os.environ["MIGRATE_QTY_ALLOW_BULK_QUARANTINE"] = "1"
        try:
            import btc_api
            original = btc_api.DB_FILE
            btc_api.DB_FILE = str(db_path)
            try:
                from db.schema import init_db
                init_db()
                init_db()
            finally:
                btc_api.DB_FILE = original
        finally:
            os.environ.pop("MIGRATE_QTY_ALLOW_BULK_QUARANTINE", None)


def _orden(**kw):
    base = {"symbol": "BTCUSDT", "orderId": 1, "orderListId": -1,
            "side": "SELL", "type": "STOP_LOSS_LIMIT",
            "price": "49000", "stopPrice": "50000",
            "origQty": "0.5", "executedQty": "0"}
    base.update(kw)
    return base


class TestClassifyOpenOrders:
    def test_oco_completo_dos_patas_mismo_grupo(self):
        orders = [
            _orden(orderId=1, orderListId=33, type="STOP_LOSS_LIMIT",
                   stopPrice="50000", price="49900"),
            _orden(orderId=2, orderListId=33, type="LIMIT_MAKER",
                   price="75000", stopPrice="0"),
        ]
        out = classify_open_orders(orders, {"BTC": 2.0})
        assert len(out) == 2
        sl = next(o for o in out if o["kind"] == "SL")
        tp = next(o for o in out if o["kind"] == "TP")
        assert sl["price"] == 50000.0          # SL usa stopPrice
        assert tp["price"] == 75000.0          # LIMIT_MAKER usa price
        assert sl["oco_group"] == tp["oco_group"] == 33
        assert sl["pct_holding"] == pytest.approx(0.25)   # 0.5 de 2.0

    def test_take_profit_limit_usa_stop_price(self):
        out = classify_open_orders(
            [_orden(type="TAKE_PROFIT_LIMIT", stopPrice="80000", price="79900")],
            {"BTC": 1.0})
        assert out[0]["kind"] == "TP"
        assert out[0]["price"] == 80000.0

    def test_limit_venta_simple_es_tp(self):
        out = classify_open_orders(
            [_orden(type="LIMIT", price="70000")], {"BTC": 1.0})
        assert out[0]["kind"] == "TP"
        assert out[0]["price"] == 70000.0
        assert out[0]["oco_group"] is None     # orderListId=-1 → suelta

    def test_buy_se_ignora(self):
        assert classify_open_orders([_orden(side="BUY")], {"BTC": 1.0}) == []

    def test_qty_restante_descuenta_ejecutado(self):
        out = classify_open_orders(
            [_orden(origQty="1.0", executedQty="0.4")], {"BTC": 2.0})
        assert out[0]["qty"] == pytest.approx(0.6)
        assert out[0]["pct_holding"] == pytest.approx(0.3)

    def test_orden_mayor_que_holding_pct_sin_clamp(self):
        out = classify_open_orders([_orden(origQty="3.0")], {"BTC": 2.0})
        assert out[0]["pct_holding"] == pytest.approx(1.5)   # hecho observado

    def test_holding_desconocido_pct_null(self):
        out = classify_open_orders([_orden(symbol="PEPEUSDT")], {"BTC": 2.0})
        assert out[0]["pct_holding"] is None   # se abstiene, no inventa

    def test_orden_completamente_ejecutada_se_omite(self):
        assert classify_open_orders(
            [_orden(origQty="0.5", executedQty="0.5")], {"BTC": 2.0}) == []


@pytest.fixture
def con_with_positions(tmp_path):
    con = _fresh_db(tmp_path)
    con.row_factory = sqlite3.Row
    # tenant 1, BTCUSDT, EXTERNAL — con sl_price/tp_price tecleados (para probar la sobreescritura).
    con.execute(
        "INSERT INTO positions "
        "(symbol, direction, status, entry_price, entry_ts, sl_price, tp_price, "
        "qty, tenant_id, control_domain) "
        "VALUES ('BTCUSDT','LONG','open',60000,'2026-06-11T00:00:00',45000,80000,"
        "0.5,1,'EXTERNAL')"
    )
    # tenant 1, ETHUSDT, INTERNAL — jamás debe tocarse.
    con.execute(
        "INSERT INTO positions "
        "(symbol, direction, status, entry_price, entry_ts, sl_price, tp_price, "
        "qty, tenant_id, control_domain) "
        "VALUES ('ETHUSDT','LONG','open',3000,'2026-06-11T00:00:00',2500,4000,"
        "1.0,1,'INTERNAL')"
    )
    yield con
    con.close()


_OBSERVED_AT = "2026-06-11T12:00:00+00:00"


def _clasificada(**kw):
    base = {"symbol": "BTCUSDT", "kind": "SL", "price": 50000.0, "qty": 0.5,
            "pct_holding": 0.25, "order_id": 1, "oco_group": None}
    base.update(kw)
    return base


class TestApplyObservedOrders:
    def test_snapshot_inserta_y_resume_en_fila_external(self, con_with_positions):
        con = con_with_positions
        apply_observed_orders(con, tenant_id=1, classified=[
            _clasificada(order_id=1, kind="SL", price=50000.0, qty=0.5),
            _clasificada(order_id=2, kind="TP", price=75000.0, qty=0.5),
        ], observed_at=_OBSERVED_AT)
        rows = con.execute("SELECT * FROM observed_orders WHERE tenant_id=1").fetchall()
        assert len(rows) == 2
        pos = con.execute(
            "SELECT sl_price, tp_price FROM positions "
            "WHERE tenant_id=1 AND symbol='BTCUSDT'").fetchone()
        assert pos["sl_price"] == 50000.0
        assert pos["tp_price"] == 75000.0

    def test_resumen_toma_orden_de_mayor_qty(self, con_with_positions):
        con = con_with_positions
        apply_observed_orders(con, tenant_id=1, classified=[
            _clasificada(order_id=1, kind="SL", price=48000.0, qty=0.2),
            _clasificada(order_id=2, kind="SL", price=50000.0, qty=1.0),
        ], observed_at=_OBSERVED_AT)
        pos = con.execute(
            "SELECT sl_price FROM positions WHERE tenant_id=1 AND symbol='BTCUSDT'"
        ).fetchone()
        assert pos["sl_price"] == 50000.0      # la de qty 1.0 gana

    def test_sin_orden_limpia_a_null(self, con_with_positions):
        con = con_with_positions   # la fila arranca con sl_price/tp_price tecleados
        apply_observed_orders(con, tenant_id=1, classified=[], observed_at=_OBSERVED_AT)
        pos = con.execute(
            "SELECT sl_price, tp_price FROM positions "
            "WHERE tenant_id=1 AND symbol='BTCUSDT'").fetchone()
        assert pos["sl_price"] is None         # fuente de verdad: sin orden = sin SL
        assert pos["tp_price"] is None

    def test_idempotente_dos_corridas_mismo_estado(self, con_with_positions):
        con = con_with_positions
        plan = [_clasificada(order_id=1)]
        apply_observed_orders(con, tenant_id=1, classified=plan, observed_at=_OBSERVED_AT)
        apply_observed_orders(con, tenant_id=1, classified=plan, observed_at=_OBSERVED_AT)
        rows = con.execute("SELECT * FROM observed_orders WHERE tenant_id=1").fetchall()
        assert len(rows) == 1                  # delete+insert: no duplica

    def test_aislamiento_per_tenant(self, con_with_positions):
        con = con_with_positions
        con.execute(
            "INSERT INTO observed_orders "
            "(tenant_id, symbol, kind, price, qty, order_id, observed_at) "
            "VALUES (2, 'ETHUSDT', 'SL', 3000, 1, 99, ?)", (_OBSERVED_AT,))
        apply_observed_orders(con, tenant_id=1, classified=[], observed_at=_OBSERVED_AT)
        t2 = con.execute("SELECT * FROM observed_orders WHERE tenant_id=2").fetchall()
        assert len(t2) == 1                    # el snapshot de 1 no toca a 2

    def test_fila_internal_jamas_tocada(self, con_with_positions):
        con = con_with_positions
        before = con.execute(
            "SELECT sl_price, tp_price FROM positions "
            "WHERE tenant_id=1 AND control_domain='INTERNAL'").fetchone()
        apply_observed_orders(con, tenant_id=1, classified=[], observed_at=_OBSERVED_AT)
        after = con.execute(
            "SELECT sl_price, tp_price FROM positions "
            "WHERE tenant_id=1 AND control_domain='INTERNAL'").fetchone()
        assert dict(before) == dict(after)


class TestDbGetObservedOrders:
    def test_devuelve_solo_el_tenant_ordenado(self, con_with_positions):
        from db.observed_orders import db_get_observed_orders
        con = con_with_positions
        for t, sym, kind, oid in [(1, "BTCUSDT", "SL", 1), (1, "BTCUSDT", "TP", 2),
                                  (2, "ETHUSDT", "SL", 3)]:
            con.execute(
                "INSERT INTO observed_orders "
                "(tenant_id, symbol, kind, price, qty, order_id, observed_at) "
                "VALUES (?, ?, ?, 100, 1, ?, '2026-06-11T00:00:00')",
                (t, sym, kind, oid))
        out = db_get_observed_orders(con, tenant_id=1)
        assert len(out) == 2
        assert all(o["symbol"] == "BTCUSDT" for o in out)
        assert isinstance(out[0], dict)
