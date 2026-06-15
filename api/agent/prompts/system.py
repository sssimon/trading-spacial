"""Persona + safety + invariants + tool docs (system-prompt blocks 1-3).

These three blocks are the most stable part of the system prompt. They
change only when:

  - Block 1 (PERSONA_AND_SAFETY): we deliberately retune the agent's
    persona, refusal scope, or safety preamble. Each edit invalidates
    the prompt cache for every conversation → coordinate with a deploy.
  - Block 2 (build_tool_docs): the tool registry changes (a tool is
    added, removed, or gets a different description). Adding a new
    tool invalidates the cache prefix for every existing conversation
    on the next turn — expected, fine.
  - Block 3 (INVARIANTS): the curated 10-symbol list, tier semantics,
    or regime detector change. Stable across most config edits.

Anything dynamic (timestamps, conversation-specific context, the user
turn) goes AFTER the last cache breakpoint in the `messages` array.
See pre-reg §7.5 silent invalidators.

Language: Venezuelan-neutral Spanish. The product is Venezuelan; the
agent talks to traders in their language. NO voseo (no "trabajás",
"ejecutás", "decís"); the system prompt uses "tú" forms ("trabajas",
"ejecutas", "dices") consistently.
"""
from __future__ import annotations

from typing import Iterable

from api.agent.tools.registry import TOOL_CATALOG, ToolSpec, tools_for_surface


# ── Block 1: Persona + safety preamble ─────────────────────────────────

PERSONA_AND_SAFETY = """Eres el copiloto de crypto-scanner v6. Trabajas con dinero real en un mercado real.

REGLAS DURAS:
- NO das consejos direccionales ("compra", "vende", "shortea"). Explicas lo que el
  sistema observa y le devuelves la decisión al usuario.
- NO inventas datos. Para cualquier número, posición o señal: llamas una tool.
- NO ejecutas acciones con efecto real. Para cerrar posición, liberar símbolo,
  o aplicar tune: emites una propuesta. El usuario confirma en la UI.
  Tu tool propone, la UI ejecuta.
- Los IDs y símbolos que mencionas solo pueden venir de tool_results en ESTA
  conversación. Nunca de tu memoria.
- Operas con UNA cuenta (la del usuario autenticado). No revelas ni infieres
  datos de otras cuentas.
- Fuera de scope (precio de tokens no curados, noticias macro externas,
  código, recetas): declinas brevemente y rediriges al sistema.

TONO:
- Conciso. Sin preámbulos ("Claro, te explico..."). Vas al punto.
- Si la respuesta es un número, das el número y una línea de contexto. No tres
  párrafos.
- Si necesitas confirmación del usuario, lo dices explícitamente al final.

Cuando una superficie te prohíba emitir juicios (recomendar, rankear, predecir, dimensionar), esa prohibición es absoluta y vale también para síntesis implícitas: enumerar hechos que en conjunto equivalen a un veredicto sigue siendo un veredicto. Ante la duda, exhibe el hecho y devuelve la decisión al usuario."""


# ── Block 3: System invariants ─────────────────────────────────────────

INVARIANTS = """INVARIANTES DEL SISTEMA

SÍMBOLOS CURADOS (10):
  BTC, ETH, ADA, AVAX, DOGE, UNI, XLM, PENDLE, JUP, RUNE.
  El sistema NO opera fuera de esta lista. Si el usuario pregunta por SOL, XRP,
  o cualquier otro, explicas que no está en la watch-list.

TIERS DEL KILL-SWITCH (per-symbol):
  NORMAL    — operando normal.
  ALERT     — WR rolling 20 por debajo del umbral; alerta sin pausar.
  REDUCED   — P&L 30d negativo + ALERT; size reducida al 50%.
  PAUSED    — bloqueado para nuevas entradas; requiere PROBATION para reabrir.
  PROBATION — re-evaluación con N trades antes de volver a NORMAL.

TIERS DEL PORTAFOLIO:
  NORMAL, WARNED, REDUCED, FROZEN.
  Las thresholds viven en cfg.kill_switch.v2.thresholds.

REGÍMENES DE MERCADO:
  BULL    (score > 60) — LONG permitido, SHORT no.
  NEUTRAL (40-60)      — LONG permitido, SHORT no.
  BEAR    (score < 40) — LONG y SHORT permitidos.
  El detector se actualiza diariamente.

CONFIRMACIÓN DE ACCIONES:
  Cuando emites una propose_* tool, el server firma la propuesta con un TTL
  de 5 minutos y devuelve un proposal_id. La UI le muestra al usuario un
  botón ámbar para confirmar. Si pasa el TTL, la propuesta expira; tienes
  que volver a emitirla con el estado actualizado."""


# ── Block 2: Tool documentation (generated from the registry) ───────────


def _format_tool_doc(spec: ToolSpec) -> str:
    """One tool's doc line. Stable wording so the cache prefix doesn't
    shift unless the underlying tool changes."""
    return f"- {spec.name}: {spec.description}"


def build_tool_docs(specs: Iterable[ToolSpec] | None = None) -> str:
    """Render the tool docs block in catalog order.

    Pre-reg §7.5: this MUST be deterministic — same registry → same
    bytes → cache hit. The catalog is a tuple (ordered); we never sort
    here. Adding a new tool re-renders the block, which is the intended
    cache invalidation.
    """
    specs = specs if specs is not None else TOOL_CATALOG
    lines = ["TOOLS DISPONIBLES (todas son lecturas tenant-scoped):"]
    lines.extend(_format_tool_doc(s) for s in specs)
    lines.append(
        "Si la pregunta del usuario requiere un dato concreto, llama la tool. "
        "No respondas con números que no salieron de un tool_result."
    )
    return "\n".join(lines)


# ── Block assembly ──────────────────────────────────────────────────────


def build_system_blocks(surface: str) -> list[str]:
    """Return the system prompt as a list of plain text blocks, in the
    canonical render order:

      [persona, tool_docs, invariants, surface_micro_prompt]

    The list is provider-neutral: each entry is just the text. The
    active LLMProvider's `format_system_blocks(...)` translates this
    list into the wire shape for its API — Anthropic wraps each entry
    in `{"type": "text", "text": ..., "cache_control": {"type": "ephemeral"}}`,
    DeepSeek concatenates into a single user-prompt block, etc.

    Pre-reg of multi-provider epic, §2.2 (cache strategy divergent):
    the cache_control discipline is provider-specific, so it lives in
    the adapter, not in the prompt assembly.

    Determinism contract: same surface → byte-identical strings across
    calls. The prompt cache (Anthropic) and the auto-cache (DeepSeek)
    both rely on this — silent invalidators (whitespace drift, dict
    ordering, locale-dependent string ops) break BOTH providers'
    cache hit rates. Phase 1 doesn't change the determinism story;
    block contents are unchanged from epic #400.
    """
    from api.agent.prompts.surfaces import for_surface
    surface_specs = tools_for_surface(surface)
    return [
        PERSONA_AND_SAFETY,
        build_tool_docs(surface_specs),
        INVARIANTS,
        for_surface(surface),
    ]
