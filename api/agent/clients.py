"""FastAPI dependency that returns the active LLMProvider.

Phase 1 of the multi-provider epic relocated the SDK construction logic
into `api.agent.providers.anthropic_adapter`. This module now exists
only as a thin shim so the router's `Depends(get_anthropic_client)`
signature keeps working without changes — the dependency still does the
same job (gate on agent_status, then return the provider for the active
default model) but it returns an `LLMProvider` instead of a raw
`AsyncAnthropic`.

The function is still named `get_anthropic_client` for backward
compatibility — every endpoint test that does
`app.dependency_overrides[get_anthropic_client] = lambda: fake`
keeps working. A future PR may rename to `get_llm_provider` once the
multi-provider epic stabilizes; deferred.

Failure modes (unchanged from Phase 0):
  - agent_status disabled → 503 with closed-enum reason.
  - SDK not installed → 503 agent_disabled (no leak of dependency).
  - Provider for the default model can't resolve a key → handled
    upstream by get_agent_status's §2.7 dual-key check; we never get
    here in that case.
"""
from __future__ import annotations

import logging

from fastapi import HTTPException

log = logging.getLogger("api.agent.clients")


def get_anthropic_client():
    """FastAPI dependency — status gate ONLY.

    Fase 3b of the multi-provider epic + PR #415 review issue (critical
    bug): the dep no longer returns a provider instance. The handler
    resolves the provider PER-REQUEST via
    `api.agent.router._resolve_provider_for_model(body.model)` so an
    override like `body.model = "claude-opus-4-7"` actually routes to
    AnthropicProvider, regardless of what the surface default points
    to. Pre-fix, the dep resolved the default's provider (DS post-
    migration) and the override path silently routed claude-* model
    ids to DeepSeek's API → 400 → friendly fallback. Operator who
    relied on Opus override discovered the bug in production.

    The function name stays `get_anthropic_client` for backward-compat
    with 13+ test `dependency_overrides[get_anthropic_client]` call
    sites. The override semantics also shift:
      - Pre-fix: override replaces the provider used in the loop.
      - Post-fix: override is just a status-gate bypass (return value
        unused). To swap the provider, monkeypatch
        `api.agent.router._resolve_provider_for_model` instead.

    Returns True on success (sentinel — the value is unused; the act
    of resolving without raising IS the OK signal).
    """
    # Local import to dodge circulars (api.agent.config doesn't import
    # this module, but the router imports both).
    from api.agent.config import get_agent_status  # noqa: PLC0415
    status = get_agent_status()
    if not status.enabled:
        raise HTTPException(status_code=503, detail=status.reason)
    return True
