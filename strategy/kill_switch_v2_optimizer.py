"""V2 backtest grid optimization for auto-calibrator (#187 #216 B4b.2).

Replaces B4b.1's run_optimization_stub with a real fitness function:
loads closed trades from positions table, replays each across 21 slider
candidates [0..100, step 5] using V2KillSwitchSimulator, picks slider with
max PnL subject to dd_target constraint.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("kill_switch_v2_optimizer")

_DEFAULT_BACKTEST_WINDOW_DAYS = 365
_DEFAULT_DD_TARGET = -0.10
_DEFAULT_CAPITAL_USD = 1000.0
_GRID_STEP = 5


def _load_closed_positions_window(window_days: float, now) -> list[dict[str, Any]]:
    """Load closed positions with exit_ts within the last window_days, ordered by entry_ts."""
    from datetime import timedelta
    import btc_api

    cutoff = (now - timedelta(days=float(window_days))).isoformat()
    conn = btc_api.get_db()
    try:
        rows = conn.execute(
            """SELECT symbol, entry_ts, exit_ts, exit_reason, pnl_usd
               FROM positions
               WHERE status = 'closed'
                 AND exit_ts IS NOT NULL
                 AND pnl_usd IS NOT NULL
                 AND exit_ts >= ?
               ORDER BY entry_ts""",
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {"symbol": r[0], "entry_ts": r[1], "exit_ts": r[2],
         "exit_reason": r[3], "pnl_usd": r[4]}
        for r in rows
    ]


def _override_slider(cfg: dict[str, Any], slider: int) -> dict[str, Any]:
    """Return a deep-copied cfg with kill_switch.v2.aggressiveness=slider.

    Creates the kill_switch.v2 block if missing.
    """
    import copy

    cfg_copy = copy.deepcopy(cfg) if cfg else {}
    ks = cfg_copy.setdefault("kill_switch", {})
    v2 = ks.setdefault("v2", {})
    v2["aggressiveness"] = slider
    return cfg_copy


def _replay_with_slider(
    closed_trades: list[dict[str, Any]],
    cfg_with_slider: dict[str, Any],
    regime_score: float | None,
    capital_base: float,
) -> dict[str, float]:
    """Replay trades through V2KillSwitchSimulator. Returns {pnl, dd}.

    For each trade:
      1. Ask simulator: would v2 take this trade? size_factor?
      2. PnL contribution = 0 if skip else trade.pnl_usd * size_factor.
      3. Update equity, track peak, compute running dd.
      4. Feed close back to simulator (updates state for future trades).

    pnl = final_equity - capital_base.
    dd = max drawdown over the equity curve (most negative value).
    """
    from strategy.kill_switch_v2_simulator import V2KillSwitchSimulator

    sim = V2KillSwitchSimulator(cfg_with_slider, regime_score, capital_base)
    equity = capital_base
    peak = capital_base
    max_dd = 0.0

    for trade in closed_trades:
        skip, size_factor = sim.should_skip_or_reduce(
            symbol=trade["symbol"], entry_ts=trade["entry_ts"],
        )
        raw_pnl = float(trade.get("pnl_usd") or 0)
        pnl_contrib = 0.0 if skip else raw_pnl * size_factor

        equity += pnl_contrib
        peak = max(peak, equity)
        if peak > 0:
            dd = (equity - peak) / peak
            max_dd = min(max_dd, dd)

        sim.on_trade_close(
            symbol=trade["symbol"], exit_ts=trade["exit_ts"],
            pnl_usd=pnl_contrib, exit_reason=trade.get("exit_reason") or "",
        )

    return {"pnl": equity - capital_base, "dd": max_dd}


def run_optimization_v2(
    cfg: dict[str, Any], regime_score: float | None = None,
) -> dict[str, Any]:
    """Real grid optimization replacing run_optimization_stub from B4b.1.

    Loads closed trades from the configured backtest window, replays each
    across 21 slider candidates [0..100, step 5] using V2KillSwitchSimulator,
    picks the slider with max PnL subject to dd_target constraint.

    Returns same shape as run_optimization_stub:
        {"status": str, "slider_value": int|None, "projected_pnl": float|None,
         "projected_dd": float|None, "report": dict}

    status values:
        "pending"     — feasible slider found, recommendation ready for review.
        "no_feasible" — all sliders blow dd_target; report includes the grid.
    """
    from datetime import datetime, timezone

    now = datetime.now(tz=timezone.utc)
    v2_cfg = (cfg.get("kill_switch", {}) or {}).get("v2", {}) or {}
    auto_cal = v2_cfg.get("auto_calibrator", {}) or {}
    window_days = float(auto_cal.get(
        "backtest_window_days", _DEFAULT_BACKTEST_WINDOW_DAYS,
    ))
    dd_target = float(auto_cal.get("dd_target", _DEFAULT_DD_TARGET))
    if dd_target > 0:
        # DD is always negative or zero; a positive target makes every slider
        # trivially feasible and would silently approve recommendations that
        # blow any meaningful drawdown limit. Reject explicitly so a config
        # typo (sign error) doesn't render the optimizer unsafe.
        raise ValueError(
            f"dd_target must be <= 0 (got {dd_target}); "
            "fix kill_switch.v2.auto_calibrator.dd_target in config",
        )
    capital_base = float(cfg.get("capital_usd", _DEFAULT_CAPITAL_USD))

    closed = _load_closed_positions_window(window_days, now)

    grid_results: dict[int, dict[str, float]] = {}
    for slider in range(0, 101, _GRID_STEP):
        cfg_eff = _override_slider(cfg, slider)
        result = _replay_with_slider(closed, cfg_eff, regime_score, capital_base)
        grid_results[slider] = result

    # Feasibility: dd is negative; constraint dd >= dd_target
    feasible = {s: r for s, r in grid_results.items() if r["dd"] >= dd_target}

    report_payload = {
        "ts": now.isoformat(),
        "window_days": window_days,
        "dd_target": dd_target,
        "capital_base": capital_base,
        "trades_in_window": len(closed),
        "regime_score": regime_score,
        "grid": {
            str(s): {"pnl": r["pnl"], "dd": r["dd"]}
            for s, r in grid_results.items()
        },
        "stub": False,
    }

    if not feasible:
        nearest = max(grid_results, key=lambda s: grid_results[s]["dd"])
        report_payload["reason"] = (
            f"all sliders blow dd_target={dd_target}; nearest_slider={nearest}"
        )
        return {
            "status": "no_feasible",
            "slider_value": None,
            "projected_pnl": grid_results[nearest]["pnl"],
            "projected_dd": grid_results[nearest]["dd"],
            "report": report_payload,
        }

    best = max(feasible, key=lambda s: feasible[s]["pnl"])
    return {
        "status": "pending",
        "slider_value": best,
        "projected_pnl": feasible[best]["pnl"],
        "projected_dd": feasible[best]["dd"],
        "report": report_payload,
    }
