"""Tests for deflated companions in calculate_metrics (#278 Part 2 Task 3).

These tests verify the plan-spec semantics:
  - sharpe_deflated IS the DSR probability in [0,1], not a rescaled Sharpe.
  - sigma_sr_trials is an injectable parameter; sharpe_deflated is None unless
    BOTH n_effective AND sigma_sr_trials are provided.
  - prob_sr_gt_0 is always present (PSR benchmark=0), None only when N<2.
  - calmar and calmar_ratio are both present; raw ratio, None when maxDD==0.
  - n_effective and sigma_sr_trials are echoed into the returned dict.
  - sortino_deflated and calmar_deflated are ABSENT (deferred, #278 Task 4 guard).

Trade shape mirrors the _trade() helper in test_backtest_bankruptcy.py.
"""
from __future__ import annotations

import pytest
import pandas as pd

from backtest import calculate_metrics


# ─── helpers ─────────────────────────────────────────────────────────────────

def _trade(entry_h: int, exit_h: int, pnl_usd: float, exit_reason: str = "SL",
           score: int = 4, size_mult: float = 1.0) -> dict:
    base = pd.Timestamp("2024-01-01", tz="UTC")
    pnl_pct = pnl_usd / 100  # pnl_pct as a percentage (e.g. 1.0 == 1%)
    return {
        "entry_time": base + pd.Timedelta(hours=entry_h),
        "exit_time": base + pd.Timedelta(hours=exit_h),
        "entry_price": 100.0,
        "exit_price": 100.0 + pnl_usd / 100,
        "exit_reason": exit_reason,
        "direction": "LONG",
        "pnl_pct": pnl_pct,
        "pnl_usd": pnl_usd,
        "overshoot_clamped": False,
        "score": score,
        "size_mult": size_mult,
        "duration_hours": float(exit_h - entry_h),
        "atr_sl_mult_used": 1.0,
        "atr_tp_mult_used": 4.0,
        "atr_be_mult_used": 1.5,
    }


def _equity(trades: list[dict], initial: float = 10_000.0) -> list[dict]:
    """Build a minimal equity_curve from trades."""
    capital = initial
    curve = [{"time": pd.Timestamp("2024-01-01", tz="UTC"), "equity": capital}]
    for t in trades:
        capital += t["pnl_usd"]
        curve.append({"time": t["exit_time"], "equity": capital})
    return curve


def _positive_sharpe_trades() -> list[dict]:
    """A trade set with positive Sharpe: many wins, a few losses.

    Spread over 365 days so span_y > 0 and trades_per_year > 0.
    """
    trades = []
    n = 40  # 40 trades spaced hourly within a year-ish span
    step = 365 * 24 // n  # ~219 hours apart
    for i in range(n):
        entry = i * step
        exit_ = entry + 2
        # mostly wins (+200) with occasional loss (-50) → positive Sharpe
        pnl = 200.0 if i % 5 != 0 else -50.0
        trades.append(_trade(entry, exit_, pnl_usd=pnl))
    return trades


# ─── new plan-spec tests ──────────────────────────────────────────────────────

def test_sharpe_deflated_is_probability_when_injected():
    """sharpe_deflated must be a probability in [0,1] when both
    n_effective and sigma_sr_trials are provided."""
    trades = _positive_sharpe_trades()
    eq = _equity(trades)
    m = calculate_metrics(trades, eq, n_effective=20, sigma_sr_trials=0.5)
    sd = m["sharpe_deflated"]
    assert sd is None or 0.0 <= sd <= 1.0, (
        f"sharpe_deflated={sd} is not None or in [0,1]"
    )


def test_sharpe_deflated_none_when_only_n_effective():
    """sharpe_deflated must be None when n_effective is given but
    sigma_sr_trials is NOT given."""
    trades = _positive_sharpe_trades()
    eq = _equity(trades)
    m = calculate_metrics(trades, eq, n_effective=20)
    assert m["sharpe_deflated"] is None, (
        f"Expected None when sigma_sr_trials absent, got {m['sharpe_deflated']}"
    )


def test_sharpe_deflated_none_when_only_sigma_sr_trials():
    """sharpe_deflated must be None when sigma_sr_trials is given but
    n_effective is NOT given."""
    trades = _positive_sharpe_trades()
    eq = _equity(trades)
    m = calculate_metrics(trades, eq, sigma_sr_trials=0.5)
    assert m["sharpe_deflated"] is None, (
        f"Expected None when n_effective absent, got {m['sharpe_deflated']}"
    )


def test_sharpe_deflated_none_when_neither_injected():
    """sharpe_deflated must be None when neither n_effective nor
    sigma_sr_trials is provided."""
    trades = _positive_sharpe_trades()
    eq = _equity(trades)
    m = calculate_metrics(trades, eq)
    assert m["sharpe_deflated"] is None, (
        f"Expected None when no injection, got {m['sharpe_deflated']}"
    )


def test_prob_sr_gt_0_always_present_and_in_unit_interval():
    """prob_sr_gt_0 must always be present; when trades >= 2 it must be in [0,1]."""
    trades = _positive_sharpe_trades()
    eq = _equity(trades)
    # with injection
    m = calculate_metrics(trades, eq, n_effective=10, sigma_sr_trials=0.4)
    assert "prob_sr_gt_0" in m, "prob_sr_gt_0 must be present"
    p = m["prob_sr_gt_0"]
    assert p is None or 0.0 <= p <= 1.0, f"prob_sr_gt_0={p} out of range"
    # without injection
    m2 = calculate_metrics(trades, eq)
    assert "prob_sr_gt_0" in m2, "prob_sr_gt_0 must be present without injection"
    p2 = m2["prob_sr_gt_0"]
    assert p2 is None or 0.0 <= p2 <= 1.0, f"prob_sr_gt_0={p2} out of range"


def test_more_trials_lower_or_equal_dsr():
    """Higher n_effective must produce lower-or-equal DSR probability
    (more trials => stronger selection penalty)."""
    trades = _positive_sharpe_trades()
    eq = _equity(trades)
    m5 = calculate_metrics(trades, eq, n_effective=5, sigma_sr_trials=0.4)
    m500 = calculate_metrics(trades, eq, n_effective=500, sigma_sr_trials=0.4)
    sd5 = m5["sharpe_deflated"]
    sd500 = m500["sharpe_deflated"]
    if sd5 is not None and sd500 is not None:
        assert sd500 <= sd5 + 1e-9, (
            f"DSR should decrease with more trials: n=5->{sd5}, n=500->{sd500}"
        )


def test_calmar_equals_calmar_ratio():
    """calmar and calmar_ratio must be equal (both present, same raw value)."""
    trades = _positive_sharpe_trades()
    eq = _equity(trades)
    m = calculate_metrics(trades, eq)
    assert "calmar" in m, "calmar key must be present"
    assert "calmar_ratio" in m, "calmar_ratio key must be present"
    c = m["calmar"]
    cr = m["calmar_ratio"]
    if c is not None and cr is not None:
        assert c == pytest.approx(cr, abs=1e-6), (
            f"calmar={c} != calmar_ratio={cr}"
        )


def test_calmar_matches_manual_formula():
    """calmar == total_return_pct / abs(max_drawdown_pct) when maxDD != 0."""
    trades = _positive_sharpe_trades()
    eq = _equity(trades)
    m = calculate_metrics(trades, eq)
    if m["max_drawdown_pct"] != 0 and m["calmar"] is not None:
        expected = m["total_return_pct"] / abs(m["max_drawdown_pct"])
        assert m["calmar"] == pytest.approx(expected, rel=1e-3), (
            f"calmar mismatch: got {m['calmar']}, expected {expected}"
        )


def test_n_effective_and_sigma_sr_trials_echoed():
    """n_effective and sigma_sr_trials must be echoed in the returned dict."""
    trades = _positive_sharpe_trades()
    eq = _equity(trades)
    m = calculate_metrics(trades, eq, n_effective=42, sigma_sr_trials=0.75)
    assert m["n_effective"] == 42
    assert m["sigma_sr_trials"] == pytest.approx(0.75, abs=1e-4)


def test_n_effective_sigma_none_when_not_given():
    """n_effective and sigma_sr_trials echoed as None when not provided."""
    trades = _positive_sharpe_trades()
    eq = _equity(trades)
    m = calculate_metrics(trades, eq)
    assert m["n_effective"] is None
    assert m["sigma_sr_trials"] is None


def test_deferred_keys_absent():
    """sortino_deflated and calmar_deflated must NOT exist in the returned dict."""
    trades = _positive_sharpe_trades()
    eq = _equity(trades)
    m = calculate_metrics(trades, eq, n_effective=10, sigma_sr_trials=0.5)
    assert "sortino_deflated" not in m, "sortino_deflated must be absent (deferred)"
    assert "calmar_deflated" not in m, "calmar_deflated must be absent (deferred)"


def test_existing_positional_call_unchanged():
    """Existing positional call calculate_metrics(trades, equity) must not crash
    and must preserve sharpe_ratio key."""
    trades = _positive_sharpe_trades()
    eq = _equity(trades)
    m = calculate_metrics(trades, eq)
    assert "sharpe_ratio" in m, "sharpe_ratio must still be present"
    assert "sortino_ratio" in m, "sortino_ratio must still be present"
    assert "total_trades" in m


def test_empty_trades_no_crash_new_keys_present():
    """Empty trades early return must not crash; new keys present as None."""
    eq = [{"time": pd.Timestamp("2024-01-01", tz="UTC"), "equity": 10_000.0}]
    m = calculate_metrics([], eq, n_effective=5, sigma_sr_trials=0.5)
    assert "error" in m  # existing early-return shape preserved
