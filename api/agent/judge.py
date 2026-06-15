"""Capa 3 del verdict_guard de Valles: juez de doctrina LLM.

El denylist (Capa 2) caza veredictos EXPLÍCITOS. El veredicto COMPOSITIVO
—enumerar hechos que en conjunto equivalen a 'buena/mala'— es semántico y
ningún regex lo atrapa. Este juez lee la respuesta candidata (NO la
conversación: juzga el texto, no la intención) y decide si compone un
juicio. Fail closed: si el juez no responde claro, se rechaza. Ver
valles spec §6.3 (D7)."""
from __future__ import annotations

import logging

from api.agent.providers.base import LLMStreamEnd, LLMTextDelta

log = logging.getLogger("api.agent.judge")

JUDGE_MODEL = "deepseek-chat"

_JUDGE_SYSTEM = (
    "Eres un juez de doctrina. Te doy una RESPUESTA de un copiloto que solo "
    "puede exhibir hechos sobre una moneda, nunca un juicio. Decide si la "
    "respuesta recomienda, rankea, predice, dice cuánto invertir, o concluye "
    "un juicio sobre comprar/vender/elegir — incluso de forma IMPLÍCITA "
    "(enumerar hechos que en conjunto dicen 'buena' o 'mala' es un juicio). "
    "Responde EXACTAMENTE una palabra, sin nada más: "
    "VEREDICTO si compone un juicio, HECHOS si solo describe hechos."
)


def _parse(raw: str) -> bool:
    """True = es veredicto (rechazar). Fail closed: ambiguo → rechazar."""
    up = (raw or "").strip().upper()
    if "VEREDICTO" in up:
        return True
    if "HECHOS" in up:
        return False
    log.warning("judge_doctrine: veredicto ambiguo %r -> fail closed", raw[:80])
    return True


async def judge_doctrine(provider, *, candidate_text: str,
                         model: str = JUDGE_MODEL) -> tuple[bool, dict]:
    """Devuelve (es_veredicto, usage). Texto vacío → (False, {}) sin llamar."""
    if not candidate_text or not candidate_text.strip():
        return False, {}
    system_blocks = provider.format_system_blocks([_JUDGE_SYSTEM])
    messages = [{"role": "user", "content": f"RESPUESTA:\n{candidate_text}"}]
    parts: list[str] = []
    usage: dict = {}
    try:
        async for ev in provider.stream(model=model, system_blocks=system_blocks,
                                        messages=messages, tools=[], max_tokens=16):
            if isinstance(ev, LLMTextDelta):
                parts.append(ev.text)
            elif isinstance(ev, LLMStreamEnd):
                usage = ev.usage
    except Exception as e:  # noqa: BLE001
        log.warning("judge_doctrine: fallo del juez %s -> fail closed", e)
        return True, usage
    return _parse("".join(parts)), usage
