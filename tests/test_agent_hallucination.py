"""Phase 5B of epic #400 — §11.4 hallucination guard.

Locks the invariant: every position_id / tune_id / symbol the
assistant mentions in its FINAL text must come from a tool_result
emitted earlier in the same conversation. Without this guard, a model
that "remembers" or "infers" a position id silently quotes wrong data
and the user can't tell.

Strategy:
  - Direct unit tests on api/agent/safety.py — the regex extraction,
    the JSON traversal, the cross-walk match.
  - End-to-end test driving run_turn with a FakeAnthropicClient that
    emits grounded text → no hallucination.
  - End-to-end test driving run_turn with a fake that emits text
    referencing a position_id NOT in the tool_result → guard raises.
  - Sync invariant: api/agent/safety.py's curated-symbol set matches
    btc_scanner.DEFAULT_SYMBOLS at the time of the test run.
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


# ── Sync invariants ───────────────────────────────────────────────


def test_curated_symbols_match_scanner_default():
    """If btc_scanner.DEFAULT_SYMBOLS changes (epic #135-style symbol
    list update), the safety module's frozenset must follow. The two
    are independent today; this test pins the contract."""
    from api.agent.safety import _CURATED_SYMBOLS_PAIRS
    import btc_scanner

    assert _CURATED_SYMBOLS_PAIRS == frozenset(btc_scanner.DEFAULT_SYMBOLS)


# ── Reference extraction ──────────────────────────────────────────


def test_extract_position_ids_handles_common_phrasings():
    from api.agent.safety import _extract_position_ids
    assert _extract_position_ids("Tu posición #42 está en verde.") == {42}
    assert _extract_position_ids("la posicion 7 ya cerró")        == {7}
    assert _extract_position_ids("Posición 100 y posición 200")   == {100, 200}
    # No match on bare numbers without the keyword.
    assert _extract_position_ids("hay 5 trades hoy")              == set()


def test_extract_position_ids_avoids_substring_of_longer_number():
    """`posición 42` must not match `posición 4234` (which would mean
    "the position with id 4234", not 42)."""
    from api.agent.safety import _extract_position_ids
    assert _extract_position_ids("Posición 4234 está abierta") == {4234}
    # The 42 doesn't separately match because it's a prefix of 4234.


def test_extract_tune_ids_handles_common_phrasings():
    from api.agent.safety import _extract_tune_ids
    assert _extract_tune_ids("El tune #3 propone bajar el SL.") == {3}
    assert _extract_tune_ids("la propuesta de tune 5 está pendiente") == {5}


def test_extract_symbols_finds_both_base_and_pair_forms():
    from api.agent.safety import _extract_symbol_tokens
    assert _extract_symbol_tokens("Tu posición en BTC está en verde.") == {"BTC"}
    assert _extract_symbol_tokens("BTCUSDT está en zona de compra.")   == {"BTCUSDT"}
    # Case-insensitive input, canonical-uppercase output.
    assert "ETH" in _extract_symbol_tokens("estoy mirando eth y ada")
    assert "ADA" in _extract_symbol_tokens("estoy mirando eth y ada")
    # Word-boundary: substring 'BTC' inside 'OBTCO' does NOT match.
    assert _extract_symbol_tokens("la palabra OBTCO no es símbolo") == set()


# ── Grounding scan ───────────────────────────────────────────────


def test_grounding_scan_pulls_position_ids_from_tool_result_json():
    """The tool_result content is a JSON string. The grounding scan
    must surface IDs both from regex match against the literal string
    AND from explicit "id" / "position_id" keys via the JSON walker."""
    from api.agent.safety import collect_grounding_from_tool_results
    messages = [
        {"role": "user", "content": "qué tengo"},
        {"role": "assistant", "content": [{"type": "tool_use",
                                            "id": "toolu_1",
                                            "name": "get_positions",
                                            "input": {}}]},
        {"role": "user", "content": [{
            "type": "tool_result",
            "tool_use_id": "toolu_1",
            "content": '{"positions": [{"id": 42, "symbol": "BTCUSDT"}, '
                       '                {"id": 99, "symbol": "ETHUSDT"}]}',
        }]},
    ]
    g = collect_grounding_from_tool_results(messages)
    assert 42 in g["position_ids"]
    assert 99 in g["position_ids"]
    assert "BTC" in g["symbols"]
    assert "ETH" in g["symbols"]


def test_grounding_scan_pulls_tune_id_from_explicit_key():
    """Tools that surface a `tune_id` key (get_tune_proposal) — the
    walker should find it even if the assistant's text wouldn't have
    matched the regex (e.g. the tool returns id-only without prose)."""
    from api.agent.safety import collect_grounding_from_tool_results
    messages = [{"role": "user", "content": [{
        "type": "tool_result",
        "tool_use_id": "toolu_1",
        "content": '{"tune_id": 7, "applied": false}',
    }]}]
    g = collect_grounding_from_tool_results(messages)
    assert 7 in g["tune_ids"]


def test_grounding_scan_handles_anthropic_tool_result_block_list_form():
    """Anthropic's tool_result content can also be a LIST of text
    blocks instead of a single string. The scanner must handle both."""
    from api.agent.safety import collect_grounding_from_tool_results
    messages = [{"role": "user", "content": [{
        "type": "tool_result",
        "tool_use_id": "toolu_1",
        "content": [
            {"type": "text", "text": '{"positions": [{"id": 5}]}'}
        ],
    }]}]
    g = collect_grounding_from_tool_results(messages)
    assert 5 in g["position_ids"]


# ── Public API: assert_text_grounded ─────────────────────────────


def test_assert_text_grounded_passes_when_references_match():
    """Happy path: text says #42/BTC, grounding has both → returns
    silently, does not raise."""
    from api.agent.safety import assert_text_grounded
    messages = [{"role": "user", "content": [{
        "type": "tool_result",
        "tool_use_id": "toolu_1",
        "content": '{"positions": [{"id": 42, "symbol": "BTCUSDT"}]}',
    }]}]
    # Must not raise.
    assert_text_grounded(
        text="Tu posición #42 en BTC sigue abierta.",
        messages=messages,
    )


def test_assert_text_grounded_raises_on_invented_position_id():
    """Text says #999, no tool_result mentioned 999 → raises."""
    from api.agent.safety import assert_text_grounded, HallucinationDetected
    messages = [{"role": "user", "content": [{
        "type": "tool_result",
        "tool_use_id": "toolu_1",
        "content": '{"positions": [{"id": 42, "symbol": "BTCUSDT"}]}',
    }]}]
    with pytest.raises(HallucinationDetected) as exc:
        assert_text_grounded(
            text="Tu posición #999 en BTC sigue abierta.",
            messages=messages,
        )
    # The error names the ungrounded ID so the operator/test can debug.
    assert "999" in str(exc.value)


def test_assert_text_grounded_raises_on_invented_symbol():
    """Text says SOL (not in curated set anyway; but pretend it WAS in
    the curated set) — actually let's flip: the assistant mentions
    XLM but the tool_result never surfaced XLM. Curated symbol BUT
    ungrounded → raises."""
    from api.agent.safety import assert_text_grounded, HallucinationDetected
    messages = [{"role": "user", "content": [{
        "type": "tool_result",
        "tool_use_id": "toolu_1",
        "content": '{"positions": [{"id": 42, "symbol": "BTCUSDT"}]}',
    }]}]
    with pytest.raises(HallucinationDetected) as exc:
        assert_text_grounded(
            text="Te sugiero abrir XLM aquí.",
            messages=messages,
        )
    assert "XLM" in str(exc.value)


def test_assert_text_grounded_treats_btc_and_btcusdt_as_same_symbol():
    """Symbol normalization: assistant says 'BTC', tool returned
    'BTCUSDT'. The guard must NOT flag this — same symbol, two
    natural representations."""
    from api.agent.safety import assert_text_grounded
    messages = [{"role": "user", "content": [{
        "type": "tool_result",
        "tool_use_id": "toolu_1",
        "content": '{"positions": [{"id": 1, "symbol": "BTCUSDT"}]}',
    }]}]
    # Must not raise.
    assert_text_grounded(
        text="Tu posición #1 en BTC sigue abierta.",
        messages=messages,
    )


def test_assert_text_grounded_ignores_unrecognized_tokens():
    """The guard scope is intentionally narrow — IDs + curated symbols.
    Random words (numbers without 'posición', random uppercase strings)
    are NOT flagged. False positives erode operator trust faster than
    missed hallucinations of obscure references."""
    from api.agent.safety import assert_text_grounded
    messages = [{"role": "user", "content": [{
        "type": "tool_result",
        "tool_use_id": "toolu_1",
        "content": '{"positions": []}',
    }]}]
    # No 'posición #N', no curated symbol. Must not raise.
    assert_text_grounded(
        text="Hoy el mercado se ve dudoso, RSI alto en 70.",
        messages=messages,
    )


# ── End-to-end through run_turn ──────────────────────────────────


@pytest.mark.anyio
async def test_e2e_grounded_response_passes_guard(tmp_db, monkeypatch):
    """A model that calls get_positions, gets back position #42 BTCUSDT,
    and refers to "#42 en BTC" in its final text → guard passes."""
    from api.agent.loop import run_turn
    from api.agent.safety import assert_text_grounded
    from api.agent.tools import handlers as h

    def _stub_positions(*, tenant_id):
        return {"positions": [{"id": 42, "symbol": "BTCUSDT"}]}
    monkeypatch.setitem(h.TOOL_HANDLERS, "get_positions", _stub_positions)

    c = FakeAnthropicClient()
    c.queue_turn(FakeTurnBuilder()
                  .tool_use("get_positions", {}, tool_use_id="toolu_1")
                  .stop_tool_use().build())
    c.queue_turn(FakeTurnBuilder()
                  .text("Tu posición #42 en BTC sigue abierta.")
                  .end_turn().build())

    msgs = [{"role": "user", "content": "qué posiciones tengo"}]
    await _collect(run_turn(client=c, model="claude-sonnet-4-6",
                             surface="dock", messages=msgs, tenant_id=1))

    # The final assistant message accumulated the streamed text. Find
    # it by walking msgs in reverse.
    final_text = ""
    for m in reversed(msgs):
        if m.get("role") != "assistant":
            continue
        c2 = m.get("content")
        if isinstance(c2, str):
            final_text = c2
            break
        if isinstance(c2, list):
            for b in c2:
                if isinstance(b, dict) and b.get("type") == "text":
                    final_text += b.get("text", "")
            if final_text:
                break

    # Must not raise.
    assert_text_grounded(text=final_text, messages=msgs)


@pytest.mark.anyio
async def test_e2e_hallucinated_response_fails_guard(tmp_db, monkeypatch):
    """Same flow, but the model invents a position id (#777) that
    never appeared in any tool_result. Guard raises."""
    from api.agent.loop import run_turn
    from api.agent.safety import assert_text_grounded, HallucinationDetected
    from api.agent.tools import handlers as h

    def _stub_positions(*, tenant_id):
        return {"positions": [{"id": 42, "symbol": "BTCUSDT"}]}
    monkeypatch.setitem(h.TOOL_HANDLERS, "get_positions", _stub_positions)

    c = FakeAnthropicClient()
    c.queue_turn(FakeTurnBuilder()
                  .tool_use("get_positions", {}, tool_use_id="toolu_1")
                  .stop_tool_use().build())
    c.queue_turn(FakeTurnBuilder()
                  .text("Cierra tu posición #777 ya mismo.")
                  .end_turn().build())

    msgs = [{"role": "user", "content": "qué posiciones tengo"}]
    await _collect(run_turn(client=c, model="claude-sonnet-4-6",
                             surface="dock", messages=msgs, tenant_id=1))

    final_text = "Cierra tu posición #777 ya mismo."
    with pytest.raises(HallucinationDetected):
        assert_text_grounded(text=final_text, messages=msgs)
