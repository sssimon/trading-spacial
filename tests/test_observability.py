"""Tests for the kill switch decision log (Phase 1 of #187)."""
import json

import pytest


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import btc_api
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    btc_api.init_db()
    yield db_path


def test_record_decision_inserts_row(tmp_db):
    from observability import record_decision, query_decisions
    record_decision(
        symbol="BTCUSDT",
        engine="v1",
        per_symbol_tier="NORMAL",
        portfolio_tier="NORMAL",
        size_factor=1.0,
        skip=False,
        reasons={"wr_rolling_20": 0.35},
        scan_id=None,
        slider_value=None,
        velocity_active=False,
    )
    rows = query_decisions()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "BTCUSDT"
    assert rows[0]["engine"] == "v1"
    assert rows[0]["per_symbol_tier"] == "NORMAL"
    assert rows[0]["size_factor"] == 1.0
    assert rows[0]["skip"] is False
    assert json.loads(rows[0]["reasons_json"]) == {"wr_rolling_20": 0.35}


def test_query_filters_by_symbol(tmp_db):
    from observability import record_decision, query_decisions
    record_decision(symbol="BTCUSDT", engine="v1", per_symbol_tier="NORMAL",
                    portfolio_tier="NORMAL", size_factor=1.0, skip=False,
                    reasons={}, scan_id=None, slider_value=None, velocity_active=False)
    record_decision(symbol="ETHUSDT", engine="v1", per_symbol_tier="ALERT",
                    portfolio_tier="NORMAL", size_factor=1.0, skip=False,
                    reasons={}, scan_id=None, slider_value=None, velocity_active=False)
    rows = query_decisions(symbol="ETHUSDT")
    assert len(rows) == 1
    assert rows[0]["symbol"] == "ETHUSDT"


def test_query_filters_by_engine(tmp_db):
    from observability import record_decision, query_decisions
    record_decision(symbol="BTCUSDT", engine="v1", per_symbol_tier="NORMAL",
                    portfolio_tier="NORMAL", size_factor=1.0, skip=False,
                    reasons={}, scan_id=None, slider_value=None, velocity_active=False)
    record_decision(symbol="BTCUSDT", engine="v2_shadow", per_symbol_tier="NORMAL",
                    portfolio_tier="NORMAL", size_factor=1.0, skip=False,
                    reasons={}, scan_id=None, slider_value=None, velocity_active=False)
    rows = query_decisions(engine="v1")
    assert len(rows) == 1
    assert rows[0]["engine"] == "v1"


def test_query_ordered_by_ts_desc(tmp_db):
    from observability import record_decision, query_decisions
    record_decision(symbol="A", engine="v1", per_symbol_tier="NORMAL",
                    portfolio_tier="NORMAL", size_factor=1.0, skip=False,
                    reasons={}, scan_id=None, slider_value=None, velocity_active=False)
    record_decision(symbol="B", engine="v1", per_symbol_tier="NORMAL",
                    portfolio_tier="NORMAL", size_factor=1.0, skip=False,
                    reasons={}, scan_id=None, slider_value=None, velocity_active=False)
    rows = query_decisions()
    assert rows[0]["symbol"] == "B"
    assert rows[1]["symbol"] == "A"


def test_query_respects_limit(tmp_db):
    from observability import record_decision, query_decisions
    for i in range(5):
        record_decision(symbol=f"SYM{i}", engine="v1", per_symbol_tier="NORMAL",
                        portfolio_tier="NORMAL", size_factor=1.0, skip=False,
                        reasons={}, scan_id=None, slider_value=None, velocity_active=False)
    rows = query_decisions(limit=3)
    assert len(rows) == 3


def test_query_filters_by_since(tmp_db):
    from observability import record_decision, query_decisions
    import time
    record_decision(symbol="OLD", engine="v1", per_symbol_tier="NORMAL",
                    portfolio_tier="NORMAL", size_factor=1.0, skip=False,
                    reasons={}, scan_id=None, slider_value=None, velocity_active=False)
    # Capture a cutoff between the two inserts
    time.sleep(0.01)  # ensure different ISO timestamps
    from datetime import datetime, timezone
    cutoff = datetime.now(timezone.utc).isoformat()
    time.sleep(0.01)
    record_decision(symbol="NEW", engine="v1", per_symbol_tier="NORMAL",
                    portfolio_tier="NORMAL", size_factor=1.0, skip=False,
                    reasons={}, scan_id=None, slider_value=None, velocity_active=False)
    rows = query_decisions(since=cutoff)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "NEW"


def test_compute_portfolio_aggregate_all_normal():
    from observability import compute_portfolio_aggregate
    per_symbol_tiers = {"BTCUSDT": "NORMAL", "ETHUSDT": "NORMAL", "ADAUSDT": "NORMAL"}
    result = compute_portfolio_aggregate(per_symbol_tiers, concurrent_alert_threshold=3)
    assert result["tier"] == "NORMAL"
    assert result["concurrent_failures"] == 0


def test_compute_portfolio_aggregate_warned_at_threshold():
    from observability import compute_portfolio_aggregate
    per_symbol_tiers = {
        "BTCUSDT": "ALERT", "ETHUSDT": "REDUCED",
        "ADAUSDT": "PAUSED", "XLMUSDT": "NORMAL",
    }
    result = compute_portfolio_aggregate(per_symbol_tiers, concurrent_alert_threshold=3)
    assert result["tier"] == "WARNED"
    assert result["concurrent_failures"] == 3


def test_compute_portfolio_aggregate_below_threshold():
    from observability import compute_portfolio_aggregate
    per_symbol_tiers = {"BTCUSDT": "ALERT", "ETHUSDT": "NORMAL", "ADAUSDT": "NORMAL"}
    result = compute_portfolio_aggregate(per_symbol_tiers, concurrent_alert_threshold=3)
    assert result["tier"] == "NORMAL"
    assert result["concurrent_failures"] == 1


def test_compute_portfolio_aggregate_empty_input():
    from observability import compute_portfolio_aggregate
    result = compute_portfolio_aggregate({}, concurrent_alert_threshold=3)
    assert result["tier"] == "NORMAL"
    assert result["concurrent_failures"] == 0


def test_get_current_state_returns_latest_per_symbol(tmp_db):
    from observability import record_decision, get_current_state
    # Record two decisions for the same symbol; newer should win
    record_decision(symbol="BTCUSDT", engine="v1", per_symbol_tier="NORMAL",
                    portfolio_tier="NORMAL", size_factor=1.0, skip=False,
                    reasons={}, scan_id=None, slider_value=None, velocity_active=False)
    record_decision(symbol="BTCUSDT", engine="v1", per_symbol_tier="ALERT",
                    portfolio_tier="NORMAL", size_factor=1.0, skip=False,
                    reasons={}, scan_id=None, slider_value=None, velocity_active=False)
    record_decision(symbol="ETHUSDT", engine="v1", per_symbol_tier="REDUCED",
                    portfolio_tier="NORMAL", size_factor=0.5, skip=False,
                    reasons={}, scan_id=None, slider_value=None, velocity_active=False)

    state = get_current_state()
    assert state["symbols"]["BTCUSDT"]["per_symbol_tier"] == "ALERT"
    assert state["symbols"]["ETHUSDT"]["per_symbol_tier"] == "REDUCED"
    assert state["portfolio"]["tier"] == "NORMAL"
    # ALERT + REDUCED = 2 concurrent failures (< default threshold of 3)
    assert state["portfolio"]["concurrent_failures"] == 2


def test_get_current_state_empty_db(tmp_db):
    from observability import get_current_state
    state = get_current_state()
    assert state["symbols"] == {}
    assert state["portfolio"]["tier"] == "NORMAL"
    assert state["portfolio"]["concurrent_failures"] == 0


def test_get_current_state_filters_by_engine(tmp_db):
    from observability import record_decision, get_current_state
    record_decision(symbol="BTCUSDT", engine="v1", per_symbol_tier="NORMAL",
                    portfolio_tier="NORMAL", size_factor=1.0, skip=False,
                    reasons={}, scan_id=None, slider_value=None, velocity_active=False)
    record_decision(symbol="BTCUSDT", engine="v2_shadow", per_symbol_tier="ALERT",
                    portfolio_tier="NORMAL", size_factor=1.0, skip=False,
                    reasons={}, scan_id=None, slider_value=None, velocity_active=False)

    state_v1 = get_current_state(engine="v1")
    state_v2 = get_current_state(engine="v2_shadow")
    assert state_v1["symbols"]["BTCUSDT"]["per_symbol_tier"] == "NORMAL"
    assert state_v2["symbols"]["BTCUSDT"]["per_symbol_tier"] == "ALERT"


def test_get_current_state_engine_v2_shadow(tmp_db):
    from observability import record_decision, get_current_state
    record_decision(
        symbol="BTCUSDT", engine="v1",
        per_symbol_tier="NORMAL", portfolio_tier="NORMAL",
        size_factor=1.0, skip=False, reasons={},
        scan_id=None, slider_value=None, velocity_active=False,
    )
    record_decision(
        symbol="BTCUSDT", engine="v2_shadow",
        per_symbol_tier="NORMAL", portfolio_tier="REDUCED",
        size_factor=1.0, skip=False,
        reasons={"portfolio_dd": -0.06},
        scan_id=None, slider_value=50.0, velocity_active=False,
    )

    v1_state = get_current_state(engine="v1")
    shadow_state = get_current_state(engine="v2_shadow")

    assert v1_state["symbols"]["BTCUSDT"]["portfolio_tier"] == "NORMAL"
    assert shadow_state["symbols"]["BTCUSDT"]["portfolio_tier"] == "REDUCED"
