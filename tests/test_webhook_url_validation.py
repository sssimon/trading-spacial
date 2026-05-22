"""SSRF guard tests for webhook_url — covers issue #127.

Eight groups:
  1. Allowed (smoke): public hostnames + public IP literals → pass through.
  2. Disallowed schemes: file/gopher/data/ftp → rejected.
  3. Private/loopback/link-local IPv4 literals → rejected (incl. AWS metadata
     169.254.169.254, the Capital-One-2019 vector).
  4. Loopback/link-local/ULA IPv6 literals → rejected.
  5. Hostname resolving (via DNS) to 127.0.0.1 → rejected — DNS mocked so
     this is deterministic on hosts without `localhost` resolution.
  6. Multi-A-record hostnames: any single private address makes the whole
     hostname unsafe; all-public passes.
  7. Integration: POST /config with a malicious webhook_url returns 422 and
     does NOT write the URL to config.json.
  8. Integration: push_webhook with cfg poisoned out-of-band does NOT issue
     an HTTP request and writes a status=0 audit row.
"""
from __future__ import annotations

import json
import socket
from unittest.mock import MagicMock, patch

import pytest

from api.security.url_validation import validate_outbound_url


# ─── Group 1: smoke — known-safe URLs pass ─────────────────────────────────


@pytest.mark.parametrize("url", [
    "https://hooks.slack.com/services/T0/B0/abc",
    # Public IP literals: 8.8.8.8 / 1.1.1.1 are unambiguously globally
    # routable. NOTE: TEST-NET prefixes (192.0.2/24, 198.51.100/24,
    # 203.0.113/24) cannot be used here — Python 3.10+ marks them
    # `is_private=True` per IANA iana-ipv4-special-registry.
    "http://8.8.8.8/webhook",
    "https://api.example.com/path?q=1",
    "http://1.1.1.1:8080/hook",
])
def test_allowed_urls_pass(url, monkeypatch):
    # For hostnames in this group, mock DNS to return a known-public address
    # so the test is deterministic without internet.
    def fake_getaddrinfo(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 0))]
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert validate_outbound_url(url) == url.strip()


# ─── Group 2: schemes other than http(s) are rejected ──────────────────────


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "gopher://10.0.0.1/",
    "data:text/plain,xxx",
    "ftp://example.com/",
    "javascript:alert(1)",
])
def test_disallowed_schemes(url):
    with pytest.raises(ValueError, match="scheme"):
        validate_outbound_url(url)


# ─── Group 3: IPv4 literals in private/loopback/link-local ranges ──────────


@pytest.mark.parametrize("url, expected_reason", [
    ("http://127.0.0.1/",        "loopback"),
    ("http://127.255.255.254/",  "loopback"),
    ("http://10.0.0.1/",         "privada"),
    ("http://172.16.5.4/",       "privada"),
    ("http://192.168.1.1/",      "privada"),
    # The load-bearing case: AWS EC2 instance metadata endpoint, same vector
    # as Capital One 2019.
    ("http://169.254.169.254/latest/meta-data/iam/security-credentials/",
     "link-local"),
    ("http://0.0.0.0/",          "unspecified"),
])
def test_private_ipv4_literals_rejected(url, expected_reason):
    with pytest.raises(ValueError, match=expected_reason):
        validate_outbound_url(url)


# ─── Group 4: IPv6 literals in loopback/link-local/ULA ranges ──────────────


@pytest.mark.parametrize("url, expected_reason", [
    ("http://[::1]/",      "loopback"),
    ("http://[fe80::1]/",  "link-local"),
    ("http://[fc00::1]/",  "privada"),
    # IPv4-mapped IPv6 must unwrap and re-check, else it smuggles loopback.
    ("http://[::ffff:127.0.0.1]/", "loopback"),
])
def test_private_ipv6_literals_rejected(url, expected_reason):
    with pytest.raises(ValueError, match=expected_reason):
        validate_outbound_url(url)


# ─── Group 5: hostname (DNS) resolving to loopback is rejected ─────────────


def test_localhost_via_dns_rejected(monkeypatch):
    """A DNS A-record pointing at 127.0.0.1 is unsafe regardless of the name."""
    def fake_getaddrinfo(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0))]
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(ValueError, match="loopback"):
        validate_outbound_url("http://localhost/")


def test_dns_failure_rejected(monkeypatch):
    """getaddrinfo error = cannot dial = reject."""
    def fake_getaddrinfo(host, port, **kwargs):
        raise socket.gaierror("Name or service not known")
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(ValueError, match="no resuelve"):
        validate_outbound_url("http://nope.invalid/")


# ─── Group 6: multi-A-record defense — one private IP poisons the lot ──────


def test_multi_record_one_private_rejects(monkeypatch):
    def fake_getaddrinfo(host, port, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.1", 0)),
        ]
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(ValueError, match="privada"):
        validate_outbound_url("http://attacker.example.com/")


def test_multi_record_all_public_passes(monkeypatch):
    def fake_getaddrinfo(host, port, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("1.1.1.1", 0)),
        ]
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert validate_outbound_url("http://hooks.example.com/") == "http://hooks.example.com/"


# ─── Group 7: integration — POST /config rejects malicious webhook_url ─────


@pytest.fixture
def config_client(monkeypatch, tmp_path):
    """TestClient pointing at an isolated config.json."""
    from fastapi.testclient import TestClient

    cfg_data = {
        "api_key": "test-key",
        "webhook_url": "",  # start empty
        "telegram_chat_id": "test-chat",
        "telegram_bot_token": "test-token",
        "signal_filters": {"min_score": 4, "require_macro_ok": False, "notify_setup": False},
        "scan_interval_sec": 300,
        "num_symbols": 20,
        "proxy": "",
        "auto_approve_tune": True,
    }
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(cfg_data))

    import api.config as _ac
    monkeypatch.setattr(_ac, "CONFIG_FILE", str(cfg_path), raising=False)
    monkeypatch.setattr(_ac, "DEFAULTS_FILE", str(tmp_path / "_no_defaults.json"), raising=False)
    monkeypatch.setattr(_ac, "SECRETS_FILE", str(tmp_path / "_no_secrets.json"), raising=False)

    import btc_api
    monkeypatch.setattr(btc_api, "CONFIG_FILE", str(cfg_path), raising=False)
    monkeypatch.setattr(btc_api, "DEFAULTS_FILE", str(tmp_path / "_no_defaults.json"), raising=False)
    monkeypatch.setattr(btc_api, "SECRETS_FILE", str(tmp_path / "_no_secrets.json"), raising=False)

    from btc_api import app
    return TestClient(app), cfg_path


def test_post_config_rejects_aws_metadata(config_client):
    client, cfg_path = config_client
    r = client.post(
        "/config",
        json={"webhook_url": "http://169.254.169.254/latest/meta-data/iam/security-credentials/"},
        headers={"X-API-Key": "test-key"},
    )
    assert r.status_code == 422
    detail = r.json().get("detail", [])
    # FastAPI formats validation errors as a list of {loc, msg, type, ...}.
    msgs = " ".join(d.get("msg", "") for d in detail) if isinstance(detail, list) else str(detail)
    assert "rechazado" in msgs.lower() or "link-local" in msgs.lower()

    # And the file must NOT have been overwritten with the bad URL.
    on_disk = json.loads(cfg_path.read_text())
    assert on_disk["webhook_url"] == ""


def test_post_config_accepts_public_url(config_client, monkeypatch):
    # Stub DNS so the test doesn't need internet.
    def fake_getaddrinfo(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 0))]
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    client, cfg_path = config_client
    r = client.post(
        "/config",
        json={"webhook_url": "https://hooks.example.com/services/abc"},
        headers={"X-API-Key": "test-key"},
    )
    assert r.status_code == 200, r.text
    on_disk = json.loads(cfg_path.read_text())
    assert on_disk["webhook_url"] == "https://hooks.example.com/services/abc"


# ─── Group 8: integration — push_webhook with poisoned cfg never dials ─────


def test_push_webhook_with_poisoned_cfg_skips_request(monkeypatch, tmp_path):
    """Out-of-band edit to config.json must not bypass validation."""
    db_path = tmp_path / "test.db"

    import db.connection as dbconn
    monkeypatch.setattr(dbconn, "DB_FILE", str(db_path))

    import btc_api
    monkeypatch.setattr(btc_api, "DB_FILE", str(db_path))

    from db.schema import init_db
    init_db()

    from api.telegram import push_webhook

    poisoned_cfg = {
        "webhook_url": "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "telegram_chat_id": "test-chat",
        "webhook_secret": "",
    }
    signal_rep = {
        "symbol": "BTCUSDT", "estado": "LONG", "direction": "LONG", "score": 5,
        "score_label": "premium", "señal_activa": True,
        "lrc_1h": {"pct": 20.0}, "macro_4h": {"price_above": True},
        "gatillo_5m": {"vela_5m_alcista": True, "rsi_recuperando": True},
        "price": 50000.0, "timestamp": "2026-01-15T10:00:00Z",
        "sizing_1h": {"sl_precio": 49000.0, "tp_precio": 54000.0,
                      "atr_1h": 500.0, "qty_btc": 0.002,
                      "sl_pct": "2%", "tp_pct": "4%"},
        "confirmations": {},
    }

    with patch("api.telegram.req_lib.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, ok=True)
        push_webhook(signal_rep, scan_id=999, cfg=poisoned_cfg)
        assert not mock_post.called, "push_webhook must NOT dial a link-local URL"

    # Blocked attempt is audited (status=0, ok=0) so operators can see it.
    from db.connection import get_db
    con = get_db()
    rows = con.execute("SELECT scan_id, status, ok FROM webhooks_sent").fetchall()
    con.close()
    assert len(rows) == 1
    assert rows[0][0] == 999     # scan_id
    assert rows[0][1] == 0       # status
    assert rows[0][2] == 0       # ok
