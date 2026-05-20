"""Provider abstraction layer for the copilot.

Phase 1 of the multi-provider epic (post-#400). The loop talks to an
LLMProvider; the provider knows the wire format of its SDK / HTTP
endpoint. Adding a new provider means dropping a new module here and
registering it — no changes to the loop, audit, quotas, breaker, or
proposals layer.

Layers (lowest → highest):
  - base.py: LLMProvider protocol + LLMEvent closed-enum dataclasses
  - anthropic_adapter.py: wraps anthropic.AsyncAnthropic
  - registry.py: maps model id prefix → provider instance

Pre-reg: docs/superpowers/specs/es/2026-05-20-multi-provider-copilot-pre-reg.md
"""
from __future__ import annotations

from api.agent.providers.base import (
    LLMEvent,
    LLMProvider,
    LLMReasoningDelta,
    LLMStreamEnd,
    LLMTextDelta,
    LLMToolUseEnd,
    LLMToolUseStart,
)
from api.agent.providers.registry import (
    PROVIDER_BY_PREFIX,
    UnknownProviderError,
    get_provider_for_model,
)

__all__ = [
    "LLMEvent",
    "LLMProvider",
    "LLMReasoningDelta",
    "LLMStreamEnd",
    "LLMTextDelta",
    "LLMToolUseEnd",
    "LLMToolUseStart",
    "PROVIDER_BY_PREFIX",
    "UnknownProviderError",
    "get_provider_for_model",
]
