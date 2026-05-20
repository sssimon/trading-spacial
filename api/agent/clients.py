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
    """FastAPI dependency that returns the active LLMProvider.

    PHASE 1 NOTE: returns an LLMProvider, not a raw Anthropic SDK
    client. The name is kept for backward-compat with the 13+ test
    `dependency_overrides[get_anthropic_client]` call sites. Tests
    inject `FakeAnthropicProvider` which implements the protocol.

    Returns the provider whose default surface model the registry
    resolves. Today that's always Anthropic (default for `dock` is
    `claude-sonnet-4-6`). Phase 3 of the multi-provider epic flips
    the default to DeepSeek and this resolver follows.
    """
    # Local import to dodge circulars (api.agent.config doesn't import
    # this module, but the router imports both).
    from api.agent.config import get_agent_status  # noqa: PLC0415
    status = get_agent_status()
    if not status.enabled:
        raise HTTPException(status_code=503, detail=status.reason)

    # Resolve the provider for the default surface model. We use "dock"
    # as the canonical reference surface — the actual model the request
    # ends up using may differ (the router supports per-request model
    # override), but the dep injection happens before the body sees the
    # request, so we resolve against the default at this point.
    from api.agent.models import default_model_for_surface  # noqa: PLC0415
    from api.agent.providers.registry import (  # noqa: PLC0415
        UnknownProviderError, get_provider_for_model,
    )
    default_model = default_model_for_surface("dock")
    try:
        return get_provider_for_model(default_model)
    except UnknownProviderError:
        log.error("default model %s has no registered provider", default_model)
        raise HTTPException(status_code=503, detail="agent_disabled")
    except ImportError as e:
        # The provider's SDK (anthropic, etc) isn't installed — same
        # closed-enum response as Phase 0's "key missing".
        log.error("provider SDK not installed: %s", e)
        raise HTTPException(status_code=503, detail="agent_disabled")
