"""GET /health/symbols, GET /health/events, POST /health/reactivate/{symbol}."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    import btc_api
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    return TestClient(btc_api.app)


def test_get_health_symbols_empty(client):
    resp = client.get("/health/symbols")
    assert resp.status_code == 200
    assert resp.json() == {"symbols": []}


def test_get_health_symbols_returns_rows(client):
    from health import apply_transition
    apply_transition(
        "BTC", new_state="ALERT", reason="wr_below_threshold",
        metrics={"trades_count_total": 50, "win_rate_20_trades": 0.1,
                  "pnl_30d": 0.0, "pnl_by_month": {},
                  "months_negative_consecutive": 0},
        from_state="NORMAL",
    )
    resp = client.get("/health/symbols")
    assert resp.status_code == 200
    payload = resp.json()
    assert len(payload["symbols"]) == 1
    assert payload["symbols"][0]["symbol"] == "BTC"
    assert payload["symbols"][0]["state"] == "ALERT"


def test_get_health_events_returns_history(client):
    from health import apply_transition
    metrics = {"trades_count_total": 50, "win_rate_20_trades": 0.5,
                "pnl_30d": 0.0, "pnl_by_month": {},
                "months_negative_consecutive": 0}
    apply_transition("BTC", "ALERT", "wr_below_threshold", metrics, "NORMAL")
    apply_transition("BTC", "REDUCED", "pnl_neg_30d", metrics, "ALERT")
    resp = client.get("/health/events?symbol=BTC")
    assert resp.status_code == 200
    events = resp.json()["events"]
    assert len(events) == 2
    # Most recent first
    assert events[0]["to_state"] == "REDUCED"
    assert events[1]["to_state"] == "ALERT"


def test_post_health_reactivate_sets_manual_override(client):
    """B5: PAUSED → PROBATION (was → NORMAL). manual_override=1 for reason='manual'."""
    from health import apply_transition
    metrics = {"trades_count_total": 50, "win_rate_20_trades": 0.5,
                "pnl_30d": 0.0, "pnl_by_month": {},
                "months_negative_consecutive": 0,
                "win_rate_10_trades": 0.5}
    apply_transition("JUP", "PAUSED", "3mo_consec_neg", metrics, "REDUCED")

    resp = client.post("/health/reactivate/JUP", json={"reason": "manual"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["state"] == "PROBATION"

    # GET again and verify
    resp = client.get("/health/symbols")
    rows = {r["symbol"]: r for r in resp.json()["symbols"]}
    assert rows["JUP"]["state"] == "PROBATION"
    assert rows["JUP"]["manual_override"] == 1


def test_post_health_reactivate_auto_recovery_no_manual_override(client):
    """reason='auto_recovery' returns PROBATION but does NOT set manual_override."""
    from health import apply_transition
    metrics = {"trades_count_total": 50, "win_rate_20_trades": 0.5,
                "pnl_30d": 0.0, "pnl_by_month": {},
                "months_negative_consecutive": 0,
                "win_rate_10_trades": 0.5}
    apply_transition("UNI", "PAUSED", "3mo_consec_neg", metrics, "REDUCED")

    resp = client.post("/health/reactivate/UNI", json={"reason": "auto_recovery"})
    assert resp.status_code == 200
    assert resp.json()["state"] == "PROBATION"

    resp = client.get("/health/symbols")
    rows = {r["symbol"]: r for r in resp.json()["symbols"]}
    assert rows["UNI"]["state"] == "PROBATION"
    assert rows["UNI"]["manual_override"] == 0
