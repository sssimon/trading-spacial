"""Pydantic input schemas for every agent tool.

These get serialized to JSON Schema and shipped in the `tools` array
of every messages.create() call (Phase 2). Keep them small and well-
described — the model uses the field descriptions to decide when to
invoke the tool and what to pass.

Pre-reg §5: tenant_id is NEVER an input field on any tool. It is
resolved server-side from the JWT and bound into the handler before
dispatch. Adding `tenant_id` to any schema below is a regression.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class GetPortfolioOverviewIn(BaseModel):
    """No input — returns the user's portfolio snapshot."""
    pass


class GetPositionsIn(BaseModel):
    """List currently open positions for the user."""
    pass


class GetPositionDetailIn(BaseModel):
    """Look up one position by id. Returns 'not_found' if it doesn't
    belong to the calling tenant — never reveals existence cross-tenant."""
    position_id: int = Field(..., ge=1, description="Numeric position id from positions.id")


class GetSymbolsWithSignalsIn(BaseModel):
    """List curated symbols ordered by current signal score."""
    limit: int = Field(default=10, ge=1, le=10,
                       description="Max symbols to return (capped at 10, the curated set size)")


class GetSymbolSetupIn(BaseModel):
    """Read the latest scanner setup for one symbol."""
    symbol: str = Field(..., min_length=2, max_length=20,
                        description="Symbol ticker, e.g. 'BTCUSDT' or 'BTC'")


class GetKillSwitchStateIn(BaseModel):
    """No input — returns portfolio + per-symbol kill-switch state."""
    pass


class GetRecentSignalsIn(BaseModel):
    """List the most recent signals the scanner emitted."""
    limit: int = Field(default=10, ge=1, le=50)
    since_hours: int = Field(default=24, ge=1, le=24 * 14,
                             description="Hours of history to include (max 14 days)")


class GetClosedTradesIn(BaseModel):
    """List closed trades for the user, windowed."""
    window: Literal["7d", "30d", "90d", "all"] = Field(default="30d")


class GetTuneProposalIn(BaseModel):
    """No input — returns the latest pending auto-tune proposal, or null."""
    pass


# Convenience map for the registry. Order here is the canonical tool
# ordering used in the system prompt's tool documentation block.
TOOL_INPUT_SCHEMAS: dict[str, type[BaseModel]] = {
    "get_portfolio_overview":   GetPortfolioOverviewIn,
    "get_positions":            GetPositionsIn,
    "get_position_detail":      GetPositionDetailIn,
    "get_symbols_with_signals": GetSymbolsWithSignalsIn,
    "get_symbol_setup":         GetSymbolSetupIn,
    "get_kill_switch_state":    GetKillSwitchStateIn,
    "get_recent_signals":       GetRecentSignalsIn,
    "get_closed_trades":        GetClosedTradesIn,
    "get_tune_proposal":        GetTuneProposalIn,
}
