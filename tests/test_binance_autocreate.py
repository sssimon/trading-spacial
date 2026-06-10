"""Auto-creación de holdings AUTO_DERIVED desde el trade history (Task 5).

Spec: 2026-06-10-binance-v02-autocreacion-observabilidad-spec.md §4, §5.

Descubre holds (/account) → pares con las 4 quotes → myTrades → ACB → filtro
minNotional → crea fila AUTO_DERIVED (market='SPOT') SOLO si no existe fila
EXTERNAL para (tenant,symbol,market,direction). NO pisa OPERATOR (F4). Usa el
BALANCE como qty (no qty_viva — transfers a Earn, Adrian #3). Abstiene:
no_reconstruible (sin trades), flat, dust (<minNotional), ingest_incompleto (ban).
Excluye Earn (LD*) y quotes.
"""
from __future__ import annotations

import os
import sqlite3

import pytest


def _fresh_db(tmp_path) -> sqlite3.Connection:
    db_path = tmp_path / "autocreate.db"
    os.environ["MIGRATE_QTY_ALLOW_BULK_QUARANTINE"] = "1"
    try:
        import btc_api
        original = btc_api.DB_FILE
        btc_api.DB_FILE = str(db_path)
        try:
            from db.schema import init_db
            init_db()
            con = sqlite3.connect(str(db_path))
            con.row_factory = sqlite3.Row
            return con
        finally:
            btc_api.DB_FILE = original
    finally:
        os.environ.pop("MIGRATE_QTY_ALLOW_BULK_QUARANTINE", None)


def _fill(*, id, time, is_buyer, qty, quote_qty):
    return {"id": id, "time": time, "isBuyer": is_buyer, "qty": str(qty),
            "quoteQty": str(quote_qty), "commission": "0", "commissionAsset": "USDT"}


class FakeClient:
    def __init__(self, *, trades=None, filters=None, prices=None, ban_on=None):
        self._trades = trades or {}
        self._filters = filters or {}
        self._prices = prices or {}
        self._ban_on = set(ban_on or [])

    def get_my_trades(self, symbol):
        if symbol in self._ban_on:
            from data.providers.binance_account import BinanceRateBanned
            raise BinanceRateBanned("banned")
        return self._trades.get(symbol, [])

    def get_exchange_filters(self, symbols):
        return {s: self._filters[s] for s in symbols if s in self._filters}

    def get_ticker_prices(self, symbols):
        return {s: self._prices[s] for s in symbols if s in self._prices}


def _run(con, *, balances, client):
    from binance_sync import autocreate_spot_holdings
    out = autocreate_spot_holdings(con, tenant_id=2, client=client, balances=balances)
    con.commit()
    return out


def _row(con, symbol):
    r = con.execute("SELECT * FROM positions WHERE symbol=? AND tenant_id=2", (symbol,)).fetchone()
    return dict(r) if r else None


def test_creates_auto_derived_for_new_hold(tmp_path):
    con = _fresh_db(tmp_path)
    try:
        client = FakeClient(
            trades={"BNBUSDT": [_fill(id=1, time=1000, is_buyer=True, qty=2, quote_qty=1000)]},
            filters={"BNBUSDT": {"min_notional": 10.0, "min_qty": 0.001}},
            prices={"BNBUSDT": 600.0},
        )
        out = _run(con, balances={"BNB": 2.0}, client=client)
        row = _row(con, "BNBUSDT")
    finally:
        con.close()
    assert "BNBUSDT" in out["created"]
    assert row["origin"] == "AUTO_DERIVED"
    assert row["market"] == "SPOT"
    assert row["control_domain"] == "EXTERNAL"
    assert row["qty"] == pytest.approx(2.0)
    assert row["entry_price"] == pytest.approx(500.0)  # ACB 1000/2


def test_uses_balance_as_qty_not_qty_viva(tmp_path):
    """Adrian #3: la qty es el BALANCE spot (no qty_viva de fills). Si parte se
    movió a Earn, los fills suman más que el balance, pero la fila usa el balance."""
    con = _fresh_db(tmp_path)
    try:
        # fills suman 3 comprados; balance spot real = 2 (1 movido a Earn).
        client = FakeClient(
            trades={"BNBUSDT": [_fill(id=1, time=1000, is_buyer=True, qty=3, quote_qty=1500)]},
            filters={"BNBUSDT": {"min_notional": 10.0}},
            prices={"BNBUSDT": 600.0},
        )
        _run(con, balances={"BNB": 2.0}, client=client)
        row = _row(con, "BNBUSDT")
    finally:
        con.close()
    assert row["qty"] == pytest.approx(2.0)            # balance, no 3
    assert row["entry_price"] == pytest.approx(500.0)  # ACB (costo unitario) sí de fills


def test_skips_existing_operator_row_unchanged(tmp_path):
    """No pisa una fila OPERATOR (manual del papá): la deja intacta y en conducta (F4)."""
    con = _fresh_db(tmp_path)
    try:
        con.execute(
            "INSERT INTO positions (scan_id,symbol,direction,status,entry_price,entry_ts,"
            "qty,tenant_id,control_domain,market,origin) VALUES "
            "(NULL,'BTCUSDT','LONG','open',64390,'2026-06-04T00:00:00+00:00',0.02,2,'EXTERNAL','SPOT','OPERATOR')"
        )
        con.commit()
        client = FakeClient(
            trades={"BTCUSDT": [_fill(id=1, time=1000, is_buyer=True, qty=0.02, quote_qty=2000)]},
            filters={"BTCUSDT": {"min_notional": 10.0}},
            prices={"BTCUSDT": 100000.0},
        )
        out = _run(con, balances={"BTC": 0.02}, client=client)
        row = _row(con, "BTCUSDT")
    finally:
        con.close()
    assert "BTCUSDT" not in out["created"]
    assert row["origin"] == "OPERATOR"          # intacta
    assert row["entry_price"] == pytest.approx(64390.0)  # entry tecleado NO sobreescrito


def test_abstains_no_reconstruible_when_no_trades(tmp_path):
    con = _fresh_db(tmp_path)
    try:
        client = FakeClient(trades={})  # sin trades en ninguna quote
        out = _run(con, balances={"WIF": 1000.0}, client=client)
    finally:
        con.close()
    assert out["created"] == []
    assert out["abstained"].get("WIF") == "no_reconstruible"


def test_skips_dust_below_min_notional(tmp_path):
    con = _fresh_db(tmp_path)
    try:
        client = FakeClient(
            trades={"ETHUSDT": [_fill(id=1, time=1000, is_buyer=True, qty=0.0000088, quote_qty=0.03)]},
            filters={"ETHUSDT": {"min_notional": 10.0}},
            prices={"ETHUSDT": 3000.0},  # 0.0000088 * 3000 = 0.026 < 10
        )
        out = _run(con, balances={"ETH": 0.0000088}, client=client)
        eth_row = _row(con, "ETHUSDT")
    finally:
        con.close()
    assert eth_row is None
    assert out["abstained"].get("ETHUSDT") == "dust"


def test_excludes_earn_ld_assets(tmp_path):
    con = _fresh_db(tmp_path)
    try:
        client = FakeClient()
        out = _run(con, balances={"LDETH": 0.5, "LDDOGE": 100.0}, client=client)
    finally:
        con.close()
    assert out["created"] == []
    # LD* ni siquiera se consideran candidatos (Earn diferido)
    assert "LDETH" not in out["abstained"]
    assert "LDDOGE" not in out["abstained"]


def test_ban_marks_ingest_incompleto(tmp_path):
    con = _fresh_db(tmp_path)
    try:
        client = FakeClient(ban_on={"BNBUSDT", "BNBUSDC", "BNBBUSD", "BNBFDUSD"})
        out = _run(con, balances={"BNB": 2.0}, client=client)
    finally:
        con.close()
    assert out["created"] == []
    assert out["abstained"].get("BNB") == "ingest_incompleto"


def test_idempotent_second_run_no_duplicate(tmp_path):
    con = _fresh_db(tmp_path)
    try:
        client = FakeClient(
            trades={"BNBUSDT": [_fill(id=1, time=1000, is_buyer=True, qty=2, quote_qty=1000)]},
            filters={"BNBUSDT": {"min_notional": 10.0}},
            prices={"BNBUSDT": 600.0},
        )
        _run(con, balances={"BNB": 2.0}, client=client)
        out2 = _run(con, balances={"BNB": 2.0}, client=client)  # segunda corrida
        n = con.execute("SELECT COUNT(*) FROM positions WHERE symbol='BNBUSDT'").fetchone()[0]
    finally:
        con.close()
    assert n == 1, "re-correr no debe duplicar"
    assert "BNBUSDT" not in out2["created"]
