"""Phase 1 of the multi-provider epic — provider registry + adapter tests.

Two layers of coverage here:

  1. REGISTRY INVARIANTS — the §2.6 contract is enforced in CI:
     - Every model in ALLOWED_MODELS matches exactly one prefix in
       PROVIDER_BY_PREFIX.
     - Every prefix has at least one model in ALLOWED_MODELS (no
       orphans).
     - get_provider_for_model raises UnknownProviderError on a prefix
       that doesn't match anything registered.

  2. ADAPTER UNIT TESTS — AnthropicProvider's individual methods,
     decoupled from the loop:
     - format_system_blocks adds cache_control:ephemeral.
     - format_tools emits {name, description, input_schema} shape.
     - estimate_cost uses the published per-model pricing table.
     - blocks_to_api_shape coerces typed blocks to dict shape.

If §11 critical tests pass for the loop AND these unit tests pass for
the adapter, the abstraction is wired correctly. Adding Phase 2's
DeepSeek adapter only needs an equivalent test file for that adapter.
"""
from __future__ import annotations

import pytest


# ── Registry invariants ──────────────────────────────────────────


def test_every_allowed_model_resolves_to_a_provider():
    """For each model in ALLOWED_MODELS, get_provider_for_model returns
    a valid provider. No orphan models."""
    from api.agent.models import ALLOWED_MODELS
    from api.agent.providers.registry import (
        PROVIDER_BY_PREFIX, get_provider_for_model,
    )

    for model in ALLOWED_MODELS:
        # We don't actually call the factory (which constructs an SDK
        # client and needs the env var); we only verify the prefix
        # resolves to a known entry in PROVIDER_BY_PREFIX.
        matched = [p for p in PROVIDER_BY_PREFIX if model.startswith(f"{p}-")]
        assert len(matched) == 1, (
            f"model {model!r} matches {len(matched)} prefixes "
            f"({matched}); expected exactly 1"
        )


def test_no_orphan_prefixes():
    """Every prefix in PROVIDER_BY_PREFIX must have at least one model
    in ALLOWED_MODELS. An orphan prefix is dead code — it would never
    fire because no model id reaches it."""
    from api.agent.models import ALLOWED_MODELS
    from api.agent.providers.registry import PROVIDER_BY_PREFIX

    for prefix in PROVIDER_BY_PREFIX:
        matched = [m for m in ALLOWED_MODELS if m.startswith(f"{prefix}-")]
        assert matched, (
            f"prefix {prefix!r} has no matching models in ALLOWED_MODELS"
        )


def test_unknown_model_id_raises_unknown_provider_error():
    """A model id that doesn't match any prefix triggers
    UnknownProviderError. The router catches this and returns 400."""
    from api.agent.providers.registry import (
        UnknownProviderError, get_provider_for_model,
    )

    with pytest.raises(UnknownProviderError):
        get_provider_for_model("gpt-5-turbo")
    with pytest.raises(UnknownProviderError):
        get_provider_for_model("totally-fake-model")


def test_anthropic_factory_raises_unknown_provider_when_key_missing(monkeypatch):
    """Deploys that don't set ANTHROPIC_API_KEY (DS-only setup per
    runbook §0.2) must surface as 400 model_not_allowed on claude-*
    overrides — NOT 500 KeyError from os.environ[...] in the SDK
    client constructor. Symmetric to DS path.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from api.agent.providers.registry import (
        PROVIDER_BY_PREFIX, UnknownProviderError,
    )

    with pytest.raises(UnknownProviderError):
        PROVIDER_BY_PREFIX["claude"]()


def test_deepseek_factory_raises_unknown_provider_when_key_missing(monkeypatch):
    """Symmetric defensive coverage: DEEPSEEK_API_KEY missing must
    raise UnknownProviderError, not propagate the ValueError that
    DeepSeekProvider._ensure_api_key throws at construction.
    Normally /agent/status short-circuits to agent_disabled before
    reaching this path, but the factory must still fail safe.
    """
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    from api.agent.providers.registry import (
        PROVIDER_BY_PREFIX, UnknownProviderError,
    )

    with pytest.raises(UnknownProviderError):
        PROVIDER_BY_PREFIX["deepseek"]()


def test_get_provider_for_model_for_claude_resolves(monkeypatch):
    """For a real claude model id, the registry calls the Anthropic
    factory. We can't easily test against the real SDK in a test
    environment without anthropic installed; we monkeypatch the
    factory to return a sentinel and verify dispatch."""
    from api.agent.providers import registry

    sentinel = object()
    monkeypatch.setitem(
        registry.PROVIDER_BY_PREFIX, "claude", lambda: sentinel,
    )
    assert registry.get_provider_for_model("claude-sonnet-4-6") is sentinel
    assert registry.get_provider_for_model("claude-opus-4-7") is sentinel


# ── AnthropicProvider unit tests ─────────────────────────────────


def test_anthropic_provider_supports_claude_models_only():
    from api.agent.providers.anthropic_adapter import AnthropicProvider
    p = AnthropicProvider()
    assert p.supports_model("claude-sonnet-4-6") is True
    assert p.supports_model("claude-opus-4-7") is True
    assert p.supports_model("deepseek-chat") is False
    assert p.supports_model("gpt-5") is False


def test_anthropic_provider_format_system_blocks_wraps_each_with_cache_control():
    from api.agent.providers.anthropic_adapter import AnthropicProvider
    p = AnthropicProvider()
    out = p.format_system_blocks(["hello", "world"])
    assert out == [
        {"type": "text", "text": "hello", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "world", "cache_control": {"type": "ephemeral"}},
    ]


def test_anthropic_provider_format_tools_emits_anthropic_shape():
    """The tool array must have {name, description, input_schema} per
    tool. The input_schema is a JSON schema with `title` keys stripped
    (determinism contract — see §7.5 of epic #400)."""
    from api.agent.providers.anthropic_adapter import AnthropicProvider
    from api.agent.tools.registry import tools_for_surface

    specs = tools_for_surface("dock")
    out = AnthropicProvider().format_tools(specs)
    assert len(out) == len(specs)
    for tool in out:
        assert set(tool.keys()) == {"name", "description", "input_schema"}
        # title stripping: walk the schema and assert no `title` keys remain.
        _assert_no_title(tool["input_schema"])


def _assert_no_title(node):
    """Recursive assertion: no `title` key in the JSON schema tree."""
    if isinstance(node, dict):
        assert "title" not in node, f"unstripped title in node: {node}"
        for v in node.values():
            _assert_no_title(v)
    elif isinstance(node, list):
        for v in node:
            _assert_no_title(v)


def test_anthropic_provider_estimate_cost_matches_known_pricing():
    """1M input + 1M output on Sonnet should be exactly $18 — same
    numbers as the legacy _MODEL_PRICING / _estimate_cost_usd from
    epic #400."""
    from api.agent.providers.anthropic_adapter import AnthropicProvider
    p = AnthropicProvider()
    cost = p.estimate_cost(
        "claude-sonnet-4-6",
        {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
    )
    assert cost == pytest.approx(18.0)


def test_anthropic_provider_estimate_cost_returns_zero_for_unknown_model():
    """Defensive fallback — same behavior as the pre-refactor cost
    helper. An unknown model id (typo, deprecated, not yet added)
    returns 0.0 instead of raising. Phase 1 of the multi-provider epic
    preserves this contract; future epic could decide differently."""
    from api.agent.providers.anthropic_adapter import AnthropicProvider
    assert AnthropicProvider().estimate_cost("claude-bogus-99", {}) == 0.0


def test_anthropic_provider_blocks_to_api_shape_coerces_typed_blocks():
    """SDK content blocks are typed objects (TextBlock, ToolUseBlock)
    that the API rejects when echoed back. The adapter coerces them
    to the {type, text} / {type, id, name, input} dict shape that
    `messages` accepts on the next turn."""
    from api.agent.providers.anthropic_adapter import AnthropicProvider

    class FakeTextBlock:
        type = "text"
        text = "hi"

    class FakeToolUseBlock:
        type = "tool_use"
        id = "toolu_1"
        name = "get_positions"
        input = {"window": "7d"}

    out = AnthropicProvider().blocks_to_api_shape(
        [FakeTextBlock(), FakeToolUseBlock()],
    )
    assert out == [
        {"type": "text", "text": "hi"},
        {"type": "tool_use", "id": "toolu_1", "name": "get_positions",
         "input": {"window": "7d"}},
    ]


# ── LLMProvider protocol — adapter conforms ────────────────────


def test_anthropic_provider_conforms_to_llmprovider_protocol():
    """Structural protocol check — every method that LLMProvider
    declares must be callable on AnthropicProvider. If somebody
    accidentally renames a method on the adapter (or on the protocol),
    this test fires."""
    import inspect
    from api.agent.providers.anthropic_adapter import AnthropicProvider
    from api.agent.providers.base import LLMProvider

    required = [
        m for m in dir(LLMProvider)
        if not m.startswith("_")
    ]
    p = AnthropicProvider()
    for method_name in required:
        assert hasattr(p, method_name), (
            f"AnthropicProvider missing {method_name!r} required by LLMProvider"
        )
