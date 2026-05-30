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


def _load_closed_trades(conn, *, tenant_id: int) -> list[dict[str, Any]]:
    """Load closed positions for `tenant_id` from DB for portfolio equity computation.

    Per the multi-tenant policy (epic B #253), `tenant_id` is required:
    background processes that don't have a user context must iterate via
    `db.capital.db_list_active_tenant_ids()` and call this loader once per
    tenant. Aggregating across tenants implicitly is not allowed.
    """
    rows = conn.execute(
        """SELECT symbol, exit_ts, pnl_usd
           FROM positions
           WHERE status = 'closed' AND exit_ts IS NOT NULL
             AND tenant_id = ?
           ORDER BY exit_ts""",
        (tenant_id,),
    ).fetchall()
    return [
        {"symbol": r[0], "exit_ts": r[1], "pnl_usd": r[2] or 0.0}
        for r in rows
    ]


def _load_open_positions(conn, *, tenant_id: int) -> list[dict[str, Any]]:
    """Load open positions for `tenant_id` from DB for MTM.

    See `_load_closed_trades` for the multi-tenant policy rationale.
    """
    rows = conn.execute(
        """SELECT symbol, entry_price, qty, direction
           FROM positions
           WHERE status = 'open' AND tenant_id = ?""",
        (tenant_id,),
    ).fetchall()
    # NOTE: qty is intentionally NOT coerced to 0.0 here. Legacy unmeasurable
    # positions (#467) carry qty=None; the membrane that hid this got removed
    # in the post-Serrano correction. Consumers (health.py, kill_switch_v2)
    # must skip None explicitly — see CLAUDE.md "Capas de enforcement".
    return [
        {
            "symbol": r[0],
            "entry_price": r[1] or 0.0,
            "qty": r[2],
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
    from db.transaction import snapshot_connection
    cutoff = (now - timedelta(hours=float(window_hours))).isoformat()
    with snapshot_connection() as conn:
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
    return [r[0] for r in rows if r[0]]


def _load_v2_state(symbol: str) -> dict[str, Any]:
    """Load per-symbol v2 state. Returns keys with None defaults if row missing."""
    from db.transaction import snapshot_connection
    with snapshot_connection() as conn:
        row = conn.execute(
            """SELECT velocity_cooldown_until, velocity_last_trigger_ts
               FROM kill_switch_v2_state
               WHERE symbol = ?""",
            (symbol,),
        ).fetchone()
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
    from db.transaction import transaction
    with transaction() as conn:
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
    from db.transaction import snapshot_connection
    with snapshot_connection() as conn:
        rows = conn.execute(
            """SELECT exit_ts, pnl_usd
               FROM positions
               WHERE symbol = ?
                 AND status = 'closed'
                 AND exit_ts IS NOT NULL
               ORDER BY exit_ts""",
            (symbol,),
        ).fetchall()
    return [{"exit_ts": r[0], "pnl_usd": r[1]} for r in rows]


def _load_baseline(symbol: str) -> dict[str, Any] | None:
    """Load per-symbol baseline. Returns None if no row exists."""
    from db.transaction import snapshot_connection
    with snapshot_connection() as conn:
        row = conn.execute(
            """SELECT baseline_wr, baseline_sigma, trades_count, computed_at
               FROM kill_switch_v2_baseline
               WHERE symbol = ?""",
            (symbol,),
        ).fetchone()
    if row is None:
        return None
    return {
        "wr": row[0],
        "sigma": row[1],
        "count": row[2],
        "computed_at": row[3],
    }


def _upsert_baseline(symbol: str, baseline: dict[str, Any], now) -> None:
    """Upsert per-symbol baseline. computed_at is set to now.isoformat().

    Validates that baseline has the three required keys; raises KeyError on
    missing keys instead of silently coercing to 0.0 (which would mask an
    upstream bug producing an empty baseline dict).
    """
    from db.transaction import transaction

    missing = [k for k in ("wr", "sigma", "count") if k not in baseline]
    if missing:
        raise KeyError(
            f"_upsert_baseline: baseline dict missing required keys: {missing}"
        )

    with transaction() as conn:
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
                float(baseline["wr"]),
                float(baseline["sigma"]),
                int(baseline["count"]),
                now.isoformat(),
            ),
        )


def _is_baseline_stale(
    computed_at: str | None, stale_days: float, now,
) -> bool:
    """Return True if the baseline is missing, malformed, in the future, or
    older than stale_days.

    A future timestamp (parsed > now) is treated as stale to guard against
    clock skew or a buggy writer; otherwise the bogus future timestamp would
    suppress recompute indefinitely.
    """
    from datetime import datetime, timedelta, timezone

    if not computed_at:
        return True
    try:
        parsed = datetime.fromisoformat(computed_at)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return True
    if parsed > now:
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
    *,
    tenant_id: int,
    regime_score: float | None = None,
    now_price_by_symbol: dict[str, float] | None = None,
) -> None:
    """Compute portfolio tier, write a v2_shadow row to the decision log.

    Uses the module-level price cache for MTM. Callers can pass additional
    prices via now_price_by_symbol; they're merged in. If regime_score is
    provided, B3 regime-aware adjustment is applied to the slider before
    threshold computation. Fail-open: any exception is caught and logged
    with full traceback.

    `tenant_id` is required: the portfolio MTM + DD must be computed against
    the tenant's positions, not the system aggregate. Schedulers (scanner,
    calibrator) iterate `db.capital.db_list_active_tenant_ids()` and call
    this once per tenant.
    """
    from strategy.kill_switch_v2 import (
        evaluate_portfolio_tier,
        classify_regime,
        compute_portfolio_dd_from_ledger,
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

        # Ledger-based DD (#397): balance already folds realized PnL; the live
        # equity is balance + open MTM. Do NOT walk closed trades again (that
        # double-counts and under-reports DD). Single source of truth:
        # kill_switch_v2.compute_portfolio_dd_from_ledger (same as the dashboard).
        from db.capital import db_get_capital
        from db.transaction import snapshot_connection as _snap
        # Pure reads — use snapshot_connection (WAL-concurrent, no writer lock) — #494
        with _snap() as _con:
            _capital_row = db_get_capital(_con, tenant_id)
            opens = _load_open_positions(_con, tenant_id=tenant_id)
        if _capital_row and _capital_row.get("balance") is not None:
            _balance = float(_capital_row["balance"])
            _peak = _capital_row.get("peak_balance")
        else:
            _balance = float(cfg.get("capital_usd", _DEFAULT_CAPITAL_USD))
            _peak = None
        prices = _snapshot_prices()
        if now_price_by_symbol:
            prices.update(now_price_by_symbol)

        _dd_result = compute_portfolio_dd_from_ledger(
            balance=_balance,
            peak_balance=(float(_peak) if _peak is not None else None),
            open_positions=opens,
            now_price_by_symbol=prices,
        )
        portfolio_dd = _dd_result["portfolio_dd"]
        concurrent = _count_concurrent_failures()

        portfolio = evaluate_portfolio_tier(
            portfolio_dd=portfolio_dd,
            concurrent_failures=concurrent,
            cfg=cfg_eff,
        )

        # B6: record portfolio-tier transitions for the dashboard
        try:
            from health import recent_portfolio_transitions, record_portfolio_transition
            from db.transaction import snapshot_connection as _snap_for_pt, transaction as _tx_for_pt
            # Pure read — snapshot_connection (WAL-concurrent, no writer lock) — #494
            with _snap_for_pt() as _pt_con:
                recent = recent_portfolio_transitions(_pt_con, limit=1)
            prev_tier = recent[0]["to_tier"] if recent else "NORMAL"
            if prev_tier != portfolio["tier"]:
                with _tx_for_pt() as _pt_con:
                    record_portfolio_transition(
                        _pt_con,
                        from_tier=prev_tier,
                        to_tier=portfolio["tier"],
                        reason=f"shadow_eval_dd_{portfolio_dd:.4f}",
                        dd_pct=float(portfolio_dd),
                        concurrent=int(concurrent),
                    )
        except Exception:
            log.warning("record_portfolio_transition (shadow) failed", exc_info=True)

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
                "dd_formula_version": "ledger_v1",
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
