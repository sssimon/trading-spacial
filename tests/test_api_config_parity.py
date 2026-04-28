"""Parity test for /config endpoints."""
from __future__ import annotations

import json
import pathlib
import tempfile

import pytest
from fastapi.testclient import TestClient


BASELINE_PATH = pathlib.Path(__file__).parent / "_baselines" / "config.json"


@pytest.fixture
def client(monkeypatch, tmp_path):
    """TestClient with isolated config files."""
    cfg_data = {
        "api_key": "test-key",
        "webhook_url": "http://test.local/hook",
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

    import btc_api
    monkeypatch.setattr(btc_api, "CONFIG_FILE", str(cfg_path), raising=False)
    monkeypatch.setattr(btc_api, "DEFAULTS_FILE", str(tmp_path / "_no_defaults.json"), raising=False)
    monkeypatch.setattr(btc_api, "SECRETS_FILE", str(tmp_path / "_no_secrets.json"), raising=False)

    # If api.config exists, patch its constants too (PR2 onwards)
    try:
        import api.config as _ac
        monkeypatch.setattr(_ac, "CONFIG_FILE", str(cfg_path), raising=False)
        monkeypatch.setattr(_ac, "DEFAULTS_FILE", str(tmp_path / "_no_defaults.json"), raising=False)
        monkeypatch.setattr(_ac, "SECRETS_FILE", str(tmp_path / "_no_secrets.json"), raising=False)
    except (ImportError, AttributeError):
        pass

    from btc_api import app
    return TestClient(app)


def test_config_responses_match_baseline(client):
    expected = json.loads(BASELINE_PATH.read_text())
    for label, expected_resp in expected.items():
        # label format: "METHOD /path (variant)"
        parts = label.split(" ", 2)
        method, url = parts[0], parts[1]
        is_auth = "(auth" in label and "no auth" not in label
        body = None
        if "POST" in label:
            body = {"signal_filters": {"min_score": 5}}

        headers = {"X-API-Key": "test-key"} if is_auth else {}
        if method == "GET":
            r = client.get(url, headers=headers)
        elif method == "POST":
            r = client.post(url, json=body, headers=headers)
        else:
            pytest.fail(f"Unexpected method {method}")

        assert r.status_code == expected_resp["status"], f"status mismatch for {label}: got {r.status_code} expected {expected_resp['status']}"
        actual_body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
        assert actual_body == expected_resp["body"], f"body mismatch for {label}"
