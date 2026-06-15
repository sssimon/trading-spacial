import asyncio

import pytest

from api.agent import loop as loop_mod
from api.agent.loop import run_turn, TextDelta, Refusal, MessageEnd, ToolUseResult
from api.agent.providers.base import (
    LLMTextDelta,
    LLMToolUseStart,
    LLMStreamEnd,
    SyntheticTextBlock,
    SyntheticToolUseBlock,
)


def _fake_judge(*, is_verdict):
    async def _j(provider, *, candidate_text, model=loop_mod.JUDGE_MODEL):
        return is_verdict, {"output_tokens": 2}
    return _j


class _ScriptedProvider:
    """Provider de juguete: __init__ toma una lista de hops. Cada hop es
    (text:str, stop_reason:str, tool_use:bool). stream() emite, por hop,
    un LLMTextDelta(text), un LLMToolUseStart (si tool_use), y un
    LLMStreamEnd cuyo .content lleva un SyntheticTextBlock(text) y (si
    tool_use) un SyntheticToolUseBlock. Usa las clases REALES de base.py
    para que final_content tenga el .type/.text/.name correcto."""

    name = "fake"

    def __init__(self, hops):
        self._hops = list(hops)
        self._i = 0

    async def stream(self, *, model, system_blocks, messages, tools, max_tokens):
        text, stop_reason, tool_use = self._hops[self._i]
        self._i += 1
        if text:
            yield LLMTextDelta(text=text)
        content = []
        if text:
            content.append(SyntheticTextBlock(text=text))
        if tool_use:
            yield LLMToolUseStart(id="toolu_1", name="get_levels")
            content.append(SyntheticToolUseBlock(
                id="toolu_1", name="get_levels", input={}))
        yield LLMStreamEnd(
            stop_reason=stop_reason,
            usage={"input_tokens": 1, "output_tokens": 1},
            content=content,
        )

    def format_system_blocks(self, blocks):
        return blocks

    def estimate_cost(self, model, usage):
        return 0.0

    def to_assistant_message(self, stream_end):
        return {"role": "assistant", "content": "scripted"}

    def to_tool_result_messages(self, tool_uses_with_results):
        return [
            {"role": "tool", "tool_call_id": "toolu_1", "content": content}
            for (_tu, content, _err) in tool_uses_with_results
        ]

    def blocks_to_api_shape(self, content):
        return content


async def _collect(gen):
    out = []
    async for ev in gen:
        out.append(ev)
    return out


def _run_turn(provider, surface, monkeypatch, **kw):
    # GOTCHA 1: _cached_formatted_tools golpea el registry de providers;
    # un nombre falso explota. Lo cortocircuitamos.
    monkeypatch.setattr(loop_mod, "_cached_formatted_tools",
                        lambda surface, name: ())
    return asyncio.run(_collect(run_turn(
        client=provider,
        model="fake-model",
        surface=surface,
        messages=[{"role": "user", "content": "hola"}],
        tenant_id=1,
        **kw,
    )))


def test_valles_buffers_no_textdelta_until_end(monkeypatch):
    monkeypatch.setattr(loop_mod, "judge_doctrine", _fake_judge(is_verdict=False))
    provider = _ScriptedProvider([("BTC está en valle, 4% de rango.", "end_turn", False)])
    events = _run_turn(provider, "valles", monkeypatch)

    deltas = [e for e in events if isinstance(e, TextDelta)]
    assert len(deltas) == 1
    assert deltas[0].text == "BTC está en valle, 4% de rango."
    assert isinstance(events[-1], MessageEnd)


def test_valles_explicit_verdict_refused(monkeypatch):
    # judge no debe ni llamarse (Capa 2 caza primero), pero si lo hace,
    # que no contradiga.
    monkeypatch.setattr(loop_mod, "judge_doctrine", _fake_judge(is_verdict=False))
    provider = _ScriptedProvider([("Deberías comprar BTC ahora.", "end_turn", False)])
    events = _run_turn(provider, "valles", monkeypatch)

    assert any(isinstance(e, Refusal) for e in events)
    assert not any(isinstance(e, TextDelta) for e in events)
    assert isinstance(events[-1], MessageEnd)  # costo NO se descarta


def test_valles_compositional_verdict_refused_by_judge(monkeypatch):
    # texto limpio (sin frase del denylist), pero el juez dice veredicto.
    monkeypatch.setattr(loop_mod, "judge_doctrine", _fake_judge(is_verdict=True))
    provider = _ScriptedProvider([
        ("BTC: tendencia alcista, volumen creciente, soporte firme.", "end_turn", False)
    ])
    events = _run_turn(provider, "valles", monkeypatch)

    assert any(isinstance(e, Refusal) for e in events)
    assert not any(isinstance(e, TextDelta) for e in events)
    assert isinstance(events[-1], MessageEnd)


def test_dock_still_streams_incrementally(monkeypatch):
    provider = _ScriptedProvider([("hola mundo", "end_turn", False)])
    events = _run_turn(provider, "dock", monkeypatch)

    deltas = [e for e in events if isinstance(e, TextDelta)]
    assert len(deltas) >= 1
    assert any(d.text == "hola mundo" for d in deltas)
    assert isinstance(events[-1], MessageEnd)


def test_valles_multihop_intermediate_text_buffered(monkeypatch):
    monkeypatch.setattr(loop_mod, "judge_doctrine", _fake_judge(is_verdict=False))
    # GOTCHA 2: el dispatch de tools es red/DB — lo cortocircuitamos.
    monkeypatch.setattr(loop_mod, "dispatch_tool",
                        lambda name, args, **kw: '{"ok": true}')
    provider = _ScriptedProvider([
        ("déjame ver los niveles", "tool_use", True),
        ("está en valle, 4% de rango", "end_turn", False),
    ])
    events = _run_turn(provider, "valles", monkeypatch)

    # Ningún TextDelta antes del resultado de la tool / durante hop1.
    idx_tool_result = next(
        (i for i, e in enumerate(events) if isinstance(e, ToolUseResult)), None
    )
    assert idx_tool_result is not None
    assert not any(
        isinstance(e, TextDelta) for e in events[:idx_tool_result]
    ), "no debe emitirse texto durante hop1 (intermedio)"

    deltas = [e for e in events if isinstance(e, TextDelta)]
    assert len(deltas) == 1
    # el único TextDelta final concatena AMBOS textos de hop (todos los hops).
    assert deltas[0].text == "déjame ver los nivelesestá en valle, 4% de rango"
    assert isinstance(events[-1], MessageEnd)


def test_valles_empty_terminal_text_no_judge_no_crash(monkeypatch):
    # final_content sin bloque de texto → full_text="" → el juez NO se llama
    # (guarda full_text.strip()) y el guard no revienta; MessageEnd limpio.
    # (Brecha de cobertura señalada por Halberg al revisar el commit del loop.)
    def _boom(*a, **k):
        raise AssertionError("el juez no debe llamarse con texto vacío")
    monkeypatch.setattr(loop_mod, "judge_doctrine", _boom)
    provider = _ScriptedProvider([("", "end_turn", False)])
    events = _run_turn(provider, "valles", monkeypatch)

    assert not any(isinstance(e, TextDelta) for e in events)
    assert not any(isinstance(e, Refusal) for e in events)
    assert isinstance(events[-1], MessageEnd)


def test_refusal_event_serializes():
    from api.agent.loop import Refusal
    from api.agent.streaming import sse_serialize

    async def gen():
        yield Refusal(user_message="no decido, te leo hechos")

    async def collect():
        out = []
        async for frame in sse_serialize(gen(), keepalive_seconds=999):
            out.append(frame.decode())
        return out

    frames = asyncio.run(collect())
    assert any('"type": "refusal"' in f and "no decido" in f for f in frames)
