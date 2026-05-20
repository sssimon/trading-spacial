"""Provider registry — maps model id prefix → provider instance.

Convention (pre-reg §2.6 of the multi-provider spec, formalized in
PR #411 review):

  - Each provider declares a canonical prefix for its model ids.
  - Adding a new provider means:
      1. Implement `LLMProvider` in api/agent/providers/{vendor}_adapter.py.
      2. Register the prefix in `PROVIDER_BY_PREFIX` below.
      3. Add its model ids to `ALLOWED_MODELS` in api/agent/models.py.
      4. Parametrize §11 tests with a `Fake{Vendor}Provider`.

Invariants enforced in CI by tests/test_provider_registry.py:
  - Every model in ALLOWED_MODELS matches exactly one PROVIDER_BY_PREFIX
    prefix.
  - Every prefix has at least one model in ALLOWED_MODELS (no orphans).
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("api.agent.providers.registry")


class UnknownProviderError(ValueError):
    """Raised when get_provider_for_model can't resolve a model id to a
    known prefix. The router catches this and returns 400."""


# ── Prefix → factory mapping ────────────────────────────────────────


def _anthropic_factory() -> Any:
    """Lazy: construct AnthropicProvider with a real SDK client. The
    SDK import happens inside build_anthropic_client; tests that don't
    have the anthropic package installed never hit this path because
    they inject a FakeAnthropicProvider via dependency_overrides."""
    from api.agent.providers.anthropic_adapter import (
        AnthropicProvider, build_anthropic_client,
    )
    return AnthropicProvider(client=build_anthropic_client())


def _deepseek_factory() -> Any:
    """Lazy: construct DeepSeekProvider. DS uses raw httpx (no SDK to
    import), so the factory only needs DEEPSEEK_API_KEY at construction
    time. Tests inject FakeDeepSeekProvider via dependency_overrides
    and never hit this path."""
    from api.agent.providers.deepseek_adapter import (
        DeepSeekProvider, build_deepseek_client_kwargs,
    )
    return DeepSeekProvider(**build_deepseek_client_kwargs())


PROVIDER_BY_PREFIX: dict[str, Any] = {
    "claude":   _anthropic_factory,
    "deepseek": _deepseek_factory,
}


# Prefix → canonical provider name. Necessary because the prefix is a
# property of the model ID series (claude-*, deepseek-*) while the name
# is a property of the vendor (anthropic, deepseek). For DeepSeek these
# happen to coincide; for Anthropic they don't. Single source of truth
# for both directions of the mapping.
PROVIDER_NAME_BY_PREFIX: dict[str, str] = {
    "claude":   "anthropic",
    "deepseek": "deepseek",
}


# Mapping from provider name → adapter class, for callers that need
# to call non-stream methods (format_system_blocks, format_tools,
# estimate_cost) WITHOUT constructing an SDK client. Phase 2 of the
# multi-provider epic + PR #412 review pickup 1: cleaner than the
# previous if/elif by provider_name in loop.py.
def get_provider_class_for_name(provider_name: str):
    """Return the adapter CLASS (not an instance) for cheap operations
    like tool formatting and cost calculation. Caller is responsible
    for constructing an instance (cheap — no SDK required for these
    methods)."""
    if provider_name == "anthropic":
        from api.agent.providers.anthropic_adapter import AnthropicProvider
        return AnthropicProvider
    if provider_name == "deepseek":
        from api.agent.providers.deepseek_adapter import DeepSeekProvider
        return DeepSeekProvider
    raise UnknownProviderError(
        f"no provider class registered for name {provider_name!r}"
    )


def get_provider_class_for_model(model: str):
    """Convenience: resolve a model id to its adapter CLASS in one step.

    Use this for cost calculation, tool formatting — anything that
    needs the provider's logic but NOT a live SDK connection. The
    caller can construct an instance cheaply (no API key validation,
    no SDK import for the connection layer).
    """
    for prefix, name in PROVIDER_NAME_BY_PREFIX.items():
        if model.startswith(f"{prefix}-"):
            return get_provider_class_for_name(name)
    raise UnknownProviderError(
        f"no provider class for model {model!r}"
    )


# ── Resolver ─────────────────────────────────────────────────────────


def get_provider_for_model(model: str) -> Any:
    """Resolve a model id to its provider instance.

    Iterates `PROVIDER_BY_PREFIX` in declaration order and uses the
    first matching prefix. Raises `UnknownProviderError` if no prefix
    matches — that's a 400, not a 500. The router translates.

    Note: this calls the factory fresh on every invocation. Production
    use should NOT rely on this for caching the SDK client itself —
    the factory is cheap (it just news up an AsyncAnthropic), but if
    a future provider has expensive setup, wrap it in lru_cache HERE,
    not at the call site.
    """
    for prefix, factory in PROVIDER_BY_PREFIX.items():
        if model.startswith(f"{prefix}-"):
            return factory()
    raise UnknownProviderError(
        f"no provider registered for model {model!r}; "
        f"add a prefix to PROVIDER_BY_PREFIX"
    )
