"""Fail-closed contract of sync_tenant (Task 8 refactor: I/O fuera del writer-lock).

sync_tenant ahora maneja sus PROPIAS conexiones (vía btc_api.DB_FILE): FASE 1
lecturas (snapshot) + I/O de red, ambas SIN writer-lock; FASE 2 writes en una tx
corta. Halberg (revisión holística): no sostener BEGIN IMMEDIATE durante la red.

Los tests apuntan btc_api.DB_FILE a una DB de archivo con schema + credencial, y
mockean BinanceAccountClient. Debe: saltar credenciales no-ACTIVE, mapear errores
de cliente al estado de la credencial, y tratar un blip de transporte como
transitorio (TRANSPORT_ERROR, credencial SIGUE ACTIVE).
"""
import sqlite3
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_BINANCE_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("MIGRATE_QTY_ALLOW_BULK_QUARANTINE", "1")
    p = tmp_path / "sync.db"
    import btc_api
    monkeypatch.setattr(btc_api, "DB_FILE", str(p), raising=False)
    from db.schema import init_db
    init_db()
    return str(p)


def _add_cred(db_path, status="ACTIVE"):
    from db.binance_credentials import db_upsert_binance_credential, db_set_credential_status
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        db_upsert_binance_credential(con, tenant_id=2, api_key_public="PUB", secret_plaintext="s")
        if status != "ACTIVE":
            db_set_credential_status(con, tenant_id=2, status=status)
        con.commit()
    finally:
        con.close()


def _status(db_path):
    con = sqlite3.connect(db_path)
    try:
        return con.execute("SELECT status FROM binance_credentials WHERE tenant_id=2").fetchone()[0]
    finally:
        con.close()


def test_no_credential(db_path):
    from tools.sync_binance_spot import sync_tenant
    assert sync_tenant(2)["status"] == "NO_CREDENTIAL"


def test_skips_non_active_credential(db_path):
    from tools.sync_binance_spot import sync_tenant
    _add_cred(db_path, status="REVOKED")
    out = sync_tenant(2)
    assert out["status"] == "REVOKED" and out.get("skipped") is True


def test_transport_error_is_transient_and_keeps_active(db_path):
    from tools.sync_binance_spot import sync_tenant
    from data.providers.binance_account import BinanceTransportError
    _add_cred(db_path)
    with patch("tools.sync_binance_spot.get_server_time_offset_ms", return_value=0), \
         patch("tools.sync_binance_spot.BinanceAccountClient") as Cli:
        Cli.return_value.get_spot_balances.side_effect = BinanceTransportError(
            "ConnectionError en GET /api/v3/account"
        )
        out = sync_tenant(2)
    assert out["status"] == "TRANSPORT_ERROR"
    assert out.get("transient") is True
    assert _status(db_path) == "ACTIVE"  # blip transitorio NO degrada la credencial


def test_auth_error_sets_status_auth_failed(db_path):
    from tools.sync_binance_spot import sync_tenant
    from data.providers.binance_account import BinanceAuthError
    _add_cred(db_path)
    with patch("tools.sync_binance_spot.get_server_time_offset_ms", return_value=0), \
         patch("tools.sync_binance_spot.BinanceAccountClient") as Cli:
        Cli.return_value.get_spot_balances.side_effect = BinanceAuthError("-2015")
        out = sync_tenant(2)
    assert out["status"] == "AUTH_FAILED"
    assert _status(db_path) == "AUTH_FAILED"  # persistido, fail-closed


# ---------------------------------------------------------------------------
# TestObservedOrdersEnSync — v0.3: captura SL/TP observados en sync_tenant
# ---------------------------------------------------------------------------

# Fixture: OCO SELL de BTCUSDT (dos patas: STOP_LOSS_LIMIT + LIMIT_MAKER).
_OCO_BTCUSDT = [
    {
        "symbol": "BTCUSDT", "side": "SELL", "type": "STOP_LOSS_LIMIT",
        "orderId": 101, "orderListId": 33,
        "stopPrice": "50000.0", "price": "49900.0",
        "origQty": "0.5", "executedQty": "0.0",
    },
    {
        "symbol": "BTCUSDT", "side": "SELL", "type": "LIMIT_MAKER",
        "orderId": 102, "orderListId": 33,
        "stopPrice": "0.0", "price": "75000.0",
        "origQty": "0.5", "executedQty": "0.0",
    },
]

_BALANCES_BTC = {"BTC": 2.0}


def _count_observed_orders(db_path, tenant_id=2):
    """Cuenta filas de observed_orders para un tenant."""
    con = sqlite3.connect(db_path)
    try:
        return con.execute(
            "SELECT COUNT(*) FROM observed_orders WHERE tenant_id=?", (tenant_id,)
        ).fetchone()[0]
    finally:
        con.close()


def _insert_snapshot(db_path, tenant_id=2):
    """Inserta un snapshot previo en observed_orders para simular estado anterior."""
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            """INSERT INTO observed_orders
                   (tenant_id, symbol, kind, price, qty, order_id, observed_at)
               VALUES (?,?,?,?,?,?,?)""",
            (tenant_id, "ETHUSDT", "SL", 3000.0, 1.0, 999, "2026-01-01T00:00:00+00:00"),
        )
        con.commit()
    finally:
        con.close()


def _insert_external_position(db_path, tenant_id=2, symbol="BTCUSDT", sl_price=50000.0):
    """Inserta una fila EXTERNAL open con sl_price para comprobar que no se limpia."""
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            """INSERT INTO positions
                   (scan_id, symbol, direction, status, entry_price, entry_ts,
                    sl_price, tp_price, size_usd, qty, tenant_id,
                    control_domain, market, origin)
               VALUES (NULL, ?, 'LONG', 'open', 60000.0, '2026-01-01T00:00:00+00:00',
                       ?, NULL, 30000.0, 0.5, ?, 'EXTERNAL', 'SPOT', 'OPERATOR')""",
            (symbol, sl_price, tenant_id),
        )
        con.commit()
    finally:
        con.close()


def _get_sl_price(db_path, tenant_id=2, symbol="BTCUSDT"):
    con = sqlite3.connect(db_path)
    try:
        row = con.execute(
            "SELECT sl_price FROM positions "
            "WHERE tenant_id=? AND symbol=? AND control_domain='EXTERNAL' AND status='open'",
            (tenant_id, symbol),
        ).fetchone()
        return row[0] if row else None
    finally:
        con.close()


class TestObservedOrdersEnSync:

    def test_sync_captura_y_persiste_observed_orders(self, db_path):
        """sync_tenant persiste las órdenes OCO y el report refleja el conteo."""
        from tools.sync_binance_spot import sync_tenant
        _add_cred(db_path)
        with patch("tools.sync_binance_spot.get_server_time_offset_ms", return_value=0), \
             patch("tools.sync_binance_spot.BinanceAccountClient") as Cli:
            Cli.return_value.get_spot_balances.return_value = _BALANCES_BTC
            Cli.return_value.get_open_orders.return_value = _OCO_BTCUSDT
            report = sync_tenant(2)
        assert report["observed_orders"]["observed"] == 2
        assert _count_observed_orders(db_path) == 2

    def test_fallo_de_open_orders_omite_paso_completo(self, db_path):
        """Un BinanceRateBanned en get_open_orders → paso SKIPPED.

        El snapshot previo de observed_orders queda INTACTO (F8: parcial
        incorrecto). sl_price de la fila EXTERNAL no se limpia. El resto del
        sync corre (report status=ACTIVE). La credencial NO se marca RATE_BANNED
        (fallo del paso, no de la credencial).
        """
        from tools.sync_binance_spot import sync_tenant
        from data.providers.binance_account import BinanceRateBanned
        _add_cred(db_path)
        # Pre-condición: snapshot previo + fila EXTERNAL con sl_price no-NULL.
        _insert_snapshot(db_path)
        _insert_external_position(db_path, sl_price=50000.0)
        with patch("tools.sync_binance_spot.get_server_time_offset_ms", return_value=0), \
             patch("tools.sync_binance_spot.BinanceAccountClient") as Cli:
            Cli.return_value.get_spot_balances.return_value = _BALANCES_BTC
            Cli.return_value.get_open_orders.side_effect = BinanceRateBanned("429")
            report = sync_tenant(2)
        # Paso omitido completo.
        assert report["observed_orders"] == "SKIPPED"
        # Snapshot previo intacto (F8: sin borrado parcial).
        assert _count_observed_orders(db_path) == 1
        # sl_price de la fila EXTERNAL no se limpió.
        assert _get_sl_price(db_path) == 50000.0
        # El resto del sync corrió.
        assert report["status"] == "ACTIVE"
        # Credencial sigue ACTIVE — fallo del paso ≠ fallo de credencial.
        assert _status(db_path) == "ACTIVE"

    def test_dry_run_no_persiste_observed_orders(self, db_path):
        """dry_run=True → el report muestra las órdenes pero la tabla queda vacía."""
        from tools.sync_binance_spot import sync_tenant
        _add_cred(db_path)
        with patch("tools.sync_binance_spot.get_server_time_offset_ms", return_value=0), \
             patch("tools.sync_binance_spot.BinanceAccountClient") as Cli:
            Cli.return_value.get_spot_balances.return_value = _BALANCES_BTC
            Cli.return_value.get_open_orders.return_value = _OCO_BTCUSDT
            report = sync_tenant(2, dry_run=True)
        # El report contiene las órdenes...
        assert report["observed_orders"]["observed"] == 2
        # ...pero la tabla queda vacía por el rollback de _DryRunAbort.
        assert _count_observed_orders(db_path) == 0
