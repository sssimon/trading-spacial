"""Read-only tool handlers — Phase 1 of epic #400.

Every handler:

  1. Is keyword-only and REQUIRES `tenant_id: int`. Callers (Phase 2's
     conversation core, the unit tests) construct them with the JWT-
     resolved tenant_id bound. The model never sees nor supplies
     tenant_id — adding it to the input schema is a regression.

  2. Filters every DB read by tenant_id where the table is per-user
     (positions, signal_outcomes, notifications_sent,
     portfolio_health_events). System-wide reads (macro cache, scanner
     state, kill-switch dashboard) flow through `get_dashboard_state`
     which is already tenant-aware as of PR #396.

  3. Returns a plain `dict` (or list of dicts). The conversation core
     serializes them as tool_result content; the Anthropic API accepts
     stringified JSON, so we json.dumps at the dispatch boundary, not
     here.

  4. On ownership mismatch or missing-row, returns `{"error": "not_found"}`.
     Never raises — the model handles error blocks via the
     `is_error: true` convention.

Pre-reg §5.1 and §8.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from db.transaction import snapshot_connection

log = logging.getLogger("api.agent.tools.handlers")


# ── Internal helpers ────────────────────────────────────────────────────


def _row_to_dict(row: Any) -> dict:
    if row is None:
        return {}
    return dict(row)


def _position_to_summary(pos: dict) -> dict:
    """Project a positions row down to the user-relevant fields. Drops
    internal columns the model has no business reasoning about."""
    return {
        "id":          pos.get("id"),
        "symbol":      pos.get("symbol"),
        "direction":   pos.get("direction"),
        "status":      pos.get("status"),
        "entry_price": pos.get("entry_price"),
        "entry_ts":    pos.get("entry_ts"),
        "sl_price":    pos.get("sl_price"),
        "tp_price":    pos.get("tp_price"),
        "size_usd":    pos.get("size_usd"),
        "exit_price":  pos.get("exit_price"),
        "exit_ts":     pos.get("exit_ts"),
        "exit_reason": pos.get("exit_reason"),
        "pnl_usd":     pos.get("pnl_usd"),
        "pnl_pct":     pos.get("pnl_pct"),
    }


def _normalize_symbol(s: str) -> str:
    """Accept 'BTC' and return 'BTCUSDT'; pass through tickers already
    ending in USDT. Defensive only — the model usually sends the full
    ticker because the system prompt shows them that way."""
    s = (s or "").upper().strip()
    if not s:
        return s
    if s.endswith("USDT"):
        return s
    return f"{s}USDT"


# ── Read-only handlers ──────────────────────────────────────────────────


def get_portfolio_overview(*, tenant_id: int) -> dict:
    """Aggregate snapshot: open positions count + notional + DD + macro.

    Reuses get_dashboard_state's portfolio block (already tenant-aware
    as of PR #396) for the equity / DD numbers; the open-positions
    section comes from db.positions.db_get_positions with tenant_id.
    """
    from db.positions import db_get_positions
    from health import get_dashboard_state
    from api.config import load_config

    with snapshot_connection() as con:
        # control_domain='INTERNAL': el copiloto solo gobierna posiciones del
        # sistema; no presenta EXTERNAL como actuable (no puede cerrarlas) — CD-1.
        open_positions = db_get_positions(
            con, status="open", tenant_id=tenant_id, control_domain="INTERNAL"
        )
    open_count = len(open_positions)
    total_notional = sum(float(p.get("size_usd") or 0) for p in open_positions)

    try:
        dashboard = get_dashboard_state(load_config(), tenant_id=tenant_id)
    except Exception:  # noqa: BLE001
        log.warning("get_portfolio_overview: dashboard fetch failed", exc_info=True)
        # Consistent with get_kill_switch_state: when the dashboard is
        # unavailable, surface the failure explicitly so the model knows
        # the equity numbers are missing because of an outage, NOT
        # because the user has zero equity. Returning nulls would be
        # ambiguous (PR #403 review issue 1).
        return {"error": "dashboard_unavailable"}

    portfolio = dashboard.get("portfolio", {})
    return {
        "open_positions_count":          open_count,
        "open_positions_notional_usd":   round(total_notional, 2),
        "current_equity_usd":            portfolio.get("current_equity"),
        "peak_equity_usd":               portfolio.get("peak_equity"),
        "drawdown_pct":                  portfolio.get("dd_pct"),
        "portfolio_tier":                portfolio.get("tier"),
    }


def get_positions(*, tenant_id: int) -> dict:
    """Currently open positions, summarized.

    control_domain='INTERNAL': EXTERNAL positions (opened outside the system)
    are not shown as the copilot's governable positions — it cannot close them
    (CD-1). They surface in the operator's own view (v0.1.5), not here.
    """
    from db.positions import db_get_positions
    with snapshot_connection() as con:
        rows = db_get_positions(
            con, status="open", tenant_id=tenant_id, control_domain="INTERNAL"
        )
    return {"positions": [_position_to_summary(p) for p in rows]}


def get_position_detail(*, tenant_id: int, position_id: int) -> dict:
    """Single position by id, ownership-enforced.

    Mirrors the IDOR pattern in db.positions.db_close_position: if the
    row exists but doesn't belong to this tenant, return 'not_found' —
    never reveals existence cross-tenant.
    """
    with snapshot_connection() as con:
        row = con.execute(
            "SELECT * FROM positions WHERE id=? AND tenant_id=?",
            (position_id, tenant_id),
        ).fetchone()
    if row is None:
        return {"error": "not_found"}
    return _position_to_summary(_row_to_dict(row))


def get_symbols_with_signals(*, tenant_id: int, limit: int = 10) -> dict:  # noqa: ARG001
    """Curated symbols by current signal score. System-wide read; tenant_id
    is accepted for signature uniformity but does not gate the result.
    The scanner state is global (one snapshot per symbol regardless of
    tenant) — the per-user notion enters at the signal-outcomes layer.

    De-dupe by symbol happens in SQL (one row per symbol = the latest
    scan), so the caller gets up to `limit` *distinct* symbols rather
    than `limit` raw scan rows that may all be the same symbol.
    """
    from db.signals import get_latest_scan_per_symbol
    with snapshot_connection() as con:
        rows = get_latest_scan_per_symbol(con, limit=limit, only_signals=False)
    return {
        "symbols": [
            {
                "symbol":    r.get("symbol"),
                "score":     r.get("score"),
                "estado":    r.get("estado"),
                "signal":    bool(r.get("señal") or 0),
                "ts":        r.get("ts"),
            }
            for r in rows if r.get("symbol")
        ]
    }


def get_symbol_setup(*, tenant_id: int, symbol: str) -> dict:  # noqa: ARG001
    """Latest scanner setup for one symbol. System-wide read (tenant_id
    accepted but unused — same rationale as get_symbols_with_signals).
    """
    from db.signals import get_scans
    norm = _normalize_symbol(symbol)
    with snapshot_connection() as con:
        rows = get_scans(con, limit=1, symbol=norm)
    if not rows:
        return {"error": "not_found"}
    r = rows[0]
    return {
        "symbol":   r.get("symbol"),
        "estado":   r.get("estado"),
        "score":    r.get("score"),
        "signal":   bool(r.get("señal") or 0),
        "direction": r.get("direction"),
        "lrc_pct":  r.get("lrc_pct"),
        "rsi_1h":   r.get("rsi_1h"),
        "ts":       r.get("ts"),
    }


def get_kill_switch_state(*, tenant_id: int) -> dict:
    """Portfolio tier + per-symbol kill-switch state. Tenant-aware DD
    (PR #396) is what produces a portfolio_tier coherent with this user's
    actual equity, not the legacy $1K simulator default.
    """
    from health import get_dashboard_state
    from api.config import load_config
    try:
        dashboard = get_dashboard_state(load_config(), tenant_id=tenant_id)
    except Exception:  # noqa: BLE001
        log.warning("get_kill_switch_state: dashboard fetch failed", exc_info=True)
        return {"error": "kill_switch_unavailable"}
    return {
        "portfolio_tier":     dashboard.get("portfolio", {}).get("tier"),
        "portfolio_dd_pct":   dashboard.get("portfolio", {}).get("dd_pct"),
        "concurrent_failures": dashboard.get("portfolio", {}).get("concurrent_failures"),
        "symbols": [
            {
                "symbol":   s.get("symbol"),
                "state":    s.get("state"),
                "metrics":  s.get("metrics"),
            }
            for s in dashboard.get("symbols", [])
        ],
    }


def get_recent_signals(*, tenant_id: int, limit: int = 10, since_hours: int = 24) -> dict:  # noqa: ARG001
    """Most recent signals. System-wide read (the scanner emits the same
    signal for every tenant; the per-user split lives in the dispatcher
    layer added in B.4 #257)."""
    from db.signals import get_scans
    with snapshot_connection() as con:
        rows = get_scans(con, limit=limit, only_signals=True, since_hours=since_hours)
    return {
        "signals": [
            {
                "symbol":    r.get("symbol"),
                "score":     r.get("score"),
                "direction": r.get("direction"),
                "ts":        r.get("ts"),
                "estado":    r.get("estado"),
            }
            for r in rows
        ]
    }


def get_closed_trades(*, tenant_id: int, window: str = "30d") -> dict:
    """Closed positions for this tenant, windowed.

    The window is pushed into SQL via `db_get_positions(since=...)` so
    we don't load the full history just to filter it down in Python
    (PR #403 review issue 3).
    """
    from datetime import datetime, timedelta, timezone
    from db.positions import db_get_positions

    with snapshot_connection() as con:
        if window == "all":
            rows = db_get_positions(con, status="closed", tenant_id=tenant_id)
        else:
            days = {"7d": 7, "30d": 30, "90d": 90}.get(window, 30)
            cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            rows = db_get_positions(
                con, status="closed", tenant_id=tenant_id, since=cutoff_iso,
            )
    return {"trades": [_position_to_summary(r) for r in rows]}


def get_tune_proposal(*, tenant_id: int) -> dict:  # noqa: ARG001
    """Latest pending auto-tune proposal. System-wide read (auto-tune
    proposes parameters for the global symbol_overrides; tenant_id is
    accepted for signature uniformity)."""
    from api.tune import tune_latest
    try:
        latest = tune_latest()
    except Exception:  # noqa: BLE001
        log.warning("get_tune_proposal: tune_latest failed", exc_info=True)
        return {"error": "tune_unavailable"}
    if not latest:
        return {"proposal": None}
    # tune_latest returns a dict already; pick fields the model needs.
    return {
        "proposal": {
            "id":           latest.get("id"),
            "ts":           latest.get("ts"),
            "status":       latest.get("status"),
            "summary":      latest.get("summary"),
            "applied":      latest.get("applied"),
        }
    }


# ── Dispatch entry point ────────────────────────────────────────────────


# Map of tool name → handler function. The conversation core in Phase 2
# reads this to validate the model's input and dispatch to the right
# handler with tenant_id bound.
#
# Phase 3 (#400) merges in PROPOSE_HANDLERS from propose_handlers.py.
# Propose handlers carry an extra `conversation_id` kwarg (they need it
# to link the persisted proposal back to the conversation in
# agent_side_effects); read-only handlers ignore conversation_id.
from api.agent.tools.propose_handlers import PROPOSE_HANDLERS  # noqa: E402

TOOL_HANDLERS: dict[str, Any] = {
    # Read-only tools (Phase 1).
    "get_portfolio_overview":   get_portfolio_overview,
    "get_positions":            get_positions,
    "get_position_detail":      get_position_detail,
    "get_symbols_with_signals": get_symbols_with_signals,
    "get_symbol_setup":         get_symbol_setup,
    "get_kill_switch_state":    get_kill_switch_state,
    "get_recent_signals":       get_recent_signals,
    "get_closed_trades":        get_closed_trades,
    "get_tune_proposal":        get_tune_proposal,
    # Propose tools (Phase 3). Same dispatch surface; the conversation
    # loop knows to route their output through the proposal SSE event.
    **PROPOSE_HANDLERS,
}


def _is_propose_handler(name: str) -> bool:
    return name in PROPOSE_HANDLERS


def dispatch_tool(
    name: str,
    raw_input: dict,
    *,
    tenant_id: int,
    conversation_id: str = "",
) -> str:
    """Validate the model's tool input against the schema, then run the
    handler with `tenant_id` bound. Returns a JSON string suitable for
    `tool_result.content`.

    Propose handlers (Phase 3) ALSO receive `conversation_id` so the
    persisted proposal in agent_side_effects links back to the
    conversation that emitted it. Read-only handlers don't need it.

    Errors (unknown tool, schema mismatch, handler exception) are
    serialized as `{"error": "..."}` content with the calling convention
    that the conversation core marks the `tool_result` block as
    `is_error: true`. The model then self-corrects on the next turn.
    """
    from api.agent.tools.schemas import TOOL_INPUT_SCHEMAS
    if name not in TOOL_HANDLERS:
        return json.dumps({"error": "unknown_tool", "name": name})
    schema = TOOL_INPUT_SCHEMAS.get(name)
    try:
        validated = schema(**(raw_input or {})) if schema else None
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": "invalid_input", "detail": str(e)})
    try:
        handler = TOOL_HANDLERS[name]
        kwargs = validated.model_dump() if validated else {}
        if _is_propose_handler(name):
            result = handler(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                **kwargs,
            )
        else:
            result = handler(tenant_id=tenant_id, **kwargs)
    except Exception as e:  # noqa: BLE001
        # Full exception (with stack trace) goes to the operator log;
        # the MODEL receives only the closed-enum reason. Internal
        # exception messages can carry file paths, DB column names,
        # secrets in error strings — echoing str(e) to the model is a
        # leak vector that would surface in the assistant's paraphrase
        # back to the user. Phase 5B test
        # test_tool_handler_raise_lands_as_is_error_block locks the
        # invariant.
        log.warning("dispatch_tool: %s raised %s", name, e, exc_info=True)
        return json.dumps({"error": "handler_error"})
    return json.dumps(result, default=str)
