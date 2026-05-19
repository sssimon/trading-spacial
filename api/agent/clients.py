"""Lazy construction of the Anthropic SDK client.

This module exists as a FastAPI dependency seam so tests can override
the production AsyncAnthropic with FakeAnthropicClient via
`app.dependency_overrides[get_anthropic_client] = lambda: fake`. Phase 2
of epic #400.

In production: returns a configured `anthropic.AsyncAnthropic`. The SDK
is imported lazily — until ANTHROPIC_API_KEY is configured and the
status endpoint reports enabled, the import is never triggered, so
operators that never enable the copilot don't carry the dependency
cost.

Failure modes:
  - SDK not installed → 503 agent_disabled (treated identically to "key
    missing", same closed-enum reason).
  - Key not in env → handled upstream by get_agent_status; this
    function only runs after that check, so the env var is guaranteed.
"""
from __future__ import annotations

import logging
import os

from fastapi import HTTPException

log = logging.getLogger("api.agent.clients")


def get_anthropic_client():
    """FastAPI dependency that returns a configured Anthropic async client.

    In tests, override via `app.dependency_overrides[get_anthropic_client]`
    with a function that returns a FakeAnthropicClient instance.
    """
    try:
        from anthropic import AsyncAnthropic  # noqa: PLC0415
    except ImportError as e:
        log.error("anthropic SDK not installed: %s", e)
        # Same closed-enum reason as a missing key — the wire format
        # never reveals whether it's a missing dependency vs a missing
        # secret. Pre-reg §11.7.
        raise HTTPException(status_code=503, detail="agent_disabled")
    return AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"].strip())
