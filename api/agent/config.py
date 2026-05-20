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
      2. Default model's provider has no API key       → agent_disabled
         (§2.7 of the multi-provider epic: we check the key of the
         provider that serves the default surface model — NOT "any
         key set". If the default is deepseek-chat and only
         ANTHROPIC_API_KEY is set, status returns disabled BEFORE
         the first turn fails inside the adapter.)
      3. Global circuit breaker tripped (explicit cfg.agent.breaker_open
         OR automatic 24h global spend cap exceeded) → breaker_open
      4. Otherwise                                     → enabled

    breaker_open is intentionally a DIFFERENT closed-enum reason from
    agent_disabled — the frontend can render different UX ("system
    temporarily halted" vs "feature off") and the operator-facing
    metrics page can tell the two states apart.

    Wire-format invariant (pre-reg §13.5): the response never leaks
    env-var names. "Key missing" and "operator disabled" collapse
    into agent_disabled.
    """
    cfg = cfg if cfg is not None else load_config()
    agent_cfg = cfg.get("agent") or {}
    if agent_cfg.get("enabled") is False:
        return AgentStatus(enabled=False, reason=_REASON_DISABLED)

    # §2.7: check the API key of the default model's provider, not
    # ANTHROPIC_API_KEY directly. The default surface is "dock";
    # whichever provider serves dock today determines the key we
    # need.
    if not _default_provider_has_key():
        return AgentStatus(enabled=False, reason=_REASON_DISABLED)

    # Local import to avoid the circular: circuit_breaker reads cfg via
    # api.config, which this module also imports — and a top-level
    # import would also force every Phase 0 test to set up a DB before
    # status could be probed.
    from api.agent.circuit_breaker import is_breaker_tripped
    if is_breaker_tripped(cfg):
        return AgentStatus(enabled=False, reason=_REASON_BREAKER_OPEN)

    return AgentStatus(enabled=True, reason=_REASON_OK)


def _default_provider_has_key() -> bool:
    """True if the provider that serves the default surface ("dock") has
    its API key configured. Decoupled from the status function so the
    import chain is testable in isolation.

    Returns False on any resolution error (unknown provider, SDK import
    failure, etc) — collapses every failure mode into the same
    agent_disabled closed-enum reason.
    """
    try:
        from api.agent.models import default_model_for_surface
        from api.agent.providers.registry import (
            UnknownProviderError, get_provider_class_for_model,
        )
        default_model = default_model_for_surface("dock")
        provider_cls = get_provider_class_for_model(default_model)
        # Construct a no-key instance. has_api_key() reads from the
        # right env var for the provider's vendor.
        return provider_cls().has_api_key()
    except Exception:  # noqa: BLE001
        log.warning("_default_provider_has_key resolution failed", exc_info=True)
        return False
