"""Regression: backtest's _close_position must CAP single-trade overshoot.

The simulator computes per-symbol PnL with a per-symbol initial capital. The
R-multiple formula `pnl_usd = risk_amount * (pnl_pct / sl_pct_actual)` is
unbounded on the TIME_LIMIT exit branch in `simulate_strategy`
(`exit_price = float(bar["close"])`) and on gap-through-SL exits, where the
realized exit price can be many multiples of the SL distance from entry.

The cap below enforces the invariant:

    |pnl_usd| ≤ K × risk_amount = K × max(0, capital) × RISK_PER_TRADE × size_mult

Per-trade overshoot is bounded relative to *current* capital (after the
existing `effective_capital = max(0.0, capital)` floor), NOT relative to
initial capital. Without this cap, a single trade with tight SL can produce
|pnl_usd| many multiples of the per-symbol initial capital allocation —
breaking the floor invariant the calling simulator relies on.

A pre-holdout regime re-tune sweep surfaced this on PENDLEUSDT: cumulative
single-symbol PnL of $-1,702,401 vs $10K initial allocation (a 170× ratio of
per-symbol cumulative PnL to allocation, NOT a per-trade R-multiple — that
ratio breaks down per-trade into many trades each in the multi-hundred to
multi-thousand R range, which is what the cap below bounds). Same mechanism
manifests at smaller scale on RUNE, AVAX-under-BYPASS, JUP-under-BYPASS.

K=10 is rule-derived: a 10× SL move is already absurd; a real trader exits
manually before that, so backtest pnl beyond this multiple is unrealistic
execution. NEVER tuned to data — that revives the leakage pattern Caveat #1
fixes for ATR multipliers. Documented in CLAUDE.md "Caveats heredados — A.4
(#250) MUST honor" #4 (per-symbol vs portfolio aggregation gap).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Fixture helpers. score=2 mirrors what an upstream caller emits for
# size_mult=1.0; _close_position uses size_mult directly.
# ─────────────────────────────────────────────────────────────────────────────
def _build_long_position(size_mult: float = 1.0):
    return {
        "entry_price": 100.0,
        "entry_time": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "score": 2,
        "direction": "LONG",
        "sl": 99.0,            # 1% SL distance
        "sl_orig": 99.0,
        "tp": 110.0,
        "size_mult": size_mult,
        "be_threshold": None,
    }


def _build_short_position(size_mult: float = 1.0):
    return {
        "entry_price": 100.0,
        "entry_time": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "score": 2,
        "direction": "SHORT",
        "sl": 101.0,           # 1% SL distance (above entry for SHORT)
        "sl_orig": 101.0,
        "tp": 90.0,
        "size_mult": size_mult,
        "be_threshold": None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Within-cap tests: ratios in [-10, +10] must produce unmodified pnl_usd
# AND must NOT set overshoot_clamped.
# ─────────────────────────────────────────────────────────────────────────────


def test_long_within_cap_positive_unchanged():
    """LONG with ratio = +5 (TP-style win, well within cap) → pnl_usd = +$500."""
    from backtest import _close_position

    trade = _close_position(
        _build_long_position(),
        exit_price=105.0,      # +5% pnl_pct, ratio = +5
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="TP",
        capital=10_000.0,
    )
    assert trade["pnl_usd"] == pytest.approx(500.0, abs=0.01)
    assert trade["overshoot_clamped"] is False


def test_long_within_cap_negative_unchanged():
    """LONG with ratio = -5 (5× SL overshoot, within cap) → pnl_usd = -$500."""
    from backtest import _close_position

    trade = _close_position(
        _build_long_position(),
        exit_price=95.0,       # -5% pnl_pct, ratio = -5
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="TIME_LIMIT",
        capital=10_000.0,
    )
    assert trade["pnl_usd"] == pytest.approx(-500.0, abs=0.01)
    assert trade["overshoot_clamped"] is False


def test_long_just_under_cap_positive_unchanged():
    """LONG with ratio = +9.99 (just under cap) → pnl_usd = +$999, NOT clamped."""
    from backtest import _close_position

    trade = _close_position(
        _build_long_position(),
        exit_price=109.99,     # +9.99% pnl_pct, ratio = +9.99
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="TIME_LIMIT",
        capital=10_000.0,
    )
    assert trade["pnl_usd"] == pytest.approx(999.0, abs=0.01)
    assert trade["overshoot_clamped"] is False


def test_long_just_under_cap_negative_unchanged():
    """LONG with ratio = -9.99 (just under cap) → pnl_usd = -$999, NOT clamped."""
    from backtest import _close_position

    trade = _close_position(
        _build_long_position(),
        exit_price=90.01,      # -9.99% pnl_pct, ratio = -9.99
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="TIME_LIMIT",
        capital=10_000.0,
    )
    assert trade["pnl_usd"] == pytest.approx(-999.0, abs=0.01)
    assert trade["overshoot_clamped"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Above-cap tests: ratios outside [-10, +10] must be clamped to ±$1000
# AND must set overshoot_clamped=True.
# ─────────────────────────────────────────────────────────────────────────────


def test_long_just_over_cap_negative_clamped():
    """LONG with ratio = -10.01 (just over cap, loss side) → clamped to -$1000."""
    from backtest import _close_position

    trade = _close_position(
        _build_long_position(),
        exit_price=89.99,      # -10.01% pnl_pct, ratio = -10.01
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="TIME_LIMIT",
        capital=10_000.0,
    )
    assert trade["pnl_usd"] == pytest.approx(-1000.0, abs=0.01)
    assert trade["overshoot_clamped"] is True


def test_long_just_over_cap_positive_clamped():
    """LONG with ratio = +10.01 (just over cap, win side) → clamped to +$1000."""
    from backtest import _close_position

    trade = _close_position(
        _build_long_position(),
        exit_price=110.01,     # +10.01% pnl_pct, ratio = +10.01
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="TIME_LIMIT",
        capital=10_000.0,
    )
    assert trade["pnl_usd"] == pytest.approx(1000.0, abs=0.01)
    assert trade["overshoot_clamped"] is True


def test_long_far_above_cap_negative_clamped():
    """LONG with ratio = -50 (5× over cap, catastrophic loss) → clamped to -$1000.

    sl_pct_actual = 1%, pnl_pct = -50% → ratio = -50, clamped to -10."""
    from backtest import _close_position

    trade = _close_position(
        _build_long_position(),
        exit_price=50.0,       # -50% pnl_pct
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="TIME_LIMIT",
        capital=10_000.0,
    )
    assert trade["pnl_usd"] == pytest.approx(-1000.0, abs=0.01)
    assert trade["overshoot_clamped"] is True


def test_long_far_above_cap_positive_clamped():
    """LONG with ratio = +50 (impossible-trader-execution win) → clamped to +$1000."""
    from backtest import _close_position

    trade = _close_position(
        _build_long_position(),
        exit_price=150.0,      # +50% pnl_pct, ratio = +50
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="TIME_LIMIT",
        capital=10_000.0,
    )
    assert trade["pnl_usd"] == pytest.approx(1000.0, abs=0.01)
    assert trade["overshoot_clamped"] is True


# ─────────────────────────────────────────────────────────────────────────────
# SHORT mirror: cap applies symmetrically across direction.
# ─────────────────────────────────────────────────────────────────────────────


def test_short_far_above_cap_negative_clamped():
    """SHORT with ratio = -50 (price gapped up, overshoot SL) → clamped to -$1000."""
    from backtest import _close_position

    trade = _close_position(
        _build_short_position(),
        # SHORT pnl_pct = (entry - exit) / entry × 100 = (100 - 150)/100 × 100 = -50%
        # sl_pct_actual = (sl - entry) / entry × 100 = 1%
        # ratio = -50, clamped to -10 → pnl_usd = -$1000
        exit_price=150.0,
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="TIME_LIMIT",
        capital=10_000.0,
    )
    assert trade["pnl_usd"] == pytest.approx(-1000.0, abs=0.01)
    assert trade["overshoot_clamped"] is True


def test_short_far_above_cap_positive_clamped():
    """SHORT with ratio = +50 (price collapsed, big win) → clamped to +$1000."""
    from backtest import _close_position

    trade = _close_position(
        _build_short_position(),
        # SHORT pnl_pct = (100 - 50)/100 × 100 = +50%, ratio = +50, clamped to +10
        exit_price=50.0,
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="TIME_LIMIT",
        capital=10_000.0,
    )
    assert trade["pnl_usd"] == pytest.approx(1000.0, abs=0.01)
    assert trade["overshoot_clamped"] is True


# ─────────────────────────────────────────────────────────────────────────────
# size_mult × cap interaction. Premium-tier (1.5×) and reduced (0.5×) trades
# at far-above-cap ratios MUST clamp to ±size_mult × $1000. Locks the cap-
# scales-with-sizing invariant (premium tier worst case motivated the PR).
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("size_mult,ratio,exit_price,expected_pnl_usd", [
    (0.5, -50, 50.0, -500.0),    # 0.5× sizing, -50% pnl_pct → clamp to -10×$50 = -$500
    (0.5, +50, 150.0, 500.0),    # 0.5× sizing, +50% pnl_pct → clamp to +10×$50 = +$500
    (1.5, -50, 50.0, -1500.0),   # 1.5× sizing (premium), -50% → clamp to -10×$150 = -$1500
    (1.5, +50, 150.0, 1500.0),   # 1.5× sizing (premium), +50% → clamp to +10×$150 = +$1500
])
def test_size_mult_at_extreme_ratios_clamps_proportionally(
    size_mult, ratio, exit_price, expected_pnl_usd
):
    """Cap × sizing interaction: clamped pnl_usd MUST scale with size_mult."""
    from backtest import _close_position

    trade = _close_position(
        _build_long_position(size_mult=size_mult),
        exit_price=exit_price,
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="TIME_LIMIT",
        capital=10_000.0,
    )
    assert trade["pnl_usd"] == pytest.approx(expected_pnl_usd, abs=0.01)
    assert trade["overshoot_clamped"] is True


# ─────────────────────────────────────────────────────────────────────────────
# NaN propagation guards. NaN exit_price MUST NOT silently propagate to
# pnl_usd / pnl_pct. inf exit_price IS allowed; clamp handles it bounded.
# ─────────────────────────────────────────────────────────────────────────────


def test_nan_exit_price_does_not_phantom_profit():
    """NaN exit_price → pnl_pct = NaN → NaN guard → pnl_usd = 0.0, NOT phantom.

    Without the guard, NaN would propagate through min/max/multiply into
    pnl_usd = NaN, then into capital += NaN = NaN, breaking all subsequent
    `if capital <= 0` comparisons (NaN comparisons evaluate False).

    Sister-variable check: the trade dict's pnl_pct is ALSO zeroed (not left
    as NaN), since pnl_pct flows directly into Sharpe / Sortino aggregation
    in calculate_metrics."""
    from backtest import _close_position

    trade = _close_position(
        _build_long_position(),
        exit_price=float("nan"),
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="TIME_LIMIT",
        capital=10_000.0,
    )
    assert trade["pnl_usd"] == 0.0
    assert trade["pnl_pct"] == 0.0
    assert trade["overshoot_clamped"] is False


def test_inf_exit_price_clamps_correctly():
    """+inf exit_price (LONG) → +inf pnl_pct → ratio = +inf → clamp to +10 → +$1000.

    Sign-correct: positive overshoot clamps to +K, not -K.
    Verifies the clamp's `min(K, +inf) = K` then `max(-K, K) = K` arithmetic."""
    from backtest import _close_position

    trade = _close_position(
        _build_long_position(),
        exit_price=float("inf"),
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="TIME_LIMIT",
        capital=10_000.0,
    )
    assert trade["pnl_usd"] == pytest.approx(1000.0, abs=0.01)
    assert trade["overshoot_clamped"] is True


def test_neg_inf_exit_price_clamps_correctly():
    """-inf exit_price (LONG) → -inf pnl_pct → ratio = -inf → clamp to -10 → -$1000.

    Counterpart to the +inf test; verifies the clamp's
    `min(K, -inf) = -inf` then `max(-K, -inf) = -K` arithmetic."""
    from backtest import _close_position

    trade = _close_position(
        _build_long_position(),
        exit_price=float("-inf"),
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="TIME_LIMIT",
        capital=10_000.0,
    )
    assert trade["pnl_usd"] == pytest.approx(-1000.0, abs=0.01)
    assert trade["overshoot_clamped"] is True


def test_long_inf_inputs_route_to_malformed_sl_guard():
    """LONG with sl_orig=+inf → sl_pct_actual = (entry - +inf)/entry × 100 = -inf.

    -inf > 0 is False, so this LONG fixture routes to the **else (malformed-SL)
    branch**, NOT the inner NaN guard. Documents the actual route taken; the
    SHORT-fixture mirror (`test_short_inf_pnl_pct_inf_sl_pct_routes_through_inner_guard`)
    is the one that exercises the inner guard.

    Both routes produce the same conservative output: pnl_usd = 0.0,
    pnl_pct = 0.0, overshoot_clamped = False."""
    from backtest import _close_position

    position = _build_long_position()
    position["sl"] = float("inf")
    position["sl_orig"] = float("inf")
    trade = _close_position(
        position,
        exit_price=float("inf"),
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="TIME_LIMIT",
        capital=10_000.0,
    )
    assert trade["pnl_usd"] == 0.0
    assert trade["pnl_pct"] == 0.0
    assert trade["overshoot_clamped"] is False


def test_short_inf_pnl_pct_inf_sl_pct_routes_through_inner_guard():
    """SHORT inf/inf actually exercises the inner NaN guard (post-division
    raw_ratio = NaN). Verifies the inner guard is reached, not the else branch.

    Trace:
      - SHORT sl_pct_actual = (sl_orig - entry_price) / entry_price × 100
                            = (+inf - 100) / 100 × 100 = +inf
      - +inf > 0 is True → enter the elif branch (clamp logic)
      - SHORT pnl_pct = (entry_price - exit_price) / entry_price × 100
                      = (100 - (-inf)) / 100 × 100 = +inf
      - raw_ratio = +inf / +inf = NaN
      - math.isnan(raw_ratio) catches it → inner NaN guard fires"""
    from backtest import _close_position

    position = _build_short_position()
    position["sl"] = float("inf")
    position["sl_orig"] = float("inf")
    trade = _close_position(
        position, exit_price=float("-inf"),
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="TIME_LIMIT", capital=10_000.0,
    )
    assert trade["pnl_usd"] == 0.0
    assert trade["pnl_pct"] == 0.0
    assert trade["overshoot_clamped"] is False


# ─────────────────────────────────────────────────────────────────────────────
# sl_pct_actual NaN/inf paths.
# ─────────────────────────────────────────────────────────────────────────────


def test_nan_sl_orig_routes_to_else_branch():
    """sl_orig = NaN → sl_pct_actual = NaN → `NaN > 0` is False → else branch.

    The existing inverted-SL guard catches this via the `sl_pct_actual > 0`
    test; pnl_usd = 0.0. No new code needed for this path — verifying the
    existing guard still handles it after the NaN refactor."""
    from backtest import _close_position

    position = _build_long_position()
    position["sl"] = float("nan")
    position["sl_orig"] = float("nan")
    trade = _close_position(
        position,
        exit_price=95.0,       # finite exit, so pnl_pct is finite
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="SL",      # incidental; the path under test is inverted-SL
        capital=10_000.0,
    )
    assert trade["pnl_usd"] == 0.0
    assert trade["overshoot_clamped"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Regression: cap must NOT alter pre-existing guarded paths.
# ─────────────────────────────────────────────────────────────────────────────


def test_negative_capital_still_zero_pnl():
    """capital ≤ 0 → effective_capital = 0 → risk_amount = 0 → pnl_usd = 0
    regardless of raw ratio. The cap is *moot* (not binding) when
    risk_amount = 0.

    AND-gate semantic on overshoot_clamped: only True when the cap actually
    bound pnl_usd below its raw R-multiple value. With risk_amount = 0,
    pnl_usd is 0 from the floor, NOT from the cap — overshoot_clamped is
    therefore False."""
    from backtest import _close_position

    trade = _close_position(
        _build_long_position(),
        exit_price=50.0,       # ratio = -50, would clamp to -10 if capital > 0
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="TIME_LIMIT",  # exit_price=50 with sl=99 = gap-through-SL/time-limit, not SL
        capital=-5000.0,       # capital is already negative
    )
    # effective_capital = max(0, -5000) = 0 → risk_amount = 0 → pnl_usd = 0
    assert trade["pnl_usd"] == 0.0
    assert trade["overshoot_clamped"] is False  # AND-gate: cap not binding


def test_zero_capital_still_zero_pnl():
    """capital == 0 → effective_capital = 0 → pnl_usd = 0. Cap is moot."""
    from backtest import _close_position

    trade = _close_position(
        _build_long_position(),
        exit_price=50.0,
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="SL",
        capital=0.0,
    )
    assert trade["pnl_usd"] == 0.0


def test_inverted_sl_still_zero_pnl():
    """Inverted SL (sl_pct_actual ≤ 0 in else branch) → pnl_usd = 0 unchanged.
    Cap path is in the `if sl_pct_actual > 0` branch only; phantom-profit guard
    in the else branch is preserved byte-identical."""
    from backtest import _close_position

    position = _build_long_position()
    position["sl"] = 101.0           # ⚠ ABOVE entry — inverted (the pre-fix bug)
    position["sl_orig"] = 101.0
    trade = _close_position(
        position,
        exit_price=101.0,
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="SL",
        capital=10_000.0,
    )
    # Phantom-profit guard (else branch) returns pnl_usd = 0; cap not reached.
    assert trade["pnl_usd"] == 0.0
    assert trade["overshoot_clamped"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Type stability: pnl_usd MUST be float in all branches, never int.
# ─────────────────────────────────────────────────────────────────────────────


def test_else_branch_pnl_usd_is_float_type():
    """The else branch (inverted SL) MUST set pnl_usd = 0.0 (float), not 0
    (int). Heterogeneous schemas in the trade dict cause int/float drift in
    pandas DataFrame dtypes downstream."""
    from backtest import _close_position

    position = _build_long_position()
    position["sl"] = 101.0
    position["sl_orig"] = 101.0
    trade = _close_position(
        position,
        exit_price=101.0,
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        exit_reason="SL",
        capital=10_000.0,
    )
    # round(0.0, 2) preserves float type; round(0, 2) returns int.
    assert isinstance(trade["pnl_usd"], float), (
        f"pnl_usd type drift: got {type(trade['pnl_usd']).__name__}, "
        f"expected float. Else branch must set pnl_usd = 0.0, not 0."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Constant invariant: K=10 is the rule-derived value locked in spec discussion.
# Catches accidental tweak to a data-derived value (which would revive leakage).
# ─────────────────────────────────────────────────────────────────────────────


def test_max_overshoot_ratio_is_ten():
    """The cap value MUST stay at 10 (rule-derived). If this test fails, someone
    tuned K to data — that revives the same leakage pattern Caveat #1 fixes for
    ATR multipliers. Treat any change to MAX_OVERSHOOT_RATIO as requiring its
    own pre-registration with explicit external review."""
    from backtest import MAX_OVERSHOOT_RATIO

    assert MAX_OVERSHOOT_RATIO == 10, (
        f"MAX_OVERSHOOT_RATIO changed from rule-derived 10 to {MAX_OVERSHOOT_RATIO}. "
        f"Any change must be pre-registered (rule-derived only — never tuned to data)."
    )
    # Type parity with INITIAL_CAPITAL / RISK_PER_TRADE (both float):
    assert isinstance(MAX_OVERSHOOT_RATIO, float), (
        f"MAX_OVERSHOOT_RATIO must be float for type parity with INITIAL_CAPITAL "
        f"and RISK_PER_TRADE; got {type(MAX_OVERSHOOT_RATIO).__name__}."
    )


# ─────────────────────────────────────────────────────────────────────────────
# calculate_metrics aggregation: clamped_trade_count must reflect the
# count of closed trades where the cap bound the R-multiple.
# ─────────────────────────────────────────────────────────────────────────────


def test_calculate_metrics_clamped_trade_count_aggregation():
    """calculate_metrics must surface clamped_trade_count summing
    overshoot_clamped flags across closed (non-OPEN) trades.

    Constructed via synthetic trade dicts mirroring the _close_position
    output schema; bypasses the simulator to isolate the aggregation logic."""
    from backtest import calculate_metrics

    def _t(day_offset, hour_offset=0):
        return datetime(2024, 1, 1 + day_offset, hour_offset, tzinfo=timezone.utc)

    # Trade fixtures: pnl_pct aligned with pnl_usd sign+magnitude so the
    # synthetic trade dict mirrors what _close_position would actually emit.
    def _make(i, exit_reason, overshoot_clamped, pnl_usd, pnl_pct):
        return {
            "entry_time": _t(i, 0), "exit_time": _t(i, 1),
            "entry_price": 100.0, "exit_price": 105.0,
            "direction": "LONG", "pnl_pct": pnl_pct, "pnl_usd": pnl_usd,
            "score": 2, "size_mult": 1.0, "duration_hours": 1.0,
            "exit_reason": exit_reason, "overshoot_clamped": overshoot_clamped,
        }

    trades = [
        _make(0, "TP", False, 500.0, 5.0),
        _make(1, "SL", False, -100.0, -1.0),
        _make(2, "TIME_LIMIT", True, -1000.0, -10.0),
        _make(3, "TIME_LIMIT", True, 1000.0, 10.0),
        # OPEN trade must be excluded from the clamped count:
        _make(4, "OPEN", True, 1000.0, 10.0),
    ]
    equity_curve = [
        {"time": _t(0, 0), "equity": 10_000.0},
        {"time": _t(4, 1), "equity": 11_400.0},
    ]
    metrics = calculate_metrics(trades, equity_curve)
    # 4 closed trades total; 2 with overshoot_clamped=True (the OPEN trade
    # is excluded by `exit_reason != "OPEN"` filter).
    assert metrics["clamped_trade_count"] == 2
    assert isinstance(metrics["clamped_trade_count"], int)


def test_calculate_metrics_clamped_trade_count_zero_when_no_clamp():
    """When no trade has overshoot_clamped=True, clamped_trade_count == 0."""
    from backtest import calculate_metrics

    def _t(day_offset, hour_offset=0):
        return datetime(2024, 1, 1 + day_offset, hour_offset, tzinfo=timezone.utc)

    trades = [
        {
            "entry_time": _t(0, 0), "exit_time": _t(0, 1),
            "entry_price": 100.0, "exit_price": 105.0,
            "exit_reason": "TP", "direction": "LONG",
            "pnl_pct": 5.0, "pnl_usd": 500.0,
            "score": 2, "size_mult": 1.0, "duration_hours": 1.0,
            "overshoot_clamped": False,
        },
        {
            "entry_time": _t(2, 0), "exit_time": _t(2, 1),
            "entry_price": 100.0, "exit_price": 99.0,
            "exit_reason": "SL", "direction": "LONG",
            "pnl_pct": -1.0, "pnl_usd": -100.0,
            "score": 2, "size_mult": 1.0, "duration_hours": 1.0,
            "overshoot_clamped": False,
        },
    ]
    equity_curve = [
        {"time": _t(0, 0), "equity": 10_000.0},
        {"time": _t(2, 1), "equity": 10_400.0},
    ]
    metrics = calculate_metrics(trades, equity_curve)
    assert metrics["clamped_trade_count"] == 0


def test_calculate_metrics_handles_legacy_trades_without_flag():
    """Backwards-compatible default: trades without `overshoot_clamped` key
    (e.g. fixtures predating this PR) treat the missing key as False via
    `t.get("overshoot_clamped", False)`. clamped_trade_count == 0 for legacy
    trade lists."""
    from backtest import calculate_metrics

    legacy_trade = {
        "entry_time": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "exit_time": datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        "entry_price": 100.0, "exit_price": 105.0,
        "exit_reason": "TP", "direction": "LONG", "pnl_pct": 5.0,
        "pnl_usd": 500.0, "score": 2, "size_mult": 1.0, "duration_hours": 1.0,
        # NO overshoot_clamped key — legacy
    }
    equity_curve = [
        {"time": datetime(2024, 1, 1, tzinfo=timezone.utc), "equity": 10_000.0},
        {"time": datetime(2024, 1, 1, 1, tzinfo=timezone.utc), "equity": 10_500.0},
    ]
    metrics = calculate_metrics([legacy_trade], equity_curve)
    assert metrics["clamped_trade_count"] == 0


def test_calculate_metrics_empty_trades_returns_clamped_zero():
    """Empty-trades early-return path must include `clamped_trade_count: 0` so
    downstream consumers can read the field unconditionally without KeyError."""
    from backtest import calculate_metrics

    metrics = calculate_metrics([], [])
    assert metrics.get("clamped_trade_count") == 0
    assert metrics.get("error") == "No trades generated"


# ─────────────────────────────────────────────────────────────────────────────
# calculate_metrics ZeroDivisionError regression. Same-day fixtures must not
# raise on the trades_per_year computation in the Sharpe branch.
# ─────────────────────────────────────────────────────────────────────────────


def test_calculate_metrics_same_day_fixtures_dont_raise():
    """Same-day fixtures (`(exit_time - entry_time).days == 0`) must not
    trigger ZeroDivisionError at the trades_per_year computation. The span_y
    guard ensures Sharpe falls back to 0 when annualization is undefined,
    matching the legacy `len(closed) > 1` else branch."""
    from backtest import calculate_metrics

    same_day = datetime(2024, 1, 1, tzinfo=timezone.utc)
    trades = [
        {
            "entry_time": same_day, "exit_time": same_day,
            "entry_price": 100.0, "exit_price": 105.0,
            "exit_reason": "TP", "direction": "LONG",
            "pnl_pct": 5.0, "pnl_usd": 500.0,
            "score": 2, "size_mult": 1.0, "duration_hours": 0.0,
            "overshoot_clamped": False,
        },
        {
            "entry_time": same_day, "exit_time": same_day,
            "entry_price": 100.0, "exit_price": 99.0,
            "exit_reason": "SL", "direction": "LONG",
            "pnl_pct": -1.0, "pnl_usd": -100.0,
            "score": 2, "size_mult": 1.0, "duration_hours": 0.0,
            "overshoot_clamped": False,
        },
    ]
    equity_curve = [
        {"time": same_day, "equity": 10_000.0},
        {"time": same_day, "equity": 10_400.0},
    ]
    # Must not raise ZeroDivisionError.
    metrics = calculate_metrics(trades, equity_curve)
    # Annualization undefined when window is < 1 day → Sharpe falls back to 0,
    # consistent with the existing `len(closed) > 1` else branch.
    assert metrics["sharpe_ratio"] == 0
    assert metrics["clamped_trade_count"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# _apply_costs_to_trade defensive guard: same NaN-comparison-class bug as
# the _close_position guard. NaN entry_notional must short-circuit rather
# than corrupting cost computation.
# ─────────────────────────────────────────────────────────────────────────────


def test_apply_costs_to_trade_skips_on_nan_entry_notional():
    """`entry_notional <= 0` evaluates False for NaN (NaN comparisons return
    False); that form would NOT short-circuit and would propagate NaN through
    the cost computation. The current `not (entry_notional > 0)` form flips
    NaN to True (since `NaN > 0` is False) and returns early."""
    from backtest import _apply_costs_to_trade

    trade = {"pnl_usd": -100.0, "pnl_pct": -1.0}
    position = {
        "entry_notional_usd": float("nan"),
        "entry_price": 100.0,
        "entry_liquidity_per_min": 10_000.0,
    }
    cost_calls = {"count": 0}

    def fake_cost_fn(*args, **kwargs):
        cost_calls["count"] += 1
        return {"total_cost_usd": 100.0, "total_cost_bps": 50.0}

    _apply_costs_to_trade(
        trade, position, exit_price_actual=101.0,
        exit_liquidity_per_min=10_000.0,
        compute_trade_costs_fn=fake_cost_fn,
        tier_params=None,
        enable_slippage=True, enable_spread=True, enable_fees=True,
    )
    assert cost_calls["count"] == 0, (
        "_apply_costs_to_trade should short-circuit on NaN entry_notional, "
        "not call compute_trade_costs."
    )
    # Trade dict unchanged: cost mutations did not occur
    assert "gross_pnl_usd" not in trade
    assert "total_cost_usd" not in trade
    assert trade["pnl_usd"] == -100.0
    assert trade["pnl_pct"] == -1.0


def test_apply_costs_to_trade_skips_on_zero_entry_notional():
    """Zero entry_notional short-circuits: `not (0 > 0)` is `not False` is
    True → return. Same outcome as the legacy `entry_notional <= 0` guard
    for the zero case."""
    from backtest import _apply_costs_to_trade

    trade = {"pnl_usd": -100.0, "pnl_pct": -1.0}
    position = {"entry_notional_usd": 0.0, "entry_price": 100.0}
    cost_calls = {"count": 0}

    def fake_cost_fn(*args, **kwargs):
        cost_calls["count"] += 1
        return {"total_cost_usd": 100.0, "total_cost_bps": 50.0}

    _apply_costs_to_trade(
        trade, position, exit_price_actual=101.0,
        exit_liquidity_per_min=10_000.0,
        compute_trade_costs_fn=fake_cost_fn,
        tier_params=None,
        enable_slippage=True, enable_spread=True, enable_fees=True,
    )
    assert cost_calls["count"] == 0
    assert "gross_pnl_usd" not in trade
