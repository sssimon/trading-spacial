"""B5 PROBATION tier — pure functions + state machine + DB lifecycle (#187 #199)."""
import pytest


# ── compute_probation_trades_remaining ──────────────────────────────────────


def test_compute_probation_trades_remaining_zero_days():
    """No days in PAUSED → trades_base unchanged."""
    from health import compute_probation_trades_remaining
    assert compute_probation_trades_remaining(0, trades_base=10, per_pause_day=0.2) == 10


def test_compute_probation_trades_remaining_negative_days_clamps():
    """Negative days_paused (clock skew, etc.) → trades_base."""
    from health import compute_probation_trades_remaining
    assert compute_probation_trades_remaining(-3, trades_base=10, per_pause_day=0.2) == 10


def test_compute_probation_trades_remaining_seven_days():
    """7 days * 0.2 = 1.4 → rounds to 11 (10 + 1)."""
    from health import compute_probation_trades_remaining
    assert compute_probation_trades_remaining(7, trades_base=10, per_pause_day=0.2) == 11


def test_compute_probation_trades_remaining_fifteen_days():
    """15 days * 0.2 = 3 → 13 (spec example)."""
    from health import compute_probation_trades_remaining
    assert compute_probation_trades_remaining(15, trades_base=10, per_pause_day=0.2) == 13


def test_compute_probation_trades_remaining_thirty_days():
    """30 days * 0.2 = 6 → 16."""
    from health import compute_probation_trades_remaining
    assert compute_probation_trades_remaining(30, trades_base=10, per_pause_day=0.2) == 16


def test_compute_probation_trades_remaining_default_args():
    """Defaults match spec: trades_base=10, per_pause_day=0.2."""
    from health import compute_probation_trades_remaining
    assert compute_probation_trades_remaining(15) == 13


# ── reactivate_symbol PAUSED → PROBATION ────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import btc_api
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    yield db_path


def _seed_paused(symbol, days_ago):
    """Insert a PAUSED row whose state_since is `days_ago` days before now."""
    from datetime import datetime, timezone, timedelta
    import btc_api
    state_since = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    conn = btc_api.get_db()
    try:
        conn.execute(
            """INSERT INTO symbol_health
               (symbol, state, state_since, last_evaluated_at, last_metrics_json)
               VALUES (?, 'PAUSED', ?, ?, '{}')""",
            (symbol, state_since, state_since),
        )
        conn.commit()
    finally:
        conn.close()


def _read_health_row(symbol):
    import btc_api
    conn = btc_api.get_db()
    try:
        row = conn.execute(
            """SELECT state, probation_trades_remaining, probation_started_at,
                      paused_days_at_entry, manual_override
               FROM symbol_health WHERE symbol=?""",
            (symbol,),
        ).fetchone()
    finally:
        conn.close()
    return row


def test_reactivate_symbol_paused_zero_days_to_probation_base_count(tmp_db):
    """PAUSED for 0 days → PROBATION + trades_remaining=10 (default trades_base)."""
    from health import reactivate_symbol
    _seed_paused("BTC", days_ago=0)
    reactivate_symbol("BTC", reason="manual")
    row = _read_health_row("BTC")
    assert row[0] == "PROBATION"
    assert row[1] == 10
    assert row[2] is not None  # probation_started_at populated
    assert row[3] == 0


def test_reactivate_symbol_paused_seven_days_to_probation_eleven(tmp_db):
    """PAUSED for 7 days → PROBATION + trades_remaining=11 (10 + round(0.2*7) = 11)."""
    from health import reactivate_symbol
    _seed_paused("ETH", days_ago=7)
    reactivate_symbol("ETH", reason="manual")
    row = _read_health_row("ETH")
    assert row[0] == "PROBATION"
    assert row[1] == 11
    assert row[3] == 7


def test_reactivate_symbol_paused_fifteen_days_to_probation_thirteen(tmp_db):
    """Spec example: 15 days paused → 13 trades_remaining."""
    from health import reactivate_symbol
    _seed_paused("DOGE", days_ago=15)
    reactivate_symbol("DOGE", reason="manual")
    row = _read_health_row("DOGE")
    assert row[0] == "PROBATION"
    assert row[1] == 13
    assert row[3] == 15


def test_reactivate_symbol_sets_manual_override_for_manual_reason(tmp_db):
    """reason='manual' sets manual_override=1; reason='auto_recovery' sets 0."""
    from health import reactivate_symbol
    _seed_paused("UNI", days_ago=10)
    reactivate_symbol("UNI", reason="manual")
    assert _read_health_row("UNI")[4] == 1

    _seed_paused("XLM", days_ago=10)
    reactivate_symbol("XLM", reason="auto_recovery")
    assert _read_health_row("XLM")[4] == 0


def test_reactivate_symbol_noop_when_not_paused(tmp_db, caplog):
    """Calling reactivate_symbol on a NORMAL symbol is a no-op + warning."""
    import logging
    from health import reactivate_symbol
    # Seed NORMAL (no row → reactivate sees state='NORMAL' default)
    with caplog.at_level(logging.WARNING, logger="health"):
        reactivate_symbol("AVAX", reason="manual")
    # Row may or may not exist; the key assertion is no PROBATION transition occurred.
    row = _read_health_row("AVAX")
    if row is not None:
        assert row[0] != "PROBATION"
    # Warning logged
    assert any("not in PAUSED" in r.message or "not paused" in r.message.lower()
               for r in caplog.records)


# ── Trade lifecycle hook ────────────────────────────────────────────────────


def test_decrement_probation_counter_decreases_value(tmp_db):
    """_decrement_probation_counter drops trades_remaining by 1."""
    import btc_api
    from health import _decrement_probation_counter
    conn = btc_api.get_db()
    try:
        conn.execute(
            """INSERT INTO symbol_health
               (symbol, state, state_since, last_evaluated_at, last_metrics_json,
                probation_trades_remaining, probation_started_at, paused_days_at_entry)
               VALUES ('BTC', 'PROBATION', '2026-04-01T00:00:00+00:00',
                       '2026-04-01T00:00:00+00:00', '{}', 13, '2026-04-01T00:00:00+00:00', 15)"""
        )
        conn.commit()
    finally:
        conn.close()
    _decrement_probation_counter("BTC")
    row = _read_health_row("BTC")
    assert row[1] == 12


def test_decrement_probation_counter_noop_when_not_probation(tmp_db):
    """When state is NOT PROBATION, decrement is a no-op (no row mutation)."""
    import btc_api
    from health import _decrement_probation_counter
    conn = btc_api.get_db()
    try:
        conn.execute(
            """INSERT INTO symbol_health (symbol, state, state_since, last_evaluated_at, last_metrics_json)
               VALUES ('BTC', 'NORMAL', '2026-04-01T00:00:00+00:00',
                       '2026-04-01T00:00:00+00:00', '{}')"""
        )
        conn.commit()
    finally:
        conn.close()
    _decrement_probation_counter("BTC")  # must not raise, must not change anything
    row = _read_health_row("BTC")
    assert row[0] == "NORMAL"
    assert row[1] is None  # was NULL, stays NULL


def test_decrement_probation_counter_floors_at_zero(tmp_db):
    """When counter is 0, decrement does not go negative."""
    import btc_api
    from health import _decrement_probation_counter
    conn = btc_api.get_db()
    try:
        conn.execute(
            """INSERT INTO symbol_health
               (symbol, state, state_since, last_evaluated_at, last_metrics_json,
                probation_trades_remaining, probation_started_at, paused_days_at_entry)
               VALUES ('BTC', 'PROBATION', '2026-04-01T00:00:00+00:00',
                       '2026-04-01T00:00:00+00:00', '{}', 0, '2026-04-01T00:00:00+00:00', 15)"""
        )
        conn.commit()
    finally:
        conn.close()
    _decrement_probation_counter("BTC")
    row = _read_health_row("BTC")
    assert row[1] == 0


def test_trigger_health_evaluation_decrements_then_evaluates(tmp_db):
    """trigger_health_evaluation on a PROBATION symbol decrements counter THEN evals."""
    import btc_api
    from health import trigger_health_evaluation, get_symbol_state

    # Seed PROBATION with counter=1 — after decrement, counter=0 → evaluate_state sees
    # 0 and returns NORMAL with reason="probation_complete".
    conn = btc_api.get_db()
    try:
        conn.execute(
            """INSERT INTO symbol_health
               (symbol, state, state_since, last_evaluated_at, last_metrics_json,
                probation_trades_remaining, probation_started_at, paused_days_at_entry)
               VALUES ('BTC', 'PROBATION', '2026-04-01T00:00:00+00:00',
                       '2026-04-01T00:00:00+00:00', '{}', 1, '2026-04-01T00:00:00+00:00', 15)"""
        )
        # Add 25 winning closed trades so trades_count_total >= min_trades_for_eval
        for i in range(25):
            conn.execute(
                """INSERT INTO positions
                   (symbol, direction, status, entry_price, entry_ts,
                    exit_price, exit_ts, exit_reason, pnl_usd, pnl_pct)
                   VALUES ('BTC', 'LONG', 'closed', 100.0, ?, 110.0, ?, 'TP', 10.0, 0.10)""",
                (f"2026-05-{1+i%28:02d}T12:00:00+00:00", f"2026-05-{1+i%28:02d}T13:00:00+00:00"),
            )
        conn.commit()
    finally:
        conn.close()

    cfg = {"kill_switch": {
        "enabled": True, "min_trades_for_eval": 20,
        "alert_win_rate_threshold": 0.15, "reduce_size_factor": 0.5,
        "pause_months_consecutive": 3, "auto_recovery_enabled": True,
        "v2": {"probation": {
            "regression_wr_threshold": 0.10, "regression_window_trades": 10,
        }},
    }}
    trigger_health_evaluation("BTC", cfg)
    assert get_symbol_state("BTC") == "NORMAL"


def test_daily_cron_eval_does_not_decrement_probation(tmp_db):
    """evaluate_and_record (daily cron path) does NOT decrement probation counter."""
    import btc_api
    from health import evaluate_and_record
    conn = btc_api.get_db()
    try:
        conn.execute(
            """INSERT INTO symbol_health
               (symbol, state, state_since, last_evaluated_at, last_metrics_json,
                probation_trades_remaining, probation_started_at, paused_days_at_entry)
               VALUES ('BTC', 'PROBATION', '2026-04-01T00:00:00+00:00',
                       '2026-04-01T00:00:00+00:00', '{}', 5, '2026-04-01T00:00:00+00:00', 15)"""
        )
        # 25 winning trades — enough for eval but no regression
        for i in range(25):
            conn.execute(
                """INSERT INTO positions
                   (symbol, direction, status, entry_price, entry_ts,
                    exit_price, exit_ts, exit_reason, pnl_usd, pnl_pct)
                   VALUES ('BTC', 'LONG', 'closed', 100.0, ?, 110.0, ?, 'TP', 10.0, 0.10)""",
                (f"2026-05-{1+i%28:02d}T12:00:00+00:00", f"2026-05-{1+i%28:02d}T13:00:00+00:00"),
            )
        conn.commit()
    finally:
        conn.close()
    cfg = {"kill_switch": {
        "enabled": True, "min_trades_for_eval": 20,
        "alert_win_rate_threshold": 0.15, "reduce_size_factor": 0.5,
        "pause_months_consecutive": 3, "auto_recovery_enabled": True,
        "v2": {"probation": {
            "regression_wr_threshold": 0.10, "regression_window_trades": 10,
        }},
    }}
    evaluate_and_record("BTC", cfg)
    row = _read_health_row("BTC")
    assert row[0] == "PROBATION"  # still in PROBATION
    assert row[1] == 5             # counter NOT decremented


# ── Auto-reactivation in daily cron ─────────────────────────────────────────


def test_auto_reactivate_paused_15d_portfolio_normal_promotes_to_probation(tmp_db):
    """PAUSED 15 days + portfolio NORMAL → auto-promotes to PROBATION."""
    from unittest.mock import patch
    from health import _maybe_auto_reactivate, get_symbol_state
    _seed_paused("BTC", days_ago=15)
    cfg = {"kill_switch": {"enabled": True, "v2": {"probation": {
        "paused_to_probation_days": 14, "trades_base": 10, "trades_per_pause_day": 0.2,
    }}}}
    with patch("health._is_portfolio_normal", return_value=True):
        _maybe_auto_reactivate("BTC", threshold_days=14, cfg=cfg)
    assert get_symbol_state("BTC") == "PROBATION"
    row = _read_health_row("BTC")
    assert row[1] == 13  # 10 + round(0.2*15)
    assert row[4] == 0   # auto_recovery → manual_override=0


def test_auto_reactivate_paused_below_threshold_stays_paused(tmp_db):
    """PAUSED 10 days (< 14 threshold) → stays PAUSED."""
    from unittest.mock import patch
    from health import _maybe_auto_reactivate, get_symbol_state
    _seed_paused("BTC", days_ago=10)
    cfg = {"kill_switch": {"enabled": True, "v2": {"probation": {"paused_to_probation_days": 14}}}}
    with patch("health._is_portfolio_normal", return_value=True):
        _maybe_auto_reactivate("BTC", threshold_days=14, cfg=cfg)
    assert get_symbol_state("BTC") == "PAUSED"


def test_auto_reactivate_paused_15d_portfolio_reduced_stays_paused(tmp_db):
    """PAUSED 15 days but portfolio REDUCED → portfolio gate blocks."""
    from unittest.mock import patch
    from health import _maybe_auto_reactivate, get_symbol_state
    _seed_paused("BTC", days_ago=15)
    cfg = {"kill_switch": {"enabled": True, "v2": {"probation": {"paused_to_probation_days": 14}}}}
    with patch("health._is_portfolio_normal", return_value=False):
        _maybe_auto_reactivate("BTC", threshold_days=14, cfg=cfg)
    assert get_symbol_state("BTC") == "PAUSED"


def test_auto_reactivate_threshold_exact_boundary_fires(tmp_db):
    """PAUSED for exactly threshold_days → fires (>= semantics, per spec boundary)."""
    from unittest.mock import patch
    from health import _maybe_auto_reactivate, get_symbol_state
    _seed_paused("BTC", days_ago=14)
    cfg = {"kill_switch": {"enabled": True, "v2": {"probation": {"paused_to_probation_days": 14}}}}
    with patch("health._is_portfolio_normal", return_value=True):
        _maybe_auto_reactivate("BTC", threshold_days=14, cfg=cfg)
    assert get_symbol_state("BTC") == "PROBATION"


def test_evaluate_all_symbols_runs_auto_reactivate_for_each(tmp_db, monkeypatch):
    """evaluate_all_symbols invokes auto-reactivation per symbol before metric eval."""
    from unittest.mock import patch
    from health import evaluate_all_symbols
    cfg = {"kill_switch": {"enabled": True, "v2": {"probation": {"paused_to_probation_days": 14}}}}
    # DEFAULT_SYMBOLS is an actual list; just monkeypatch it to a tiny set
    import btc_scanner
    monkeypatch.setattr(btc_scanner, "DEFAULT_SYMBOLS", ["AAA", "BBB"])
    calls = []

    def fake_maybe(symbol, threshold_days, cfg):
        calls.append(symbol)

    with patch("health._maybe_auto_reactivate", side_effect=fake_maybe), \
         patch("health.evaluate_and_record", return_value="NORMAL"):
        evaluate_all_symbols(cfg)

    assert calls == ["AAA", "BBB"]
