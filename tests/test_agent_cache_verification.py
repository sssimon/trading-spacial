"""Phase 5B of epic #400 — §11.6 prompt cache verification.

The 4-layer cache prefix (persona, tool-docs, invariants, surface
micro-prompt) is the spine of the cost model. Pre-reg §7 says the
prefix must be deterministic + cache-controlled; this test locks
the invariant against silent drift.

Two layers of assertion:

  1. STRUCTURAL — build_system_blocks() returns exactly 4 blocks,
     each with cache_control={"type":"ephemeral"}. If somebody drops
     a breakpoint or adds a 5th, the test fires.
  2. WIRE — drive run_turn with a FakeAnthropicClient that reports
     cache_creation > 0 on turn 1 and cache_read > 0 on turn 2.
     Verify both values land in MessageEnd.usage and survive the
     multi-hop accumulation logic.

Pre-reg §7.5: silent invalidators (whitespace, ordering, dict iteration
order) would break the cache without raising. Other tests in
test_agent_loop.py cover determinism (system_blocks_are_deterministic_
across_calls); this file covers REPORTED usage.
"""
from __future__ import annotations

import pytest

from tests._fakes import FakeAnthropicClient, FakeTurnBuilder


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import btc_api
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    yield db_path


async def _collect(events_iter):
    out = []
    async for ev in events_iter:
        out.append(ev)
    return out


# ── 1. Structural cache breakpoints ─────────────────────────────


def test_system_blocks_have_four_cache_breakpoints():
    """Anthropic Messages API accepts up to 4 cache_control breakpoints
    per request. We use exactly four — one per layer. Adding a 5th
    would silently lose the last one; dropping below 4 wastes cache
    headroom."""
    from api.agent.prompts.system import build_system_blocks

    blocks = build_system_blocks("dock")
    assert len(blocks) == 4
    for b in blocks:
        assert b["type"] == "text"
        assert b.get("cache_control") == {"type": "ephemeral"}, (
            f"block missing cache_control: {b!r}"
        )


def test_system_blocks_order_is_canonical():
    """Render order matters for the cache prefix. The first three blocks
    are surface-independent (persona / tool-docs / invariants); the
    fourth varies by surface. Reordering ANY of these invalidates the
    cache for every existing conversation."""
    from api.agent.prompts.system import (
        build_system_blocks,
        PERSONA_AND_SAFETY,
        INVARIANTS,
    )
    from api.agent.prompts.surfaces import for_surface

    blocks = build_system_blocks("dock")
    assert blocks[0]["text"] == PERSONA_AND_SAFETY
    # Block 1 is the tool docs — built from the surface's tool subset.
    # We only assert the header line is present (the rest is
    # registry-derived and tested in test_agent_models.py).
    assert "TOOLS DISPONIBLES" in blocks[1]["text"]
    assert blocks[2]["text"] == INVARIANTS
    assert blocks[3]["text"] == for_surface("dock")


# ── 2. Wire-reported cache usage ────────────────────────────────


@pytest.mark.anyio
async def test_first_turn_reports_cache_creation_tokens(tmp_db):
    """The FIRST turn of a conversation populates the cache. Anthropic
    reports the bytes it wrote into cache via
    `usage.cache_creation_input_tokens`. The loop must propagate that
    field into MessageEnd.usage so audit + metrics can track cache
    health (spec §14 target is >= 70% cache hit rate by week 2)."""
    from api.agent.loop import run_turn, MessageEnd

    c = FakeAnthropicClient()
    c.queue_turn(FakeTurnBuilder()
                  .text("respuesta del turno 1")
                  .end_turn()
                  .usage(input_tokens=50, output_tokens=20,
                          cache_creation=1500, cache_read=0)
                  .build())

    msgs = [{"role": "user", "content": "primer turno"}]
    events = await _collect(run_turn(
        client=c, model="claude-sonnet-4-6", surface="dock",
        messages=msgs, tenant_id=1,
    ))
    ends = [e for e in events if isinstance(e, MessageEnd)]
    assert len(ends) == 1
    end = ends[0]
    assert end.usage["cache_creation_input_tokens"] == 1500
    assert end.usage["cache_read_input_tokens"] == 0


@pytest.mark.anyio
async def test_second_turn_reports_cache_read_tokens(tmp_db):
    """A SECOND turn on the same conversation should see most of the
    system prompt served from cache. The fake reports a high cache_read
    + low cache_creation; the loop propagates both into MessageEnd."""
    from api.agent.loop import run_turn, MessageEnd

    c = FakeAnthropicClient()
    c.queue_turn(FakeTurnBuilder()
                  .text("respuesta del turno 2")
                  .end_turn()
                  .usage(input_tokens=20, output_tokens=15,
                          cache_creation=0, cache_read=1450)
                  .build())

    # Simulating turn 2 — the previous user + assistant messages are
    # in the transcript the frontend re-sends every turn.
    msgs = [
        {"role": "user",      "content": "primer turno"},
        {"role": "assistant", "content": "respuesta del turno 1"},
        {"role": "user",      "content": "segundo turno"},
    ]
    events = await _collect(run_turn(
        client=c, model="claude-sonnet-4-6", surface="dock",
        messages=msgs, tenant_id=1,
    ))
    ends = [e for e in events if isinstance(e, MessageEnd)]
    assert ends[0].usage["cache_read_input_tokens"] == 1450
    assert ends[0].usage["cache_creation_input_tokens"] == 0


@pytest.mark.anyio
async def test_cache_tokens_sum_across_hops(tmp_db, monkeypatch):
    """Multi-hop turn: hop 1 hits cache (read=1400), hop 2 also hits
    cache (read=1450). MessageEnd.usage.cache_read_input_tokens must
    be the SUM (2850), not just the last hop's value. Same accumulation
    invariant as multi-hop cost (PR #408 review fix), now extended to
    every usage field."""
    from api.agent.loop import run_turn, MessageEnd
    from api.agent.tools import handlers as h

    def _stub_positions(*, tenant_id):
        return {"positions": []}
    monkeypatch.setitem(h.TOOL_HANDLERS, "get_positions", _stub_positions)

    c = FakeAnthropicClient()
    c.queue_turn(FakeTurnBuilder()
                  .tool_use("get_positions", {}, tool_use_id="toolu_1")
                  .stop_tool_use()
                  .usage(input_tokens=30, output_tokens=20,
                          cache_read=1400, cache_creation=0)
                  .build())
    c.queue_turn(FakeTurnBuilder()
                  .text("Listo, no tienes posiciones abiertas.")
                  .end_turn()
                  .usage(input_tokens=40, output_tokens=30,
                          cache_read=1450, cache_creation=0)
                  .build())

    msgs = [{"role": "user", "content": "qué tengo"}]
    events = await _collect(run_turn(
        client=c, model="claude-sonnet-4-6", surface="dock",
        messages=msgs, tenant_id=1,
    ))
    end = next(e for e in events if isinstance(e, MessageEnd))
    assert end.usage["cache_read_input_tokens"] == 2850  # 1400 + 1450


@pytest.mark.anyio
async def test_cache_aware_cost_is_cheaper_than_uncached(tmp_db):
    """A turn that uses cache_read should cost MUCH less than the
    equivalent turn billed as fresh input. We don't pin the exact
    discount (cached input is ~0.1× fresh in the published pricing
    table), but the assertion `cached_cost < uncached_cost * 0.5`
    locks the property loosely so a future cost-formula edit doesn't
    silently flatten the discount."""
    from api.agent.loop import _estimate_cost_usd

    uncached = _estimate_cost_usd(
        "claude-sonnet-4-6",
        {"input_tokens": 1500, "output_tokens": 100,
         "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
    )
    cached = _estimate_cost_usd(
        "claude-sonnet-4-6",
        {"input_tokens": 0, "output_tokens": 100,
         "cache_read_input_tokens": 1500, "cache_creation_input_tokens": 0},
    )
    assert cached < uncached * 0.5, (
        f"cached cost ${cached:.6f} should be < 50% of uncached "
        f"${uncached:.6f} — pricing table drifted?"
    )
