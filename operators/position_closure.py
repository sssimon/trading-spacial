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
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Literal, Optional

from db import transaction as _tx_module  # imported as module so tests can
                                            # patch `transaction` on it
from db.transaction import precheck_connection
from db.positions import db_get_position_by_id, db_close_position_sql, _calc_pnl
from operators.precheck import (
    PositionSnapshot,
    PrecheckNotFound,
    PrecheckAlreadyClosed,
    PrecheckOkToProceed,
    PrecheckRejectedState,
    PrecheckResult,
)
from db import capital as _capital_module  # imported as module so tests can
                                            # patch `apply_pnl_to_capital` on it
from api.positions import _write_position_event_log, update_positions_json
from api.config import load_config
from health import trigger_health_evaluation
from notifier import notify, PositionExitEvent

log = logging.getLogger("operators.position_closure")

_VALID_EXIT_REASONS = frozenset({"MANUAL", "MANUAL_AGENT", "SL_HIT", "TP_HIT", "TIME_LIMIT_HIT"})


@dataclass(frozen=True)
class CloseOutcome:
    status: Literal["closed", "not_found", "already_closed", "rejected_unexpected_state"]
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
        self._result_row: Optional[dict] = None
        self._result_pnl: tuple[Optional[float], Optional[float]] = (None, None)
        self._consumed = False
        self._precheck_result: PrecheckResult | None = None

    def __enter__(self) -> "PositionClosure":
        if self._consumed:
            raise RuntimeError("PositionClosure is single-use; construct a new one")
        self._precheck_result = self._run_precheck()
        return self

    def _run_precheck(self) -> PrecheckResult:
        """Read outside any transaction; return one of the 3 PrecheckResult variants.

        Implements ownership-before-lock (USER mode): a row whose tenant_id does
        not match caller_tenant_id collapses to PrecheckNotFound (IDOR-safe).
        """
        with precheck_connection() as con:
            row = db_get_position_by_id(con, self._pos_id)

        if row is None:
            return PrecheckNotFound()

        snapshot = PositionSnapshot(
            pos_id=row["id"],
            tenant_id=row["tenant_id"],
            status=row["status"],
            symbol=row["symbol"],
            direction=row["direction"],
            entry_price=row["entry_price"],
            qty=row["qty"],
        )

        if self._mode == "USER":
            if snapshot.tenant_id != self._caller_tenant_id:
                return PrecheckNotFound()  # IDOR-safe collapse

        if snapshot.status == "closed":
            return PrecheckAlreadyClosed(snapshot=snapshot)
        if snapshot.status != "open":
            # F2 fix per Voronov: status not in {open, closed} (e.g., "cancelled")
            # MUST be reported distinctly, not collapsed to already_closed.
            return PrecheckRejectedState(snapshot=snapshot)

        return PrecheckOkToProceed(snapshot=snapshot)

    @staticmethod
    def _snapshot_to_dict(snapshot: PositionSnapshot) -> dict:
        """Convert a PositionSnapshot back to dict for the CloseOutcome.position
        field (which existing callers expect as a dict)."""
        return {
            "id": snapshot.pos_id,
            "tenant_id": snapshot.tenant_id,
            "status": snapshot.status,
            "symbol": snapshot.symbol,
            "direction": snapshot.direction,
            "entry_price": snapshot.entry_price,
            "qty": snapshot.qty,
        }

    def execute(self) -> CloseOutcome:
        if self._consumed:
            raise RuntimeError("PositionClosure already executed; single-use")
        self._consumed = True

        result = self._precheck_result

        if isinstance(result, PrecheckNotFound):
            return CloseOutcome(status="not_found", position=None, pnl_usd=None, pnl_pct=None)

        if isinstance(result, PrecheckAlreadyClosed):
            return CloseOutcome(
                status="already_closed",
                position=self._snapshot_to_dict(result.snapshot),
                pnl_usd=None,
                pnl_pct=None,
            )

        if isinstance(result, PrecheckRejectedState):
            return CloseOutcome(
                status="rejected_unexpected_state",
                position=self._snapshot_to_dict(result.snapshot),
                pnl_usd=None,
                pnl_pct=None,
            )

        # PrecheckOkToProceed: write-tx must re-validate snapshot's mutable fields.
        snapshot = result.snapshot
        with _tx_module.transaction() as con:
            row = db_get_position_by_id(con, self._pos_id)
            if row is None:
                return CloseOutcome(status="not_found", position=None, pnl_usd=None, pnl_pct=None)
            # #461 closure: tenant_id re-validation is obligatory by construction.
            if row["tenant_id"] != snapshot.tenant_id:
                # Tenant reassigned between precheck and write-tx. IDOR-safe collapse.
                return CloseOutcome(status="not_found", position=None, pnl_usd=None, pnl_pct=None)
            if row["status"] == "closed":
                # Race: another caller closed this position between precheck and BEGIN IMMEDIATE.
                # Do NOT set self._result_row — the other caller already fired side-effects.
                # F1 fix per Voronov: normalize CloseOutcome.position shape to snapshot
                # shape (same as precheck-detected already_closed branch). Consumers
                # needing exit_* fields must read the row directly via a separate query.
                race_snapshot = PositionSnapshot(
                    pos_id=row["id"],
                    tenant_id=row["tenant_id"],
                    status=row["status"],
                    symbol=row["symbol"],
                    direction=row["direction"],
                    entry_price=row["entry_price"],
                    qty=row["qty"],
                )
                return CloseOutcome(
                    status="already_closed",
                    position=self._snapshot_to_dict(race_snapshot),
                    pnl_usd=None,
                    pnl_pct=None,
                )
            if row["status"] != "open":
                # F2 fix per Voronov: status != "open" AND != "closed" (e.g., "cancelled")
                # MUST NOT collapse to already_closed. The consumer needs to know the real
                # state to decide retry vs notify vs log-skip.
                rejected_snapshot = PositionSnapshot(
                    pos_id=row["id"],
                    tenant_id=row["tenant_id"],
                    status=row["status"],
                    symbol=row["symbol"],
                    direction=row["direction"],
                    entry_price=row["entry_price"],
                    qty=row["qty"],
                )
                return CloseOutcome(
                    status="rejected_unexpected_state",
                    position=self._snapshot_to_dict(rejected_snapshot),
                    pnl_usd=None,
                    pnl_pct=None,
                )

            # Snapshot's immutable fields trusted; consume directly.
            # Schema now enforces qty NOT NULL (CHECK constraint with
            # exemption for status='legacy_unmeasurable' — see #467).
            pnl_usd, pnl_pct = _calc_pnl(
                snapshot.direction, snapshot.entry_price, self._exit_price, snapshot.qty,
            )
            exit_ts = self._now.isoformat()
            closed_row = db_close_position_sql(
                con, self._pos_id, self._exit_price, self._exit_reason,
                exit_ts, pnl_usd, pnl_pct,
            )
            if snapshot.tenant_id is not None and pnl_usd is not None:
                _capital_module.apply_pnl_to_capital(con, snapshot.tenant_id, pnl_usd)
            elif snapshot.tenant_id is None:
                log.warning(
                    "PositionClosure: skipping capital roll-in for legacy tenant_id=NULL pos_id=%s",
                    self._pos_id,
                )
            self._result_row = closed_row
            self._result_pnl = (pnl_usd, pnl_pct)
        return CloseOutcome(
            status="closed",
            position=self._result_row,
            pnl_usd=self._result_pnl[0],
            pnl_pct=self._result_pnl[1],
        )

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is not None:
            # Invariant 5: on exception inside the block (including from
            # execute()), no post-commit side-effects fire. Propagate.
            log.error(
                "PositionClosure failed: pos_id=%s mode=%s caller_tenant_id=%s "
                "exit_reason=%s exit_price=%s exception=%s",
                self._pos_id,
                self._mode,
                self._caller_tenant_id,
                self._exit_reason,
                self._exit_price,
                exc_type.__name__,
            )
            return False  # propagate

        if self._result_row is None:
            # NOT_FOUND or ALREADY_CLOSED path — no side-effects to fire.
            return False

        cfg = self._cfg or load_config()
        pos_row = self._result_row
        pnl_usd, pnl_pct = self._result_pnl

        # Invariant 6: each side-effect fires exactly once on success.
        # Invariant 10 (AMBER F2): best-effort — failures are logged and
        # swallowed so the committed close is not "undone" by a notify glitch.

        # 1) event log
        try:
            _write_position_event_log(pos_row, self._exit_reason, self._exit_price)
        except Exception as e:
            log.warning("PositionClosure: event log failed: %s", e)

        # 2) health trigger
        try:
            trigger_health_evaluation(pos_row["symbol"], cfg)
        except Exception as e:
            log.warning("PositionClosure: health trigger failed: %s", e)

        # 3) notify
        try:
            # Preserve pre-migration external event semantics: scanner
            # used to map SL_HIT→SL, TP_HIT→TP, TIME_LIMIT_HIT→TIME_LIMIT
            # before constructing PositionExitEvent. Internal
            # self._exit_reason stays as the DB-canonical *_HIT form;
            # only the event payload carries the stripped tier code.
            event_reason = self._exit_reason.replace("_HIT", "")
            event = PositionExitEvent(
                symbol=pos_row.get("symbol", ""),
                direction=str(pos_row.get("direction", "LONG")).upper(),
                exit_reason=event_reason,
                entry_price=float(pos_row.get("entry_price") or 0.0),
                exit_price=float(self._exit_price),
                pnl_usd=float(pnl_usd) if pnl_usd is not None else 0.0,
                pnl_pct=float(pnl_pct) if pnl_pct is not None else 0.0,
            )
            notify(event, cfg, tenant_id=pos_row.get("tenant_id"))
        except Exception as e:
            log.warning("PositionClosure: notify failed: %s", e)

        # 4) positions snapshot
        try:
            update_positions_json()
        except Exception as e:
            log.warning("PositionClosure: positions snapshot failed: %s", e)

        return False
