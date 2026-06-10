# tests/test_binance_sync.py
import sqlite3
import pytest
from cryptography.fernet import Fernet


@pytest.fixture
def con(monkeypatch):
    monkeypatch.setenv("TRADING_BINANCE_MASTER_KEY", Fernet.generate_key().decode())
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE positions (id INTEGER PRIMARY KEY AUTOINCREMENT, scan_id INTEGER, "
        "symbol TEXT NOT NULL, direction TEXT NOT NULL DEFAULT 'LONG', status TEXT NOT NULL DEFAULT 'open', "
        "entry_price REAL NOT NULL, entry_ts TEXT NOT NULL, sl_price REAL, tp_price REAL, "
        "size_usd REAL, qty REAL, tenant_id INTEGER, control_domain TEXT NOT NULL DEFAULT 'INTERNAL', market TEXT)"
    )
    from db.schema import _install_binance_external_guards
    _install_binance_external_guards(c)
    # Fila EXTERNAL tecleada del papá (bootstrap, market NULL aún).
    c.execute(
        "INSERT INTO positions (symbol, direction, status, entry_price, entry_ts, qty, "
        "tenant_id, control_domain) VALUES ('BTCUSDT','LONG','open',64390,'t',0.01967,2,'EXTERNAL')"
    )
    return c


def test_base_asset_strips_quote():
    from binance_sync import base_asset
    assert base_asset("BTCUSDT") == "BTC"
    assert base_asset("ETHUSDC") == "ETH"


def test_reconcile_updates_qty_and_adopts_market(con):
    from binance_sync import reconcile_spot
    report = reconcile_spot(con, tenant_id=2, balances={"BTC": 0.02100}, dust=1e-6)
    row = con.execute("SELECT qty, market FROM positions WHERE symbol='BTCUSDT'").fetchone()
    assert abs(row["qty"] - 0.02100) < 1e-9   # qty autoridad-Binance
    assert row["market"] == "SPOT"            # bootstrap adoption
    assert report["reconciled"] == ["BTCUSDT"]


def test_closed_position_qty_goes_to_zero_and_is_flagged(con):
    from binance_sync import reconcile_spot
    report = reconcile_spot(con, tenant_id=2, balances={}, dust=1e-6)  # papá cerró en Binance
    row = con.execute("SELECT qty FROM positions WHERE symbol='BTCUSDT'").fetchone()
    assert row["qty"] == 0.0
    assert "BTCUSDT" in report["closed_pending"]


def test_untracked_hold_is_reported_not_inserted(con):
    from binance_sync import reconcile_spot
    report = reconcile_spot(con, tenant_id=2, balances={"BTC": 0.02, "SOL": 5.0}, dust=1e-6)
    n = con.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    assert n == 1  # SOL NO se inserta (no hay cost-basis)
    assert "SOLUSDT" in report["untracked"]
