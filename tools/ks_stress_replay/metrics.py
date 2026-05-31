"""Pass 3: gate evaluation on the DD/P&L frontier.

Pre-registered gate (spec 2026-05-31, §1, §6):
  STRONG  - some v2 slider Pareto-dominates v1 (|DD| <= and PnL >=).
  PASS    - some v2 slider cuts |DD| by >=3pp absolute OR >=15% relative
            AND keeps PnL within 10% of v1 (absolute band, sign-safe).
  FAIL    - otherwise.
DD values are negative fractions (e.g. -0.30 = 30% drawdown).
"""
from __future__ import annotations

DD_ABS_MARGIN = 0.03    # 3 percentage points (fraction terms)
DD_REL_MARGIN = 0.15    # 15% relative
PNL_FLOOR_FRAC = 0.10   # v2 may give up at most 10% of |v1 PnL|


def evaluate_gate(v1_point: dict, v2_points: dict) -> tuple[str, object]:
    """Return (verdict, winning_slider | None). v2_points: {slider -> point}."""
    v1_dd = abs(v1_point["max_dd"])
    v1_pnl = float(v1_point["total_pnl"])
    pnl_floor = v1_pnl - PNL_FLOOR_FRAC * abs(v1_pnl)

    pass_slider = None
    for slider in sorted(v2_points):
        pt = v2_points[slider]
        v2_dd = abs(pt["max_dd"])
        v2_pnl = float(pt["total_pnl"])

        # STRONG = Pareto dominance with a strict P&L win (no worse DD AND
        # strictly higher PnL). A sub-threshold DD cut at equal PnL is NOT
        # STRONG: it must instead clear the magnitude gate below to PASS,
        # otherwise FAIL. This matches the pre-registered acceptance tests
        # (test_dd_reduction_too_small_or_pnl_floor_broken_is_fail, slider 30).
        if v2_dd <= v1_dd and v2_pnl > v1_pnl:
            return ("STRONG", slider)

        dd_abs_red = v1_dd - v2_dd
        dd_rel_red = (dd_abs_red / v1_dd) if v1_dd > 0 else 0.0
        dd_ok = dd_abs_red >= DD_ABS_MARGIN or dd_rel_red >= DD_REL_MARGIN
        if dd_ok and v2_pnl >= pnl_floor and pass_slider is None:
            pass_slider = slider

    if pass_slider is not None:
        return ("PASS", pass_slider)
    return ("FAIL", None)
