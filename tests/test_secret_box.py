import os
import pytest


def test_roundtrip_encrypt_decrypt(monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("TRADING_BINANCE_MASTER_KEY", Fernet.generate_key().decode())
    from db.secret_box import encrypt_secret, decrypt_secret
    token = encrypt_secret("my-binance-secret")
    assert token != b"my-binance-secret"
    assert b"my-binance-secret" not in token
    assert decrypt_secret(token) == "my-binance-secret"


def test_fail_closed_when_master_key_missing(monkeypatch):
    monkeypatch.delenv("TRADING_BINANCE_MASTER_KEY", raising=False)
    from db.secret_box import encrypt_secret
    with pytest.raises(RuntimeError, match="TRADING_BINANCE_MASTER_KEY"):
        encrypt_secret("x")
