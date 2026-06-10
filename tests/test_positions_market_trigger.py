import sqlite3
import pytest


@pytest.fixture
def con():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    # Tabla positions mínima suficiente para el trigger + índice.
    c.execute(
        "CREATE TABLE positions (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "scan_id INTEGER, symbol TEXT NOT NULL, direction TEXT NOT NULL DEFAULT 'LONG', "
        "status TEXT NOT NULL DEFAULT 'open', entry_price REAL NOT NULL, entry_ts TEXT NOT NULL, "
        "qty REAL, tenant_id INTEGER, control_domain TEXT NOT NULL DEFAULT 'INTERNAL', market TEXT)"
    )
    from db.schema import _install_binance_external_guards
    _install_binance_external_guards(c)
    return c


def _ins(con, **kw):
    cols = ", ".join(kw); ph = ", ".join("?" for _ in kw)
    con.execute(f"INSERT INTO positions ({cols}) VALUES ({ph})", tuple(kw.values()))


def test_market_set_with_internal_is_rejected(con):
    with pytest.raises(sqlite3.IntegrityError):
        _ins(con, symbol="BTCUSDT", entry_price=1, entry_ts="t", tenant_id=2,
             control_domain="INTERNAL", market="SPOT")


def test_market_set_with_external_is_allowed(con):
    _ins(con, symbol="BTCUSDT", entry_price=1, entry_ts="t", tenant_id=2,
         control_domain="EXTERNAL", market="SPOT")
    assert con.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 1


def test_market_null_internal_is_allowed(con):
    # Las filas INTERNAL normales (market NULL) pasan sin problema.
    _ins(con, symbol="BTCUSDT", entry_price=1, entry_ts="t", tenant_id=2,
         control_domain="INTERNAL")
    assert con.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 1


def test_update_to_internal_with_market_is_rejected(con):
    _ins(con, symbol="BTCUSDT", entry_price=1, entry_ts="t", tenant_id=2,
         control_domain="EXTERNAL", market="SPOT")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE positions SET control_domain='INTERNAL' WHERE symbol='BTCUSDT'")


def test_idempotency_index_blocks_duplicate_external(con):
    _ins(con, symbol="BTCUSDT", entry_price=1, entry_ts="t1", tenant_id=2,
         direction="LONG", control_domain="EXTERNAL", market="SPOT")
    with pytest.raises(sqlite3.IntegrityError):
        _ins(con, symbol="BTCUSDT", entry_price=2, entry_ts="t2", tenant_id=2,
             direction="LONG", control_domain="EXTERNAL", market="SPOT")
