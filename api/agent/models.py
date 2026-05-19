"""Per-surface model selection — Phase 4 of epic #400.

Pre-reg §4.3 ("ningún surface mezcla modelos dentro de la misma sesión"):
the surface is fixed at session-start, the model is bound to that surface
deterministically, and the prompt-cache prefix stays warm because tools +
system prompt are stable for the surface.

This module is the canonical source for both the per-surface default and
the allowlist of legitimate model IDs. The router used to declare these
inline; centralizing them lets us:

  - re-use the same defaults from a future telemetry endpoint that
    reports "what model are dock turns actually running on";
  - add a single test surface for the invariant "every declared surface
    maps to a model in the allowlist", which fires at import time and at
    test time (defense in depth, since the import-time check rolls back
    a broken deploy at process start);
  - keep `api/agent/router.py` focused on HTTP plumbing.

Pricing source: the canonical $/1M-token map lives in
`api/agent/loop.py:_MODEL_PRICING` (it's the dict the cost calculator
actually reads). DO NOT duplicate numeric prices here — when Anthropic
ships a price change, there is exactly one number to update, in one
file (PR #407 review issue 2).

Context-window capacities (informational, used to pick the right
surface model — not for billing): Opus 4.7 + Sonnet 4.6 are 1M; Haiku
4.5 is 200K. Surfaces that need broad portfolio synthesis use Sonnet
(or Opus on override); surfaces scoped to a single symbol or windowed
history use Haiku — narrower context, faster, cheaper. The user can
flip to Opus on demand via the `model` override on the turn request
(the router enforces the allowlist).
"""
from __future__ import annotations


# Canonical mapping. Adding a new surface here REQUIRES adding it to:
#   - api/agent/prompts/surfaces.py  (micro-prompt)
#   - api/agent/tools/registry.py    (tool subset via the `surfaces` field)
#   - frontend/src/agent/surfaces.ts (UI metadata)
#   - the Literal[...] in api/agent/router.py's _AgentTurnRequest
# The invariant check below + test_models_invariants in
# tests/test_agent_models.py catches the first two; a snapshot test on
# the tool subset catches drift in the third.
SURFACE_MODEL_DEFAULTS: dict[str, str] = {
    "dock":          "claude-sonnet-4-6",
    "symbol_detail": "claude-haiku-4-5",
    "kill_switch":   "claude-sonnet-4-6",
    "autotune":      "claude-sonnet-4-6",
    "historial":     "claude-haiku-4-5",
}


# Closed allowlist of model IDs accepted by the turn endpoint. A user-
# supplied `model` override on the turn request is rejected unless it
# appears here. New models go through deliberate code review — pricing
# + capability gates live in this set, not in env vars.
ALLOWED_MODELS: frozenset[str] = frozenset({
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
    "claude-opus-4-7",
})


def default_model_for_surface(surface: str) -> str:
    """Return the default model id for a surface. Raises KeyError if the
    surface is unknown — that's the right behavior at the call site,
    since the router has already validated the surface via the Literal
    on the request schema before we get here."""
    return SURFACE_MODEL_DEFAULTS[surface]


# Import-time invariant: every default must be in the allowlist. If a
# typo lands in SURFACE_MODEL_DEFAULTS the process refuses to start. We
# use RuntimeError (not assert) because `python -O` strips asserts and
# we want this check to fire in production too (mirror of the same
# pattern in api/agent/tools/registry.py).
_bad = {s: m for s, m in SURFACE_MODEL_DEFAULTS.items() if m not in ALLOWED_MODELS}
if _bad:
    raise RuntimeError(
        f"SURFACE_MODEL_DEFAULTS contains models not in ALLOWED_MODELS: {_bad}"
    )
