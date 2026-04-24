"""Kill switch v2 shadow engine (#187 B2 — portfolio circuit breaker).

Pure functions computing portfolio-level state from equity curves. Runs in
shadow mode during Phase 2: writes to decision log with engine='v2_shadow';
does NOT affect real trading. The actual v1 kill switch continues operating
untouched.

Operator-facing slider (0-100) interpolates thresholds linearly between
tmin (laxo) and tmax (paranoid). Values come from config.defaults.json
under kill_switch.v2.thresholds.
"""
from __future__ import annotations

from typing import Any


# Defaults (match config.defaults.json). Used as fallback when config is incomplete.
_DEFAULT_AGGRESSIVENESS = 50.0
_DEFAULT_DD_REDUCED = {"min": -0.08, "max": -0.03}
_DEFAULT_DD_FROZEN = {"min": -0.15, "max": -0.06}


def interpolate_threshold(slider: float, t_min: float, t_max: float) -> float:
    """Linearly interpolate a threshold value from the slider (0-100).

    slider=0 → t_min (most permissive)
    slider=100 → t_max (most strict)
    """
    slider = max(0.0, min(100.0, float(slider)))
    return t_min + (slider / 100.0) * (t_max - t_min)


def get_portfolio_thresholds(cfg: dict[str, Any]) -> dict[str, float]:
    """Extract the slider-adjusted portfolio DD thresholds from config.

    Returns:
        {"reduced_dd": float, "frozen_dd": float}

    Both values are negative (drawdowns). Falls back to defaults when config
    keys are missing.
    """
    v2_cfg = (cfg.get("kill_switch", {}) or {}).get("v2", {}) or {}
    slider = v2_cfg.get("aggressiveness", _DEFAULT_AGGRESSIVENESS)
    thresholds_cfg = v2_cfg.get("thresholds", {}) or {}

    reduced_range = thresholds_cfg.get("portfolio_dd_reduced") or _DEFAULT_DD_REDUCED
    frozen_range = thresholds_cfg.get("portfolio_dd_frozen") or _DEFAULT_DD_FROZEN

    return {
        "reduced_dd": interpolate_threshold(
            slider, reduced_range["min"], reduced_range["max"]
        ),
        "frozen_dd": interpolate_threshold(
            slider, frozen_range["min"], frozen_range["max"]
        ),
    }


def compute_portfolio_equity_curve(
    closed_trades: list[dict[str, Any]],
    open_positions: list[dict[str, Any]],
    capital_base: float,
    now_price_by_symbol: dict[str, float],
) -> list[dict[str, Any]]:
    """Compute a portfolio equity curve by applying closed trades + open MTM.

    Args:
        closed_trades: list of {"symbol", "exit_ts", "pnl_usd"} — pnl added cumulatively.
        open_positions: list of {"symbol", "entry_price", "qty", "direction"} — MTM'd at end.
        capital_base: starting equity.
        now_price_by_symbol: current price per symbol, used to MTM open positions.

    Returns:
        List of {"ts": str, "equity": float} points, time-ordered.
    """
    # Sort closed trades by exit_ts ascending
    sorted_closed = sorted(closed_trades, key=lambda t: t.get("exit_ts", ""))

    curve: list[dict[str, Any]] = []

    # Starting point
    start_ts = sorted_closed[0].get("exit_ts") if sorted_closed else "start"
    curve.append({"ts": start_ts, "equity": capital_base})

    # Apply each closed trade
    current_equity = capital_base
    for trade in sorted_closed:
        pnl = float(trade.get("pnl_usd") or 0)
        current_equity += pnl
        curve.append({"ts": trade.get("exit_ts", ""), "equity": current_equity})

    # Add MTM point for open positions
    mtm_total = 0.0
    for pos in open_positions:
        sym = pos.get("symbol")
        if sym not in now_price_by_symbol:
            continue
        entry = float(pos.get("entry_price") or 0)
        qty = float(pos.get("qty") or 0)
        direction = pos.get("direction", "LONG")
        current_price = now_price_by_symbol[sym]
        if direction == "SHORT":
            mtm_total += (entry - current_price) * qty
        else:
            mtm_total += (current_price - entry) * qty

    if mtm_total != 0.0:
        curve.append({"ts": "now_mtm", "equity": current_equity + mtm_total})

    return curve
