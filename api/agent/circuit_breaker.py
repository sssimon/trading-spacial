"""Global circuit breaker for the copilot — Phase 5 of epic #400.

The breaker is the operator's emergency kill-switch. Two trip sources:

  1. EXPLICIT — `cfg.agent.breaker_open = true` in config.json. Flipped
     by hand when something looks off in metrics (cost spike, weird
     error rate, suspect prompt injection). Persists across restarts.
  2. AUTOMATIC — global spend in the rolling 24h window crossed the
     daily cap from `cfg.agent.global_daily_usd_cap`. Re-checked on
     every turn pre-flight via is_breaker_tripped(). Resets implicitly
     when the 24h tail rolls past the spike (no manual intervention).

When tripped, GET /agent/status returns `reason="breaker_open"` and
POST /agent/turn 503s with the same closed-enum reason. Per-tenant
quota checks (api/agent/quotas.py) are independent — they 429, not
503. The two layers compose:

  - breaker_open ⇒ entire feature halted, no turns for anyone
  - quota_exceeded ⇒ one tenant blocked, others unaffected

The §12 rollout plan mentions a `$5/día` cap "as kill-switch implícito"
for the first week. That's exactly what `global_daily_usd_cap` is.

Pre-reg §13.5: closed-enum reasons only. NEVER leak the operator's
config path, the env var name, or numeric thresholds via the wire.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from api.config import load_config
from db.connection import get_db

log = logging.getLogger("api.agent.circuit_breaker")


# Default global cap. Used if cfg.agent.global_daily_usd_cap is missing
# or non-numeric. Conservative — the rollout plan starts at $5/día.
DEFAULT_GLOBAL_DAILY_USD_CAP = 5.00


# Closed-enum reason surfaced to the wire when the breaker is open.
REASON_BREAKER_OPEN = "breaker_open"


def is_breaker_tripped(cfg: Optional[dict] = None) -> bool:
    """Return True if any trip source fired. Cheap-on-success: the
    explicit flag is a dict lookup; the automatic spend check is one
    SELECT against agent_conversations with a covering index.

    Conservative on DB failure: if the spend query fails, we DON'T trip
    the breaker (we'd cut everyone off on a DB hiccup). We log + return
    False, mirroring quotas.record_spend's fail-quiet discipline. The
    spike would still get caught on the next turn after the DB recovers.
    """
    cfg = cfg if cfg is not None else load_config()
    agent_cfg = cfg.get("agent") or {}

    # 1. Explicit operator trip — flag wins regardless of spend.
    if agent_cfg.get("breaker_open") is True:
        return True

    # 2. Automatic spend trip. Read the cap from cfg; fall back to the
    # conservative default if missing or malformed.
    raw_cap = agent_cfg.get("global_daily_usd_cap", DEFAULT_GLOBAL_DAILY_USD_CAP)
    try:
        cap = float(raw_cap)
    except (TypeError, ValueError):
        cap = DEFAULT_GLOBAL_DAILY_USD_CAP

    if cap <= 0:
        # An operator who sets cap=0 intends "no spend allowed" — that's
        # the explicit trip, route it through the same enum.
        return True

    try:
        spent_24h = _global_spend_last_24h()
    except Exception:  # noqa: BLE001
        log.warning(
            "is_breaker_tripped: 24h spend query failed; failing open (not tripping). "
            "If this persists, the breaker is effectively disabled — operator should "
            "investigate the agent_conversations table.",
            exc_info=True,
        )
        return False

    return spent_24h >= cap


def _global_spend_last_24h() -> float:
    """Sum agent_conversations.cost_usd over the rolling 24h window.
    Indexed by ts; SQLite handles this cheaply for the volumes we
    expect (< 10K rows/day at full saturation)."""
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=24)).isoformat()
    con = get_db()
    try:
        row = con.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS total "
            "FROM agent_conversations "
            "WHERE ts >= ?",
            (cutoff,),
        ).fetchone()
    finally:
        con.close()
    return float(dict(row)["total"] or 0.0)


def current_global_spend_24h() -> float:
    """Read-only accessor for the metrics endpoint. Same query as the
    internal helper; promoted to public so callers don't pierce the
    underscore convention."""
    return _global_spend_last_24h()
