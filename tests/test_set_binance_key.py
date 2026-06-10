# tests/test_set_binance_key.py
import sqlite3
import pytest
from unittest.mock import MagicMock
from cryptography.fernet import Fernet


@pytest.fixture
def con(monkeypatch):
    monkeypatch.setenv("TRADING_BINANCE_MASTER_KEY", Fernet.generate_key().decode())
    c = sqlite3.connect(":memory:"); c.row_factory = sqlite3.Row
    from db.schema import _migrate_binance_credentials
    _migrate_binance_credentials(c)
    return c


def test_onboard_rejects_key_with_trading_enabled(con):
    from tools.set_binance_key import onboard_credential
    client = MagicMock()
    client.probe_trading_disabled.return_value = False  # trading ENABLED → rechazar
    client.get_spot_balances.return_value = {"BTC": 0.5}
    with pytest.raises(ValueError, match="trading"):
        onboard_credential(con, tenant_id=2, api_key="P", secret="S",
                           client=client, ip_whitelisted=True)
    assert con.execute("SELECT COUNT(*) FROM binance_credentials").fetchone()[0] == 0


def test_onboard_stores_when_read_only_and_ip_whitelisted(con):
    from tools.set_binance_key import onboard_credential
    from db.binance_credentials import db_get_decrypted_secret
    client = MagicMock()
    client.probe_trading_disabled.return_value = True   # read-only → OK
    client.get_spot_balances.return_value = {"BTC": 0.5}
    onboard_credential(con, tenant_id=2, api_key="PUB", secret="SECRET",
                       client=client, ip_whitelisted=True)
    assert db_get_decrypted_secret(con, tenant_id=2) == "SECRET"


def test_onboard_requires_ip_whitelist(con):
    from tools.set_binance_key import onboard_credential
    client = MagicMock()
    client.probe_trading_disabled.return_value = True
    client.get_spot_balances.return_value = {"BTC": 0.5}
    with pytest.raises(ValueError, match="IP"):
        onboard_credential(con, tenant_id=2, api_key="P", secret="S",
                           client=client, ip_whitelisted=False)
