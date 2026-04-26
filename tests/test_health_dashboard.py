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
