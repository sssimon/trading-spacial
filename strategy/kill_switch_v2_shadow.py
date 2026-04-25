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

# Matches btc_scanner.scan()'s hardcoded capital (see btc_scanner.py:1121).
# Config doesn't currently expose this; the default must match the real
# deployed value so shadow DD is not off by ~100×.
_DEFAULT_CAPITAL_USD = 1000.0

# Price cache accumulated across scan() calls. Each scan updates its symbol's
# price via update_price(); emit_shadow_decision MTMs every open position
# that has a cached price. Over one full scan cycle (~10 symbols), all live
# symbols populate.
_PRICE_CACHE: dict[str, float] = {}


def _now():
    """Indirection seam so tests can monkeypatch the current time."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def update_price(symbol: str, price: float) -> None:
    """Record the latest scanned price so MTM can see every open symbol."""
    _PRICE_CACHE[symbol] = float(price)


def _snapshot_prices() -> dict[str, float]:
    return dict(_PRICE_CACHE)


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
    portfolio = state.get("portfolio") or {}
    return int(portfolio.get("concurrent_failures", 0))


def _load_recent_sl_timestamps(
    symbol: str, now, window_hours: float
) -> list[str]:
    """Load exit_ts of closed positions with exit_reason='SL' for a symbol within window."""
    from datetime import timedelta
    import btc_api
    cutoff = (now - timedelta(hours=float(window_hours))).isoformat()
    conn = btc_api.get_db()
    try:
        rows = conn.execute(
            """SELECT exit_ts
               FROM positions
               WHERE symbol = ?
                 AND status = 'closed'
                 AND exit_reason = 'SL'
                 AND exit_ts IS NOT NULL
                 AND exit_ts >= ?""",
            (symbol, cutoff),
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows if r[0]]


def _load_v2_state(symbol: str) -> dict[str, Any]:
    """Load per-symbol v2 state. Returns keys with None defaults if row missing."""
    import btc_api
    conn = btc_api.get_db()
    try:
        row = conn.execute(
            """SELECT velocity_cooldown_until, velocity_last_trigger_ts
               FROM kill_switch_v2_state
               WHERE symbol = ?""",
            (symbol,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {
            "velocity_cooldown_until": None,
            "velocity_last_trigger_ts": None,
        }
    return {
        "velocity_cooldown_until": row[0],
        "velocity_last_trigger_ts": row[1],
    }


def _upsert_v2_state(symbol: str, state: dict[str, Any], now) -> None:
    """Upsert v2 state for a symbol. updated_at is set to now.isoformat()."""
    import btc_api
    conn = btc_api.get_db()
    try:
        conn.execute(
            """INSERT INTO kill_switch_v2_state
                 (symbol, velocity_cooldown_until, velocity_last_trigger_ts, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(symbol) DO UPDATE SET
                 velocity_cooldown_until = excluded.velocity_cooldown_until,
                 velocity_last_trigger_ts = excluded.velocity_last_trigger_ts,
                 updated_at = excluded.updated_at""",
            (
                symbol,
                state.get("velocity_cooldown_until"),
                state.get("velocity_last_trigger_ts"),
                now.isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _evaluate_velocity(symbol: str, cfg: dict[str, Any]) -> bool:
    """Evaluate B1 velocity triggers for a symbol.

    Loads recent SLs, reads/updates v2 state, returns whether the cooldown
    is currently active. Caller is responsible for fail-open wrapping; this
    function may raise.
    """
    from strategy.kill_switch_v2 import (
        get_velocity_thresholds,
        detect_velocity_trigger,
        compute_velocity_state,
    )
    from datetime import datetime, timezone

    now = _now()
    thresholds = get_velocity_thresholds(cfg)

    sl_timestamps = _load_recent_sl_timestamps(
        symbol, now=now, window_hours=thresholds["window_hours"],
    )
    current_state = _load_v2_state(symbol)
    triggered = detect_velocity_trigger(
        sl_timestamps, now,
        sl_count=thresholds["sl_count"],
        window_hours=thresholds["window_hours"],
    )
    new_state = compute_velocity_state(
        current_state, triggered=triggered, now=now,
        cooldown_hours=thresholds["cooldown_hours"],
    )
    if new_state != current_state:
        _upsert_v2_state(symbol, new_state, now=now)

    cooldown = new_state.get("velocity_cooldown_until")
    if not cooldown:
        return False
    try:
        parsed = datetime.fromisoformat(cooldown)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed > now
    except (TypeError, ValueError):
        return False


def _load_closed_trades_for_symbol(symbol: str) -> list[dict[str, Any]]:
    """Load closed positions for a symbol with non-NULL exit_ts."""
    import btc_api
    conn = btc_api.get_db()
    try:
        rows = conn.execute(
            """SELECT exit_ts, pnl_usd
               FROM positions
               WHERE symbol = ?
                 AND status = 'closed'
                 AND exit_ts IS NOT NULL
               ORDER BY exit_ts""",
            (symbol,),
        ).fetchall()
    finally:
        conn.close()
    return [{"exit_ts": r[0], "pnl_usd": r[1]} for r in rows]


def _load_baseline(symbol: str) -> dict[str, Any] | None:
    """Load per-symbol baseline. Returns None if no row exists."""
    import btc_api
    conn = btc_api.get_db()
    try:
        row = conn.execute(
            """SELECT baseline_wr, baseline_sigma, trades_count, computed_at
               FROM kill_switch_v2_baseline
               WHERE symbol = ?""",
            (symbol,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "wr": row[0],
        "sigma": row[1],
        "count": row[2],
        "computed_at": row[3],
    }


def _upsert_baseline(symbol: str, baseline: dict[str, Any], now) -> None:
    """Upsert per-symbol baseline. computed_at is set to now.isoformat()."""
    import btc_api
    conn = btc_api.get_db()
    try:
        conn.execute(
            """INSERT INTO kill_switch_v2_baseline
                 (symbol, baseline_wr, baseline_sigma, trades_count, computed_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(symbol) DO UPDATE SET
                 baseline_wr = excluded.baseline_wr,
                 baseline_sigma = excluded.baseline_sigma,
                 trades_count = excluded.trades_count,
                 computed_at = excluded.computed_at""",
            (
                symbol,
                float(baseline.get("wr", 0.0)),
                float(baseline.get("sigma", 0.0)),
                int(baseline.get("count", 0)),
                now.isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _is_baseline_stale(
    computed_at: str | None, stale_days: float, now,
) -> bool:
    """Return True if the baseline is missing, malformed, or older than stale_days."""
    from datetime import datetime, timedelta, timezone

    if not computed_at:
        return True
    try:
        parsed = datetime.fromisoformat(computed_at)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return True
    return (now - parsed) > timedelta(days=float(stale_days))


def _evaluate_per_symbol_tier_with_telemetry(
    symbol: str, cfg: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Evaluate B4a per-symbol tier (NORMAL or ALERT) with full telemetry.

    Lazy-refresh logic: if the cached baseline is missing or older than
    `baseline_stale_days`, recompute from positions and upsert. If fresh,
    reuse cached.

    Returns:
        (tier, telemetry) where telemetry contains the inputs the dashboard
        needs to explain the decision (baseline_wr, sigma, rolling_wr,
        sigma_multiplier, trades_count, baseline_stale, status="ok").

    This function may raise on DB errors / malformed data — caller is
    responsible for fail-open wrapping.
    """
    from strategy.kill_switch_v2 import (
        compute_baseline_metrics,
        evaluate_per_symbol_tier,
        get_baseline_sigma_multiplier,
    )
    from health import compute_rolling_metrics_from_trades

    now = _now()

    v2_cfg = (cfg.get("kill_switch", {}) or {}).get("v2", {}) or {}
    min_trades = int(v2_cfg.get("baseline_min_trades", 100))
    stale_days = float(v2_cfg.get("baseline_stale_days", 7))

    closed_trades = _load_closed_trades_for_symbol(symbol)

    cached = _load_baseline(symbol)
    baseline_was_stale = cached is None or _is_baseline_stale(
        cached.get("computed_at") if cached else None, stale_days, now,
    )

    if baseline_was_stale:
        baseline = compute_baseline_metrics(closed_trades)
        _upsert_baseline(symbol, baseline, now=now)
    else:
        baseline = {
            "wr": cached["wr"],
            "sigma": cached["sigma"],
            "count": cached["count"],
        }

    rolling = compute_rolling_metrics_from_trades(closed_trades, now=now)
    rolling_wr_20 = rolling.get("win_rate_20_trades")

    sigma_multiplier = get_baseline_sigma_multiplier(cfg)

    tier = evaluate_per_symbol_tier(
        rolling_wr_20=rolling_wr_20,
        baseline=baseline,
        sigma_multiplier=sigma_multiplier,
        trades_count=baseline["count"],
        min_trades=min_trades,
    )

    telemetry = {
        "tier": tier,
        "status": "ok",
        "baseline_wr": baseline["wr"],
        "baseline_sigma": baseline["sigma"],
        "rolling_wr_20": rolling_wr_20,
        "sigma_multiplier": sigma_multiplier,
        "trades_count": baseline["count"],
        "baseline_stale": baseline_was_stale,
    }
    return tier, telemetry


def emit_shadow_decision(
    symbol: str,
    cfg: dict[str, Any],
    regime_score: float | None = None,
    now_price_by_symbol: dict[str, float] | None = None,
) -> None:
    """Compute portfolio tier, write a v2_shadow row to the decision log.

    Uses the module-level price cache for MTM. Callers can pass additional
    prices via now_price_by_symbol; they're merged in. If regime_score is
    provided, B3 regime-aware adjustment is applied to the slider before
    threshold computation. Fail-open: any exception is caught and logged
    with full traceback.
    """
    from strategy.kill_switch_v2 import (
        compute_portfolio_equity_curve,
        compute_portfolio_dd,
        evaluate_portfolio_tier,
        classify_regime,
    )
    from strategy import kill_switch_v2 as _ks_v2
    import observability

    try:
        # B3: apply regime-aware adjustment to cfg (fail-safe: fall back to original)
        _regime_adjustment_status = "ok"
        try:
            cfg_eff = _ks_v2.apply_regime_adjustment(cfg, regime_score)
        except Exception as _re:
            log.warning(
                "B3 regime adjustment failed for %s: %s",
                symbol, _re, exc_info=True,
            )
            # Deepcopy on fallback for symmetry — success path returns a new dict
            # so downstream consumers never share mutable state with the caller.
            import copy as _copy
            try:
                cfg_eff = _copy.deepcopy(cfg)
            except Exception:
                cfg_eff = cfg if isinstance(cfg, dict) else {}
            _regime_adjustment_status = "failed"

        capital_base = float(cfg.get("capital_usd", _DEFAULT_CAPITAL_USD))
        closed = _load_closed_trades()
        opens = _load_open_positions()
        prices = _snapshot_prices()
        if now_price_by_symbol:
            prices.update(now_price_by_symbol)

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
            cfg=cfg_eff,
        )

        # Slider values for telemetry
        v2_base = (cfg.get("kill_switch", {}) or {}).get("v2", {}) or {}
        v2_eff = (cfg_eff.get("kill_switch", {}) or {}).get("v2", {}) or {}
        slider_base = float(v2_base.get("aggressiveness", 50.0))
        slider_effective = float(v2_eff.get("aggressiveness", slider_base))
        regime_enabled = bool(
            (v2_base.get("advanced_overrides", {}) or {}).get(
                "regime_adjustment_enabled", True
            )
        )

        # B1 velocity triggers — fail-open; defaults to False if anything raises.
        try:
            velocity_active = _evaluate_velocity(symbol, cfg_eff)
        except Exception as _ve:
            log.warning(
                "B1 velocity eval failed for %s: %s", symbol, _ve, exc_info=True,
            )
            velocity_active = False

        # B4a per-symbol tier — fail-open; defaults to NORMAL with status=failed.
        try:
            per_symbol_tier, per_symbol_telemetry = (
                _evaluate_per_symbol_tier_with_telemetry(symbol, cfg_eff)
            )
        except Exception as _pe:
            log.warning(
                "B4a per-symbol tier eval failed for %s: %s",
                symbol, _pe, exc_info=True,
            )
            per_symbol_tier = "NORMAL"
            per_symbol_telemetry = {
                "tier": "NORMAL",
                "status": "failed",
                "baseline_wr": None,
                "baseline_sigma": None,
                "rolling_wr_20": None,
                "sigma_multiplier": None,
                "trades_count": 0,
                "baseline_stale": None,
            }

        observability.record_decision(
            symbol=symbol,
            engine="v2_shadow",
            per_symbol_tier=per_symbol_tier,
            portfolio_tier=portfolio["tier"],
            size_factor=1.0,
            skip=False,
            reasons={
                "portfolio_dd": portfolio_dd,
                "reduced_threshold": portfolio["reduced_threshold"],
                "frozen_threshold": portfolio["frozen_threshold"],
                "concurrent_failures": concurrent,
                "regime": {
                    "score": regime_score,
                    "label": classify_regime(regime_score),
                    "slider_base": slider_base,
                    "slider_effective": slider_effective,
                    "adjustment": slider_effective - slider_base,
                    "enabled": regime_enabled,
                    "adjustment_status": _regime_adjustment_status,
                },
                "per_symbol": per_symbol_telemetry,
            },
            scan_id=None,
            slider_value=slider_effective,
            velocity_active=velocity_active,
        )
    except Exception as e:
        log.warning(
            "kill_switch_v2_shadow.emit_shadow_decision failed for %s: %s",
            symbol, e, exc_info=True,
        )
