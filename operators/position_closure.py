"""PositionClosure — business operator for closing a position.

Implements the contract specified in
docs/superpowers/analysis/2026-05-25-446-preconditions-synthesis.md
Section 'PositionClosure operator contract spec'.

Single legal entry point for closing a position. The only caller of
transaction() in the close-flow. db/* helpers are pure SQL and receive
con from this operator.

NOTE on `mode` parameter (Voronov AMBER F1): `mode` is a Literal flag
that bifurcates ownership-check behavior (USER enforces, SYSTEM skips).
This works for the two modes present today. If a third mode ever emerges
(BATCH, RECONCILIATION, etc.), the flag-based dispatcher becomes a
homograph of the _tx_or_use pattern this PR closed. Resolve at that
point by splitting into subclasses (`UserPositionClosure`,
`SystemPositionClosure`) sharing an abstract base where the ownership
contract lives in the type, not in a runtime branch.
"""
from __future__ import annotations
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import sqlite3
from typing import Literal, Optional

from db.transaction import transaction
from db.connection import _open_configured_connection

log = logging.getLogger("operators.position_closure")

_VALID_EXIT_REASONS = frozenset({"MANUAL", "SL_HIT", "TP_HIT", "TIME_LIMIT_HIT"})


@dataclass(frozen=True)
class CloseOutcome:
    status: Literal["closed", "not_found", "already_closed"]
    position: Optional[dict]
    pnl_usd: Optional[float]
    pnl_pct: Optional[float]


class PositionClosure:
    """See module docstring."""

    def __init__(
        self,
        pos_id: int,
        exit_price: float,
        exit_reason: str,
        *,
        mode: Literal["USER", "SYSTEM"],
        caller_tenant_id: Optional[int] = None,
        cfg: Optional[dict] = None,
        now: Optional[datetime] = None,
    ) -> None:
        if mode not in ("USER", "SYSTEM"):
            raise ValueError(f"mode must be 'USER' or 'SYSTEM', got {mode!r}")
        if mode == "USER":
            if caller_tenant_id is None or caller_tenant_id <= 0:
                raise ValueError("USER mode requires caller_tenant_id > 0")
        if mode == "SYSTEM" and caller_tenant_id is not None:
            raise ValueError("SYSTEM mode forbids caller_tenant_id (got %r)" % caller_tenant_id)
        if exit_price <= 0:
            raise ValueError(f"exit_price must be > 0, got {exit_price}")
        if exit_reason not in _VALID_EXIT_REASONS:
            raise ValueError(
                f"exit_reason must be one of {sorted(_VALID_EXIT_REASONS)}, got {exit_reason!r}"
            )

        self._pos_id = pos_id
        self._exit_price = exit_price
        self._exit_reason = exit_reason
        self._mode = mode
        self._caller_tenant_id = caller_tenant_id
        self._cfg = cfg
        self._now = now or datetime.now(timezone.utc)

        self._state: Literal["INIT", "NOT_FOUND", "ALREADY_CLOSED", "OK_TO_PROCEED"] = "INIT"
        self._pre_row: Optional[dict] = None
        self._result_row: Optional[dict] = None
        self._consumed = False

    def __enter__(self) -> "PositionClosure":
        raise NotImplementedError  # Filled in Task 4

    def execute(self) -> CloseOutcome:
        raise NotImplementedError  # Filled in Task 4

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        raise NotImplementedError  # Filled in Task 4
