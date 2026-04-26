"""State machine: NORMAL → ALERT → REDUCED → PAUSED + manual override.

evaluate_state is pure: given metrics + current state + manual_override flag +
config, returns (new_state, reason)."""


CFG = {
    "min_trades_for_eval": 20,
    "alert_win_rate_threshold": 0.15,
    "reduce_pnl_window_days": 30,  # resolved elsewhere — evaluate_state just uses pnl_30d
    "reduce_size_factor": 0.5,     # unused by evaluate_state
    "pause_months_consecutive": 3,
    "auto_recovery_enabled": True,
}


def _metrics(total=50, wr=0.5, pnl_30d=500.0, months_neg=0):
    return {
        "trades_count_total": total,
        "win_rate_20_trades": wr,
        "pnl_30d": pnl_30d,
        "pnl_by_month": {},
        "months_negative_consecutive": months_neg,
    }


def test_healthy_symbol_stays_normal():
    from health import evaluate_state
    new, reason = evaluate_state(_metrics(), "NORMAL", False, CFG)
    assert new == "NORMAL"
    assert reason == "healthy"


def test_low_win_rate_transitions_to_alert():
    from health import evaluate_state
    new, reason = evaluate_state(_metrics(wr=0.10), "NORMAL", False, CFG)
    assert new == "ALERT"
    assert reason == "wr_below_threshold"


def test_negative_pnl_30d_transitions_to_reduced():
    from health import evaluate_state
    new, reason = evaluate_state(_metrics(wr=0.5, pnl_30d=-100.0), "ALERT", False, CFG)
    assert new == "REDUCED"
    assert reason == "pnl_neg_30d"


def test_three_months_negative_transitions_to_paused():
    from health import evaluate_state
    new, reason = evaluate_state(_metrics(months_neg=3), "REDUCED", False, CFG)
    assert new == "PAUSED"
    assert reason == "3mo_consec_neg"


def test_rule_order_paused_beats_reduced_beats_alert():
    """When multiple rules fire, the most severe wins."""
    from health import evaluate_state
    new, reason = evaluate_state(
        _metrics(wr=0.05, pnl_30d=-500, months_neg=3), "NORMAL", False, CFG,
    )
    assert new == "PAUSED"


def test_cold_start_holds_state_unchanged():
    """If trades_count_total < min_trades, state is locked to its current value."""
    from health import evaluate_state
    new, reason = evaluate_state(
        _metrics(total=10, wr=0.0, pnl_30d=-1000, months_neg=3), "NORMAL", False, CFG,
    )
    assert new == "NORMAL"
    assert reason == "insufficient_data"


def test_auto_recovery_from_alert_to_normal():
    """Once metrics are healthy again, ALERT → NORMAL automatically."""
    from health import evaluate_state
    new, reason = evaluate_state(_metrics(wr=0.5), "ALERT", False, CFG)
    assert new == "NORMAL"
    assert reason == "auto_recovery"


def test_auto_recovery_disabled_by_config():
    """If auto_recovery_enabled=False, non-healthy states hold until manual intervention."""
    from health import evaluate_state
    cfg = dict(CFG, auto_recovery_enabled=False)
    new, reason = evaluate_state(_metrics(wr=0.5), "ALERT", False, cfg)
    assert new == "ALERT"
    assert reason == "auto_recovery_disabled"


def test_manual_override_respected_on_normal_with_good_metrics():
    """A reactivated (manual_override=1) symbol with healthy metrics stays NORMAL
    (auto-recovery path but also fine; override is informational here)."""
    from health import evaluate_state
    new, reason = evaluate_state(_metrics(wr=0.5), "NORMAL", True, CFG)
    assert new == "NORMAL"


def test_manual_override_expires_if_a_severe_rule_fires():
    """Manual override survives minor dips but NOT a fresh PAUSED-triggering condition."""
    from health import evaluate_state
    new, reason = evaluate_state(
        _metrics(months_neg=3), "NORMAL", True, CFG,
    )
    assert new == "PAUSED"
    assert reason == "3mo_consec_neg"


def test_win_rate_exactly_at_threshold_is_healthy():
    """wr == alert_win_rate_threshold is NOT ALERT — operator is strict <."""
    from health import evaluate_state
    new, _ = evaluate_state(_metrics(wr=0.15), "NORMAL", False, CFG)
    assert new == "NORMAL"


def test_win_rate_one_tick_below_threshold_is_alert():
    """wr just below threshold triggers ALERT."""
    from health import evaluate_state
    new, reason = evaluate_state(_metrics(wr=0.1499), "NORMAL", False, CFG)
    assert new == "ALERT"
    assert reason == "wr_below_threshold"


def test_invalid_current_state_raises_value_error():
    """Garbage-in/garbage-out is not OK: bad state names must raise early."""
    import pytest
    from health import evaluate_state
    with pytest.raises(ValueError, match="unknown current_state"):
        evaluate_state(_metrics(), "DISABLED", False, CFG)


# ── B5: PROBATION branch ───────────────────────────────────────────────────


CFG_PROB = {
    "min_trades_for_eval": 20,
    "alert_win_rate_threshold": 0.15,
    "reduce_pnl_window_days": 30,
    "reduce_size_factor": 0.5,
    "pause_months_consecutive": 3,
    "auto_recovery_enabled": True,
    "v2": {
        "probation": {
            "regression_wr_threshold": 0.10,
            "regression_window_trades": 10,
        },
    },
}


def _metrics_prob(total=50, wr20=0.5, wr10=0.5, pnl_30d=500.0,
                   months_neg=0, trades_remaining=5):
    return {
        "trades_count_total": total,
        "win_rate_20_trades": wr20,
        "win_rate_10_trades": wr10,
        "pnl_30d": pnl_30d,
        "pnl_by_month": {},
        "months_negative_consecutive": months_neg,
        "probation_trades_remaining": trades_remaining,
    }


def test_probation_severe_regression_returns_to_paused():
    """In PROBATION + WR<10% in last 10 trades + ≥10 closed → PAUSED."""
    from health import evaluate_state
    new, reason = evaluate_state(
        _metrics_prob(total=50, wr10=0.05, trades_remaining=8),
        "PROBATION", False, CFG_PROB,
    )
    assert new == "PAUSED"
    assert reason == "regression_severe"


def test_probation_completes_when_counter_zero():
    """In PROBATION + counter == 0 + healthy WR → NORMAL."""
    from health import evaluate_state
    new, reason = evaluate_state(
        _metrics_prob(total=50, wr10=0.5, trades_remaining=0),
        "PROBATION", False, CFG_PROB,
    )
    assert new == "NORMAL"
    assert reason == "probation_complete"


def test_probation_holds_when_in_progress():
    """In PROBATION + counter > 0 + healthy → hold PROBATION."""
    from health import evaluate_state
    new, reason = evaluate_state(
        _metrics_prob(total=50, wr10=0.5, trades_remaining=5),
        "PROBATION", False, CFG_PROB,
    )
    assert new == "PROBATION"
    assert reason == "probation_in_progress"


def test_probation_skips_regression_check_with_few_trades():
    """In PROBATION + WR<10% but < 10 trades → skip regression check (insufficient evidence)."""
    from health import evaluate_state
    new, reason = evaluate_state(
        _metrics_prob(total=5, wr10=0.0, trades_remaining=10),
        "PROBATION", False, CFG_PROB,
    )
    # Insufficient data branch fires first (total < min_trades_for_eval=20)
    assert new == "PROBATION"
    assert reason == "insufficient_data"


def test_probation_corrupt_null_counter_treated_as_zero():
    """If probation_trades_remaining is missing (corrupt row), exit to NORMAL on next eval."""
    from health import evaluate_state
    metrics = _metrics_prob(total=50, wr10=0.5, trades_remaining=5)
    metrics.pop("probation_trades_remaining")
    new, reason = evaluate_state(metrics, "PROBATION", False, CFG_PROB)
    assert new == "NORMAL"
    assert reason == "probation_complete"


def test_valid_states_includes_probation():
    """VALID_STATES tuple now includes PROBATION."""
    from health import VALID_STATES
    assert "PROBATION" in VALID_STATES
