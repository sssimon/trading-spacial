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


# ─── Group 8: opt-in permissive mode (`allow_private=True`) ────────────────
# Operators with trusted internal webhooks (n8n on localhost, internal k8s
# service) can set `cfg.security.webhook_allow_private_ips=true`. The flag
# loosens local-network trust but NEVER unlocks IMDS.


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/",
    "http://10.0.0.1/",
    "http://172.16.5.4/",
    "http://192.168.1.1/",
    "http://[::1]/",
    "http://[fc00::1]/",
])
def test_allow_private_permits_loopback_and_rfc1918(url):
    assert validate_outbound_url(url, allow_private=True) == url


def test_allow_private_still_blocks_imds():
    """Load-bearing: opt-in mode MUST still block 169.254.169.254."""
    with pytest.raises(ValueError, match="link-local"):
        validate_outbound_url(
            "http://169.254.169.254/latest/meta-data/",
            allow_private=True,
        )


def test_allow_private_still_blocks_zero():
    with pytest.raises(ValueError, match="unspecified"):
        validate_outbound_url("http://0.0.0.0/", allow_private=True)


def test_allow_private_still_blocks_non_http():
    with pytest.raises(ValueError, match="scheme"):
        validate_outbound_url("file:///etc/passwd", allow_private=True)


def test_allow_private_via_dns_localhost(monkeypatch):
    """A hostname resolving to 127.0.0.1 is permitted under allow_private."""
    def fake_getaddrinfo(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0))]
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert validate_outbound_url(
        "http://localhost/", allow_private=True
    ) == "http://localhost/"


def test_post_config_with_flag_accepts_localhost(config_client, monkeypatch):
    """Operator can flip the flag + set a private webhook in one POST."""
    def fake_getaddrinfo(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0))]
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    client, cfg_path = config_client
    r = client.post(
        "/config",
        json={
            "webhook_url": "http://localhost:5678/webhook/n8n",
            "security": {"webhook_allow_private_ips": True},
        },
        headers={"X-API-Key": "test-key"},
    )
    assert r.status_code == 200, r.text
    on_disk = json.loads(cfg_path.read_text())
    assert on_disk["webhook_url"] == "http://localhost:5678/webhook/n8n"
    assert on_disk["security"]["webhook_allow_private_ips"] is True


def test_post_config_with_flag_still_rejects_imds(config_client):
    """Flag does NOT unlock IMDS — POST /config still 422s."""
    client, _ = config_client
    r = client.post(
        "/config",
        json={
            "webhook_url": "http://169.254.169.254/latest/meta-data/",
            "security": {"webhook_allow_private_ips": True},
        },
        headers={"X-API-Key": "test-key"},
    )
    assert r.status_code == 422


# ─── Group 9: integration — push_webhook with poisoned cfg never dials ─────


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


# ─── Group 10: block-path for the other two outbound sites ─────────────────


def test_webhook_channel_skips_imds_and_continues():
    """WebhookChannel.send: an IMDS endpoint is skipped; safe ones still fire."""
    from notifier.channels.webhook import WebhookChannel

    cfg = {
        "notifier": {
            "channels": {
                "webhook": {
                    "enabled": True,
                    "endpoints": [
                        {"url": "http://169.254.169.254/imds"},
                        {"url": "http://8.8.8.8/ok"},
                    ],
                },
            },
        },
        # No security section → default strict.
    }
    channel = WebhookChannel(cfg)
    ok = MagicMock()
    ok.ok = True
    ok.status_code = 200

    with patch("notifier.channels.webhook.requests.post", return_value=ok) as mock_post:
        receipt = channel.send('{}', event_type="signal")

    # Overall ok=ok because the safe endpoint succeeded; first endpoint's
    # rejection is recorded in the error string.
    assert receipt.status == "ok"
    assert "169.254.169.254" in receipt.error
    assert "SSRF guard" in receipt.error
    # ONLY the safe endpoint was actually dialed.
    assert mock_post.call_count == 1
    assert mock_post.call_args[0][0] == "http://8.8.8.8/ok"


def test_webhook_channel_allow_private_still_blocks_imds():
    """Even with allow_private=true at app level, the channel must block IMDS."""
    from notifier.channels.webhook import WebhookChannel

    cfg = {
        "security": {"webhook_allow_private_ips": True},
        "notifier": {
            "channels": {
                "webhook": {
                    "enabled": True,
                    "endpoints": [{"url": "http://169.254.169.254/imds"}],
                },
            },
        },
    }
    channel = WebhookChannel(cfg)

    with patch("notifier.channels.webhook.requests.post") as mock_post:
        receipt = channel.send('{}', event_type="signal")

    assert receipt.status == "failed"
    assert "link-local" in receipt.error
    assert not mock_post.called


def test_webhook_test_endpoint_blocks_poisoned_url(monkeypatch, tmp_path):
    """/webhook/test with poisoned config returns ok=False without dialing."""
    from fastapi.testclient import TestClient

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "api_key": "test-key",
        "webhook_url": "http://169.254.169.254/imds",
        "telegram_chat_id": "test-chat",
        "telegram_bot_token": "",
    }))

    import api.config as _ac
    monkeypatch.setattr(_ac, "CONFIG_FILE", str(cfg_path), raising=False)
    monkeypatch.setattr(_ac, "DEFAULTS_FILE", str(tmp_path / "_no_defaults.json"), raising=False)
    monkeypatch.setattr(_ac, "SECRETS_FILE", str(tmp_path / "_no_secrets.json"), raising=False)

    import btc_api
    monkeypatch.setattr(btc_api, "CONFIG_FILE", str(cfg_path), raising=False)
    monkeypatch.setattr(btc_api, "DEFAULTS_FILE", str(tmp_path / "_no_defaults.json"), raising=False)
    monkeypatch.setattr(btc_api, "SECRETS_FILE", str(tmp_path / "_no_secrets.json"), raising=False)

    from btc_api import app
    client = TestClient(app)

    with patch("btc_api.req_lib.post") as mock_post:
        r = client.get("/webhook/test", headers={"X-API-Key": "test-key"})

    assert r.status_code == 200
    data = r.json()
    # The IMDS attempt must be blocked + surfaced in the response.
    assert data["webhook_n8n"]["ok"] is False
    assert "SSRF guard" in data["webhook_n8n"]["error"]
    # And the validator must have short-circuited BEFORE the HTTP call.
    assert not mock_post.called
