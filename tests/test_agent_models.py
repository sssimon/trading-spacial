"""Phase 4 of epic #400 — per-surface model + tool snapshot tests.

Locks two matrices that drift quietly otherwise:

  1. SURFACE → model:    api/agent/models.py    SURFACE_MODEL_DEFAULTS
  2. SURFACE → tool set: api/agent/tools/registry.py  tools_for_surface()

Both matrices are "stable contracts" that the prompt cache, telemetry,
and per-surface UX all depend on. Quiet drift (somebody flips Dock from
Sonnet to Haiku without telemetry headers-up, or adds a tool to a
surface that doesn't need it and blows the cache prefix) is exactly the
class of bug snapshot tests catch.

If a snapshot fails, the fix is:
  - Confirm the change is deliberate (not a copy-paste mistake).
  - Update the EXPECTED constant in this file.
  - Note WHY in the commit message (the test failure log will quote
    the diff for review).
"""
from __future__ import annotations

from typing import FrozenSet


# ── 1. Per-surface MODEL snapshot ────────────────────────────────────


# Canonical mapping locked. If this matrix needs to change, do it
# deliberately — DO NOT silently update both sides without a
# corresponding commit message explaining why and what it costs.
EXPECTED_SURFACE_MODELS: dict[str, str] = {
    "dock":          "claude-sonnet-4-6",
    "symbol_detail": "claude-haiku-4-5",
    "kill_switch":   "claude-sonnet-4-6",
    "autotune":      "claude-sonnet-4-6",
    "historial":     "claude-haiku-4-5",
}


def test_surface_model_defaults_snapshot():
    """Lock the full SURFACE_MODEL_DEFAULTS map. If this fails, somebody
    changed model selection for at least one surface — update
    EXPECTED_SURFACE_MODELS in this file deliberately."""
    from api.agent.models import SURFACE_MODEL_DEFAULTS

    assert dict(SURFACE_MODEL_DEFAULTS) == EXPECTED_SURFACE_MODELS


def test_allowed_models_snapshot():
    """Closed allowlist of accepted model IDs. Adding a new model is
    deliberate. Removing one is even more so — it can break in-flight
    overrides.

    Fase 2 of the multi-provider epic added `deepseek-chat`.
    Fase 3a adds `deepseek-reasoner` (R1) with reasoning_content
    streaming. Defaults still point at Anthropic — Fase 3b migrates
    them after parity validation.
    """
    from api.agent.models import ALLOWED_MODELS

    assert ALLOWED_MODELS == frozenset({
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
        "claude-opus-4-7",
        "deepseek-chat",
        "deepseek-reasoner",
    })


def test_default_model_for_surface_returns_canonical_value():
    """Helper API — used by the router on every turn."""
    from api.agent.models import default_model_for_surface

    for surface, expected in EXPECTED_SURFACE_MODELS.items():
        assert default_model_for_surface(surface) == expected


def test_default_model_for_surface_unknown_surface_raises_keyerror():
    """Defensive — the router validates the surface via the Literal[...]
    on the request schema, but if a caller bypasses that, the helper
    should fail loudly, not silently fall back to a default that
    obscures the bug."""
    from api.agent.models import default_model_for_surface
    import pytest as _pytest

    with _pytest.raises(KeyError):
        default_model_for_surface("nonexistent_surface")


def test_every_default_is_in_allowlist():
    """The module's import-time check enforces this too, but having it
    in CI as a named test gives an unambiguous failure message ("you
    typo'd a model id in defaults") instead of an ImportError stack
    trace at process start."""
    from api.agent.models import ALLOWED_MODELS, SURFACE_MODEL_DEFAULTS

    for surface, model in SURFACE_MODEL_DEFAULTS.items():
        assert model in ALLOWED_MODELS, (
            f"{surface} maps to {model!r} which is not in ALLOWED_MODELS"
        )


# ── 2. Per-surface TOOL snapshot ──────────────────────────────────────


# Exact tool name set per surface, locked. Order does not matter (the
# registry preserves catalog order for cache stability — that's an
# implementation detail covered by a separate test). What this guards is
# the SET membership: which tools the model sees on each surface.
#
# If the diff on this constant is non-trivial, the reviewer should ask:
#
#   - Does the new tool break the prompt-cache prefix (it'll change
#     the cached tools-block bytes on the surfaces that get it)?
#   - Does the removed tool leave the model without a way to answer
#     a class of question the surface advertises in its micro-prompt?
#   - Does adding a tool to a propose-only surface (kill_switch,
#     autotune) expose a new action path that needs role-checks
#     (see api/agent/router.py _ADMIN_ONLY_ACTIONS)?
EXPECTED_SURFACE_TOOLS: dict[str, FrozenSet[str]] = {
    "dock": frozenset({
        "get_portfolio_overview",
        "get_positions",
        "get_position_detail",
        "get_symbols_with_signals",
        "get_symbol_setup",
        "get_kill_switch_state",
        "get_recent_signals",
        "get_closed_trades",
        "get_tune_proposal",
        "propose_close_position",
        "propose_reactivate_symbol",
        "propose_apply_tune",
    }),
    "symbol_detail": frozenset({
        "get_positions",
        "get_position_detail",
        "get_symbol_setup",
        "get_recent_signals",
    }),
    "kill_switch": frozenset({
        "get_portfolio_overview",
        "get_kill_switch_state",
        "propose_reactivate_symbol",
    }),
    "autotune": frozenset({
        "get_closed_trades",
        "get_tune_proposal",
        "propose_apply_tune",
    }),
    "historial": frozenset({
        "get_closed_trades",
    }),
}


def test_tool_subset_per_surface_snapshot():
    """Lock the full tool subset per surface. Failures are real signal —
    the matrix above is the contract between the model's reasoning
    capabilities and the UI surface it's reasoning on."""
    from api.agent.tools.registry import tools_for_surface

    for surface, expected in EXPECTED_SURFACE_TOOLS.items():
        actual = frozenset(t.name for t in tools_for_surface(surface))
        assert actual == expected, (
            f"surface {surface!r}: expected {sorted(expected)}, "
            f"got {sorted(actual)}"
        )


def test_no_surface_lost_tools_silently():
    """Defense: the union of per-surface tool sets must cover every
    tool in the catalog. A tool that's declared in the catalog but
    exposed on zero surfaces is dead weight at best and a wire-shape
    surprise at worst."""
    from api.agent.tools.registry import TOOL_CATALOG, tools_for_surface

    all_catalog_names = {t.name for t in TOOL_CATALOG}
    exposed_anywhere: set[str] = set()
    for surface in EXPECTED_SURFACE_TOOLS:
        exposed_anywhere.update(t.name for t in tools_for_surface(surface))
    assert all_catalog_names == exposed_anywhere, (
        "Some tools in TOOL_CATALOG are not exposed on any surface: "
        f"{all_catalog_names - exposed_anywhere}"
    )


# ── 3. Cross-surface invariants ──────────────────────────────────────


def test_surfaces_match_prompts_module():
    """Every surface in SURFACE_MODEL_DEFAULTS must have a matching
    micro-prompt in api/agent/prompts/surfaces.py. The runtime falls
    back to dock for an unknown surface (defensive); this test makes
    the matrix authoritative instead — adding a surface requires the
    same set in both places."""
    from api.agent.models import SURFACE_MODEL_DEFAULTS
    from api.agent.prompts.surfaces import SURFACE_PROMPTS

    assert set(SURFACE_MODEL_DEFAULTS.keys()) == set(SURFACE_PROMPTS.keys())


def test_surfaces_match_router_literal():
    """Every surface in SURFACE_MODEL_DEFAULTS must also appear in the
    Literal[...] on api/agent/router.py's _AgentTurnRequest.surface, or
    the router will 422 a request for a surface the rest of the system
    knows about.

    Implementation note: we walk the AST of router.py and collect string
    literals from every `Literal[...]` subscript we find, then compare
    the union against SURFACE_MODEL_DEFAULTS.keys(). PR #407 review
    issue 1: an earlier version of this test used a substring `surface in
    source` check, which produces false negatives when somebody removes
    the Literal annotation but the surface name still appears elsewhere
    in the module (a log line, an example, etc). The AST walk catches
    that drift; the substring version did not.
    """
    import ast
    import inspect
    from api.agent import router as _router
    from api.agent.models import SURFACE_MODEL_DEFAULTS

    source = inspect.getsource(_router)
    tree = ast.parse(source)

    literal_strings: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        # Pydantic v2 schemas use `typing.Literal[...]`. We accept either
        # `Literal` or `typing.Literal` as the subscript target — anything
        # named Literal at the end of an attribute chain.
        target = node.value
        name = (
            target.id if isinstance(target, ast.Name)
            else target.attr if isinstance(target, ast.Attribute)
            else None
        )
        if name != "Literal":
            continue
        # The slice can be a single ast.Constant (one-arg Literal) or an
        # ast.Tuple (multi-arg Literal). Older 3.x versions wrapped slice
        # in ast.Index; 3.9+ collapsed it. We support both shapes
        # defensively even though current CI runs on 3.12+.
        slc = node.slice
        if hasattr(ast, "Index") and isinstance(slc, ast.Index):  # py <= 3.8
            slc = slc.value  # type: ignore[attr-defined]
        elts = slc.elts if isinstance(slc, ast.Tuple) else [slc]
        for elt in elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                literal_strings.add(elt.value)

    declared = set(SURFACE_MODEL_DEFAULTS.keys())
    missing_from_router = declared - literal_strings
    assert not missing_from_router, (
        f"surfaces in SURFACE_MODEL_DEFAULTS missing from any Literal[...] "
        f"in api/agent/router.py: {sorted(missing_from_router)} — "
        f"requests for these will 422"
    )
