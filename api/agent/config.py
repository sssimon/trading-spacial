"""Agent runtime configuration: feature flag + status resolution.

Intentionally minimal in Phase 0 — owns the contract behind GET /agent/status
documented in §4.4 of the pre-reg. Phase 2 will extend this module with the
per-surface model whitelist, the daily/monthly token budget readers, and
the AGENT_PROPOSAL_SECRET accessor used by HMAC proposal signing.

Spec §6.3 + §13.5: this module NEVER leaks env-var names, .env paths, or
operator-only configuration detail through any field it returns. The
`reason` field is a closed enum of user-safe strings — anything else is
a regression that must trip the no-leak test in tests/test_agent_status.py.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from api.config import load_config

log = logging.getLogger("api.agent.config")


# Closed enum of user-safe status reasons. Never expand this with an
# operator-only string (env var name, path, secret name, etc).
_REASON_OK            = "ok"
_REASON_DISABLED      = "agent_disabled"
_REASON_BREAKER_OPEN  = "breaker_open"   # Phase 5: global circuit breaker


@dataclass(frozen=True)
class AgentStatus:
    """Public status returned by GET /agent/status."""

    enabled: bool
    reason: str


def get_agent_status(cfg: Optional[dict] = None) -> AgentStatus:
    """Resolve the agent's runtime status.

    Precedence (any disabling source wins):
      1. cfg["agent"]["enabled"] is explicitly False  → agent_disabled
      2. ANTHROPIC_API_KEY missing or empty            → treated identically
         (we deliberately collapse "key missing" into the same reason as
         "operator disabled" so the wire format never leaks the existence
         of an env var named ANTHROPIC_API_KEY)
      3. Global circuit breaker tripped (explicit cfg.agent.breaker_open
         OR automatic 24h global spend cap exceeded) → breaker_open
      4. Otherwise                                     → enabled

    breaker_open is intentionally a DIFFERENT closed-enum reason from
    agent_disabled — the frontend can render different UX ("system
    temporarily halted" vs "feature off") and the operator-facing
    metrics page can tell the two states apart.
    """
    cfg = cfg if cfg is not None else load_config()
    agent_cfg = cfg.get("agent") or {}
    if agent_cfg.get("enabled") is False:
        return AgentStatus(enabled=False, reason=_REASON_DISABLED)

    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        return AgentStatus(enabled=False, reason=_REASON_DISABLED)

    # Local import to avoid the circular: circuit_breaker reads cfg via
    # api.config, which this module also imports — and a top-level
    # import would also force every Phase 0 test to set up a DB before
    # status could be probed.
    from api.agent.circuit_breaker import is_breaker_tripped
    if is_breaker_tripped(cfg):
        return AgentStatus(enabled=False, reason=_REASON_BREAKER_OPEN)

    return AgentStatus(enabled=True, reason=_REASON_OK)
