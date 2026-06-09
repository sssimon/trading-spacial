"""Tests del equity en vivo (real_equity) — display-only, marca holds a precio actual.

equity_real = cash_balance + Σ(posiciones EXTERNAL abiertas: qty × precio_actual).
On-read, no toca capital.balance ni el portfolio_dd del kill-switch (las bolsas
del operador no le throttlean el sizing a sus trades de señal).

Spec: docs/superpowers/specs/es/2026-06-09-posiciones-externas-control-domain-spec.md (v0.1.5).
"""
from __future__ import annotations

import os
import sqlite3

import pytest


@pytest.fixture
def con(tmp_path):
    db_path = tmp_path / "eq.db"
    os.environ["MIGRATE_QTY_ALLOW_BULK_QUARANTINE"] = "1"
    try:
        import btc_api
        orig = btc_api.DB_FILE
        btc_api.DB_FILE = str(db_path)
        try:
            from db.schema import init_db
            init_db()
        finally:
            btc_api.DB_FILE = orig
    finally:
        os.environ.pop("MIGRATE_QTY_ALLOW_BULK_QUARANTINE", None)
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def test_capital_has_cash_balance_column(con):
    cols = {r[1]: r for r in con.execute("PRAGMA table_info(capital)").fetchall()}
    assert "cash_balance_usd" in cols
    _, _, ctype, notnull, dflt, _ = cols["cash_balance_usd"]
    assert ctype == "REAL"
    assert notnull == 1
    assert dflt in ("0", "0.0")


def test_set_and_get_cash_balance(con):
    from db.capital import db_set_cash_balance, db_get_capital
    db_set_cash_balance(con, 2, 81.63)
    con.commit()
    row = db_get_capital(con, 2)
    assert row is not None
    assert row["cash_balance_usd"] == pytest.approx(81.63)


def test_compute_real_equity_marks_external_holds(con):
    from db.capital import db_set_cash_balance
    from tools.register_external_position import register_external
    from api.equity import compute_real_equity

    db_set_cash_balance(con, 2, 81.63)
    register_external(con, tenant_id=2, symbol="BTCUSDT", direction="LONG",
                      qty=0.01967, entry_price=64390.0, entry_ts="2026-06-04T00:56:00+00:00")
    register_external(con, tenant_id=2, symbol="ETHUSDT", direction="LONG",
                      qty=0.448, entry_price=1700.0, entry_ts="2026-06-02T11:11:00+00:00")
    con.commit()

    prices = {"BTCUSDT": 61792.01, "ETHUSDT": 1652.28}
    r = compute_real_equity(con, tenant_id=2, price_lookup=prices)

    expected_holds = 0.01967 * 61792.01 + 0.448 * 1652.28
    assert r["cash_balance_usd"] == pytest.approx(81.63)
    assert r["holds_value_usd"] == pytest.approx(expected_holds)
    assert r["real_equity_usd"] == pytest.approx(81.63 + expected_holds)
    assert r["missing_prices"] == []
    assert len(r["holds"]) == 2


def test_compute_real_equity_excludes_internal_and_handles_missing_price(con):
    from db.capital import db_set_cash_balance
    from tools.register_external_position import register_external
    from api.equity import compute_real_equity

    db_set_cash_balance(con, 2, 100.0)
    # INTERNAL open position must NOT count in real_equity (leveraged ≠ spot value).
    con.execute(
        "INSERT INTO positions (symbol,direction,status,entry_price,entry_ts,qty,"
        "tenant_id,control_domain) VALUES ('SOLUSDT','LONG','open',100,'2026-06-01T00:00:00+00:00',5,2,'INTERNAL')"
    )
    # EXTERNAL with no price available → listed in missing_prices, not summed.
    register_external(con, tenant_id=2, symbol="XRPUSDT", direction="LONG",
                      qty=10.0, entry_price=2.0, entry_ts="2026-06-03T00:00:00+00:00")
    con.commit()

    r = compute_real_equity(con, tenant_id=2, price_lookup={})  # no prices
    assert r["holds_value_usd"] == 0.0
    assert r["missing_prices"] == ["XRPUSDT"]
    assert r["real_equity_usd"] == pytest.approx(100.0)
    # SOL (INTERNAL) absent from holds entirely.
    assert all(h["symbol"] != "SOLUSDT" for h in r["holds"])
