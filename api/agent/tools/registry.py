"""Tool catalog + per-surface subsets.

Phase 1 deliverable. The conversation core (Phase 2) reads this module
to build the `tools=[...]` array on every Messages API request — per
surface, which keeps the model focused on the relevant tools and keeps
the prompt cache hot (changing tool sets mid-session invalidates the
prefix; per pre-reg §4.3 we never mix models within a conversation, and
the per-surface tool subset is stable across a session because the
surface is fixed at session-start).

Each tool entry carries:
  - name         — wire name used by the model when emitting tool_use
  - description  — one-sentence guidance shown to the model in the
                   system-prompt "tool docs" block (§7.2)
  - schema       — Pydantic input model (from schemas.py)
  - surfaces     — set of surface names this tool is exposed on
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Type

from pydantic import BaseModel

from api.agent.tools.schemas import (
    TOOL_INPUT_SCHEMAS,
    GetClosedTradesIn,
    GetKillSwitchStateIn,
    GetPortfolioOverviewIn,
    GetPositionDetailIn,
    GetPositionsIn,
    GetRecentSignalsIn,
    GetSymbolSetupIn,
    GetSymbolsWithSignalsIn,
    GetTuneProposalIn,
    ProposeApplyTuneIn,
    ProposeClosePositionIn,
    ProposeReactivateSymbolIn,
)


# Surface identifiers used by Phase 2's POST /agent/conversations/{id}/turn
# body. Adding a new surface here means also adding a micro-prompt in
# api/agent/prompts/surfaces.py (Phase 4) and a row to the matrix below.
Surface = str  # 'dock' | 'symbol_detail' | 'kill_switch' | 'autotune' | 'historial'

ALL_SURFACES: frozenset[Surface] = frozenset({
    "dock", "symbol_detail", "kill_switch", "autotune", "historial",
})


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    schema: Type[BaseModel]
    surfaces: frozenset[Surface]


# Catalog order matches pre-reg §5.1. Keep stable — the model sees these
# in this order in the cached tool-docs block; reordering invalidates the
# cache prefix.
TOOL_CATALOG: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="get_portfolio_overview",
        description=(
            "Aggregate snapshot of the user's portfolio: open position count, "
            "total notional, current/peak equity, drawdown, portfolio tier. "
            "Use this when the user asks a 'how am I doing overall' question."
        ),
        schema=GetPortfolioOverviewIn,
        surfaces=frozenset({"dock", "kill_switch"}),
    ),
    ToolSpec(
        name="get_positions",
        description=(
            "List the user's currently open positions with entry/SL/TP/size. "
            "Use this when the user references 'mis posiciones' or asks for a list."
        ),
        schema=GetPositionsIn,
        surfaces=frozenset({"dock", "symbol_detail"}),
    ),
    ToolSpec(
        name="get_position_detail",
        description=(
            "Look up one position by its numeric id. Returns 'not_found' if the "
            "id does not belong to the calling user — never reveals existence "
            "across users. Use this when the user references a specific position id."
        ),
        schema=GetPositionDetailIn,
        surfaces=frozenset({"dock", "symbol_detail"}),
    ),
    ToolSpec(
        name="get_symbols_with_signals",
        description=(
            "List the curated 10 symbols (BTC, ETH, ADA, AVAX, DOGE, UNI, XLM, "
            "PENDLE, JUP, RUNE) with their current scanner score and signal flag. "
            "Use this when the user asks 'where is the best opportunity right now'."
        ),
        schema=GetSymbolsWithSignalsIn,
        surfaces=frozenset({"dock"}),
    ),
    ToolSpec(
        name="get_symbol_setup",
        description=(
            "Read the latest scanner setup for one symbol: LRC%, RSI, score, "
            "direction, estado. Use this when the user asks about a specific "
            "symbol's current state."
        ),
        schema=GetSymbolSetupIn,
        surfaces=frozenset({"dock", "symbol_detail"}),
    ),
    ToolSpec(
        name="get_kill_switch_state",
        description=(
            "Portfolio tier plus per-symbol kill-switch state "
            "(NORMAL/ALERT/REDUCED/PAUSED/PROBATION). Use this when the user asks "
            "about pausing, recovery, or the health of the system."
        ),
        schema=GetKillSwitchStateIn,
        surfaces=frozenset({"dock", "kill_switch"}),
    ),
    ToolSpec(
        name="get_recent_signals",
        description=(
            "List the most recent emitted signals (windowed). Use this when the "
            "user asks what signals fired today, yesterday, etc."
        ),
        schema=GetRecentSignalsIn,
        surfaces=frozenset({"dock", "symbol_detail"}),
    ),
    ToolSpec(
        name="get_closed_trades",
        description=(
            "List closed trades for the user, windowed (7d/30d/90d/all). Use this "
            "for performance review, win-rate questions, and historial analysis."
        ),
        schema=GetClosedTradesIn,
        surfaces=frozenset({"dock", "historial", "autotune"}),
    ),
    ToolSpec(
        name="get_tune_proposal",
        description=(
            "Return the latest pending auto-tune proposal, or null. Use this when "
            "the user is on the AutoTune view or asks about parameter changes."
        ),
        schema=GetTuneProposalIn,
        surfaces=frozenset({"dock", "autotune"}),
    ),
    # ── Propose tools (Phase 3). These NEVER execute the action — the
    # tool signs a proposal envelope; the UI shows an amber confirm
    # button; only on user confirmation does the downstream handler run.
    # The model receives a user-facing summary, never the signed token.
    ToolSpec(
        name="propose_close_position",
        description=(
            "Propose closing one of the user's open positions. Server signs a "
            "5-min-TTL proposal; the UI renders an amber confirm button. NEVER "
            "do this unless the user explicitly asked to close the position AND "
            "has articulated a rationale. The downstream close fires only on "
            "user confirmation."
        ),
        schema=ProposeClosePositionIn,
        surfaces=frozenset({"dock"}),
    ),
    ToolSpec(
        name="propose_reactivate_symbol",
        description=(
            "Propose moving a PAUSED symbol back to PROBATION. Use this when "
            "the user has articulated a concrete reason for the override (a "
            "regime change, a specific catalyst, etc), NOT for vague feelings. "
            "Server signs + persists the proposal; the UI confirms."
        ),
        schema=ProposeReactivateSymbolIn,
        surfaces=frozenset({"dock", "kill_switch"}),
    ),
    ToolSpec(
        name="propose_apply_tune",
        description=(
            "Propose applying a pending auto-tune to the live config. The "
            "tune_id must come from a prior get_tune_proposal call in the "
            "same conversation. Use this when the user has reasoned through "
            "the risks per-symbol."
        ),
        schema=ProposeApplyTuneIn,
        surfaces=frozenset({"dock", "autotune"}),
    ),
)


# Sanity invariant: every entry in TOOL_CATALOG has a corresponding
# schema in TOOL_INPUT_SCHEMAS, and vice versa. If you add a tool to one
# and forget the other, this raises at import time so the process refuses
# to start in production with an inconsistent registry. The test in
# tests/test_agent_tools.py covers the same invariant in CI.
#
# `raise RuntimeError`, not `assert`: `python -O` strips assert
# statements out of the bytecode entirely (PR #403 review issue 4).
_CATALOG_NAMES = {t.name for t in TOOL_CATALOG}
_SCHEMA_NAMES = set(TOOL_INPUT_SCHEMAS.keys())
if _CATALOG_NAMES != _SCHEMA_NAMES:
    raise RuntimeError(
        f"Tool catalog/schema mismatch — catalog: {_CATALOG_NAMES}, "
        f"schemas: {_SCHEMA_NAMES}"
    )


def tools_for_surface(surface: Surface) -> tuple[ToolSpec, ...]:
    """Subset of the catalog exposed on `surface`. Stable ordering — the
    return preserves the catalog order so the prompt cache stays warm
    across calls for the same surface."""
    return tuple(t for t in TOOL_CATALOG if surface in t.surfaces)
