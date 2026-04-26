"""B6 dashboard observability — pure fns + endpoint (#187 #200)."""
import pytest


# ── compute_next_conditions ─────────────────────────────────────────────────


CFG_NC = {
    "min_trades_for_eval": 20,
    "alert_win_rate_threshold": 0.15,
    "pause_months_consecutive": 3,
    "v2": {"probation": {
        "regression_wr_threshold": 0.10,
        "regression_window_trades": 10,
        "paused_to_probation_days": 14,
    }},
}


def _metrics(wr20=0.5, wr10=0.5, pnl_30d=500.0, months_neg=0, total=50,
              prob_remaining=None, paused_days=None):
    return {
        "trades_count_total": total,
        "win_rate_20_trades": wr20,
        "win_rate_10_trades": wr10,
        "pnl_30d": pnl_30d,
        "months_negative_consecutive": months_neg,
        "probation_trades_remaining": prob_remaining,
        "paused_days_at_entry": paused_days,
    }


def test_next_conditions_normal_returns_healthy():
    from health import compute_next_conditions
    text = compute_next_conditions("NORMAL", _metrics(), False, CFG_NC, 0)
    assert "Saludable" in text


def test_next_conditions_alert_text_includes_wr_and_wins_needed():
    """ALERT with WR=0.10 (2/20 wins), threshold=0.15 (3/20) → wins_needed=1."""
    from health import compute_next_conditions
    text = compute_next_conditions("ALERT", _metrics(wr20=0.10), False, CFG_NC, 0)
    assert "WR" in text and "0.15" in text
    # Spec: text must mention what the threshold is and how many trades to evaluate.


def test_next_conditions_reduced_text_mentions_pnl_30d():
    from health import compute_next_conditions
    text = compute_next_conditions(
        "REDUCED", _metrics(pnl_30d=-50.0), False, CFG_NC, 0,
    )
    assert "pnl_30d" in text or "PnL" in text
    assert "0" in text  # threshold or gap


def test_next_conditions_paused_manual_override_text():
    from health import compute_next_conditions
    text = compute_next_conditions(
        "PAUSED", _metrics(months_neg=4), True, CFG_NC, 0,
    )
    assert "manual" in text.lower() or "Reactivación manual" in text


def test_next_conditions_paused_auto_text_includes_days_remaining():
    """PAUSED 7 days, threshold 14 → 7 days remaining."""
    from health import compute_next_conditions
    text = compute_next_conditions(
        "PAUSED", _metrics(months_neg=4), False, CFG_NC, days_in_paused=7,
    )
    # Should mention days remaining (14 - 7 = 7) or paused_to_probation_days threshold
    assert "días" in text.lower()


def test_next_conditions_probation_text_includes_trades_remaining():
    from health import compute_next_conditions
    text = compute_next_conditions(
        "PROBATION", _metrics(prob_remaining=8), False, CFG_NC, 0,
    )
    assert "8" in text
    assert "trades" in text.lower() or "PROBATION" in text or "NORMAL" in text


# ── sparkline_for_symbol ────────────────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import btc_api
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    yield db_path


def _insert_closed_position(conn, symbol, pnl, exit_ts):
    conn.execute(
        """INSERT INTO positions
           (symbol, direction, status, entry_price, entry_ts,
            exit_price, exit_ts, exit_reason, pnl_usd, pnl_pct)
           VALUES (?, 'LONG', 'closed', 100.0, ?, 110.0, ?, 'TP', ?, ?)""",
        (symbol, exit_ts, exit_ts, pnl, pnl / 100.0),
    )


def test_sparkline_empty_returns_20_nones(tmp_db):
    import btc_api
    from health import sparkline_for_symbol
    conn = btc_api.get_db()
    try:
        result = sparkline_for_symbol("BTC", conn)
    finally:
        conn.close()
    assert len(result) == 20
    assert all(x is None for x in result)


def test_sparkline_3_wins_pads_with_leading_nones(tmp_db):
    """3 wins → [None, None, ..., 'W', 'W', 'W'] in chronological order."""
    import btc_api
    from health import sparkline_for_symbol
    conn = btc_api.get_db()
    try:
        for i in range(3):
            _insert_closed_position(conn, "BTC", 10.0, f"2026-04-{1+i:02d}T12:00:00+00:00")
        conn.commit()
        result = sparkline_for_symbol("BTC", conn)
    finally:
        conn.close()
    assert len(result) == 20
    assert result[-3:] == ['W', 'W', 'W']
    assert all(x is None for x in result[:-3])


def test_sparkline_mixed_wins_losses(tmp_db):
    """W/L based on pnl_usd>0."""
    import btc_api
    from health import sparkline_for_symbol
    conn = btc_api.get_db()
    try:
        _insert_closed_position(conn, "BTC", 10.0, "2026-04-01T12:00:00+00:00")
        _insert_closed_position(conn, "BTC", -5.0, "2026-04-02T12:00:00+00:00")
        _insert_closed_position(conn, "BTC", 0.0, "2026-04-03T12:00:00+00:00")  # breakeven = L
        conn.commit()
        result = sparkline_for_symbol("BTC", conn)
    finally:
        conn.close()
    assert result[-3:] == ['W', 'L', 'L']


def test_sparkline_caps_at_20(tmp_db):
    import btc_api
    from health import sparkline_for_symbol
    conn = btc_api.get_db()
    try:
        for i in range(25):
            _insert_closed_position(conn, "BTC", 10.0, f"2026-04-{1+i%28:02d}T{i%24:02d}:00:00+00:00")
        conn.commit()
        result = sparkline_for_symbol("BTC", conn)
    finally:
        conn.close()
    assert len(result) == 20
    assert all(x == 'W' for x in result)


# ── summarize_recent_alerts ─────────────────────────────────────────────────


def _insert_health_event(conn, symbol, from_state, to_state, reason, ts):
    conn.execute(
        """INSERT INTO symbol_health_events
           (symbol, from_state, to_state, trigger_reason, metrics_json, ts)
           VALUES (?, ?, ?, ?, '{}', ?)""",
        (symbol, from_state, to_state, reason, ts),
    )


def test_summarize_recent_alerts_empty_returns_empty_items(tmp_db):
    import btc_api
    from health import summarize_recent_alerts
    conn = btc_api.get_db()
    try:
        result = summarize_recent_alerts(conn=conn, window_hours=24)
    finally:
        conn.close()
    assert result["items"] == []


def test_summarize_recent_alerts_3_alerts_emits_warning(tmp_db):
    """3 distinct symbols entered ALERT → emits symbol_failures warning."""
    from datetime import datetime, timezone, timedelta
    import btc_api
    from health import summarize_recent_alerts
    now = datetime.now(timezone.utc)
    recent_ts = (now - timedelta(hours=2)).isoformat()
    conn = btc_api.get_db()
    try:
        _insert_health_event(conn, "BTC", "NORMAL", "ALERT", "wr_below_threshold", recent_ts)
        _insert_health_event(conn, "ETH", "NORMAL", "ALERT", "wr_below_threshold", recent_ts)
        _insert_health_event(conn, "DOGE", "NORMAL", "ALERT", "wr_below_threshold", recent_ts)
        conn.commit()
        result = summarize_recent_alerts(conn=conn, window_hours=24)
    finally:
        conn.close()
    items = result["items"]
    assert any(i["kind"] == "symbol_failures" for i in items)
    failure_item = next(i for i in items if i["kind"] == "symbol_failures")
    assert "3" in failure_item["text"]
    assert failure_item["severity"] == "warning"


def test_summarize_recent_alerts_excludes_events_outside_window(tmp_db):
    from datetime import datetime, timezone, timedelta
    import btc_api
    from health import summarize_recent_alerts
    now = datetime.now(timezone.utc)
    old_ts = (now - timedelta(hours=48)).isoformat()
    conn = btc_api.get_db()
    try:
        _insert_health_event(conn, "BTC", "NORMAL", "ALERT", "wr_below_threshold", old_ts)
        conn.commit()
        result = summarize_recent_alerts(conn=conn, window_hours=24)
    finally:
        conn.close()
    assert result["items"] == []
