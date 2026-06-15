"""Tests for Capa 3 del verdict_guard: juez de doctrina LLM."""
import asyncio
from api.agent.providers.base import LLMTextDelta, LLMStreamEnd
from api.agent.judge import judge_doctrine


class _FakeProvider:
    name = "fake"

    def __init__(self, verdict_word):
        self._w = verdict_word

    def format_system_blocks(self, blocks):
        return blocks

    def estimate_cost(self, model, usage):
        return 0.0

    async def stream(self, *, model, system_blocks, messages, tools, max_tokens):
        yield LLMTextDelta(text=self._w)
        yield LLMStreamEnd(stop_reason="end_turn", usage={"output_tokens": 3}, content=[])


def _run(coro):
    return asyncio.run(coro)


def test_judge_flags_verdict():
    is_v, usage = _run(judge_doctrine(_FakeProvider("VEREDICTO"),
                                      candidate_text="se mueve poco, equipo sólido"))
    assert is_v is True


def test_judge_passes_facts():
    is_v, usage = _run(judge_doctrine(_FakeProvider("HECHOS"),
                                      candidate_text="el precio giró 3 veces en el piso"))
    assert is_v is False


def test_judge_empty_text_passes_without_call():
    is_v, usage = _run(judge_doctrine(_FakeProvider("VEREDICTO"), candidate_text="   "))
    assert is_v is False
    assert usage == {}


def test_judge_unparseable_fails_closed():
    is_v, usage = _run(judge_doctrine(_FakeProvider("???"),
                                      candidate_text="texto ambiguo"))
    assert is_v is True


class _RaisingProvider:
    """El juez revienta a mitad del stream (upstream caído)."""
    name = "fake"

    def format_system_blocks(self, blocks):
        return blocks

    def estimate_cost(self, model, usage):
        return 0.0

    async def stream(self, *, model, system_blocks, messages, tools, max_tokens):
        raise RuntimeError("upstream down")
        yield  # unreachable; hace de esto un async generator


def test_judge_exception_fails_closed():
    # Si el juez falla, NO se deja pasar el texto sin juzgar: fail closed → rechaza.
    is_v, usage = _run(judge_doctrine(_RaisingProvider(), candidate_text="texto cualquiera"))
    assert is_v is True
