"""Shadow-mode glue for kill switch v2 (#187 B2).

Reads state from DB (closed trades + open positions + current prices),
calls the pure functions in strategy.kill_switch_v2, writes a decision
to the observability log with engine='v2_shadow'.

Fail-open: any exception is logged; v1 keeps operating untouched.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("kill_switch_v2_shadow")


def _load_closed_trades() -> list[dict[str, Any]]:
    """Load closed positions from DB for portfolio equity computation."""
    import btc_api
    conn = btc_api.get_db()
    try:
        rows = conn.execute(
            """SELECT symbol, exit_ts, pnl_usd
               FROM positions
               WHERE status = 'closed' AND exit_ts IS NOT NULL
               ORDER BY exit_ts"""
        ).fetchall()
    finally:
        conn.close()
    return [
        {"symbol": r[0], "exit_ts": r[1], "pnl_usd": r[2] or 0.0}
        for r in rows
    ]


def _load_open_positions() -> list[dict[str, Any]]:
    """Load open positions from DB for MTM."""
    import btc_api
    conn = btc_api.get_db()
    try:
        rows = conn.execute(
            """SELECT symbol, entry_price, qty, direction
               FROM positions
               WHERE status = 'open'"""
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "symbol": r[0],
            "entry_price": r[1] or 0.0,
            "qty": r[2] or 0.0,
            "direction": r[3] or "LONG",
        }
        for r in rows
    ]


def _count_concurrent_failures() -> int:
    """Count symbols whose latest v1 decision is ALERT/REDUCED/PAUSED/PROBATION."""
    import observability
    state = observability.get_current_state(engine="v1")
    return state["portfolio"]["concurrent_failures"]


def emit_shadow_decision(
    symbol: str,
    cfg: dict[str, Any],
    now_price_by_symbol: dict[str, float] | None = None,
) -> None:
    """Compute portfolio tier, write a v2_shadow row to the decision log.

    Fail-open: any exception is caught and logged.
    """
    from strategy.kill_switch_v2 import (
        compute_portfolio_equity_curve,
        compute_portfolio_dd,
        evaluate_portfolio_tier,
        get_portfolio_thresholds,
    )
    import observability

    try:
        capital_base = float(cfg.get("capital_usd", 100_000.0))
        closed = _load_closed_trades()
        opens = _load_open_positions()
        prices = now_price_by_symbol or {}

        equity_curve = compute_portfolio_equity_curve(
            closed_trades=closed,
            open_positions=opens,
            capital_base=capital_base,
            now_price_by_symbol=prices,
        )
        portfolio_dd = compute_portfolio_dd(equity_curve)
        concurrent = _count_concurrent_failures()

        portfolio = evaluate_portfolio_tier(
            portfolio_dd=portfolio_dd,
            concurrent_failures=concurrent,
            cfg=cfg,
        )

        v2_cfg = (cfg.get("kill_switch", {}) or {}).get("v2", {}) or {}
        slider = float(v2_cfg.get("aggressiveness", 50.0))

        observability.record_decision(
            symbol=symbol,
            engine="v2_shadow",
            per_symbol_tier="NORMAL",  # v2 per-symbol tier lands with B4 auto-cal
            portfolio_tier=portfolio["tier"],
            size_factor=1.0,  # v2 sizing lands later
            skip=False,
            reasons={
                "portfolio_dd": portfolio_dd,
                "reduced_threshold": portfolio["reduced_threshold"],
                "frozen_threshold": portfolio["frozen_threshold"],
                "concurrent_failures": concurrent,
            },
            scan_id=None,
            slider_value=slider,
            velocity_active=False,
        )
    except Exception as e:
        log.warning("kill_switch_v2_shadow.emit_shadow_decision failed for %s: %s", symbol, e)
