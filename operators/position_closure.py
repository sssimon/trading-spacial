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
import math
from typing import Literal, Optional

from db import transaction as _tx_module  # imported as module so tests can
                                            # patch `transaction` on it
from db.transaction import PrecheckConn, precheck_connection
from db.positions import db_get_position_by_id, db_close_position_sql, _calc_pnl
from operators.precheck import (
    PositionSnapshot,
    OwnershipValidatedSnapshot,
    _build_validated_snapshot,
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

        # NOTE: an earlier spec (docs/.../2026-05-25-446-preconditions-synthesis.md
        # lines 137-148) declared a `_state` field as a four-valued string state
        # machine for the closure lifecycle. The implementation absorbed that
        # axis into PrecheckResult (the tagged union stored as
        # _precheck_result) and the operator-lifecycle axis into _entered /
        # _consumed booleans. `_state` was declared on this line but never
        # assigned anywhere — fossil of the abandoned design. Deleted per
        # Voronov 2026-05-26 (#492): "an operator that has not yet learned to
        # delete its own ghosts will accumulate them until a future
        # contributor can no longer tell which fields are load-bearing."
        self._result_row: Optional[dict] = None
        self._result_pnl: tuple[Optional[float], Optional[float]] = (None, None)
        # Two flags to enforce single-use symmetrically (#460):
        #   _entered: set by __enter__. Prevents re-entry on the same instance
        #             even when execute() was never called inside the first
        #             enter (degenerate enter-without-execute flow).
        #   _consumed: set by execute(). Prevents double-execute within a
        #              single enter block.
        # Both raise RuntimeError on re-use. Single-use means single-use.
        self._entered = False
        self._consumed = False
        self._precheck_result: PrecheckResult | None = None

    def __enter__(self) -> "PositionClosure":
        if self._entered:
            raise RuntimeError(
                "PositionClosure has already been entered; instances are single-use"
            )
        self._entered = True
        with precheck_connection() as precheck_con:
            self._precheck_result = self._run_precheck(precheck_con)
        return self

    def _run_precheck(self, precheck_con: PrecheckConn) -> PrecheckResult:
        """Read outside any transaction; return one of the 3 PrecheckResult variants.

        Implements ownership-before-lock (USER mode): a row whose tenant_id does
        not match caller_tenant_id collapses to PrecheckNotFound (IDOR-safe).

        Signature consumes PrecheckConn (NewType from db.transaction) to declare
        the KIND of connection this operator expects — Voronov-coherent: the
        operator names its consumed contract, not just the helpers. Helper
        signatures (db_get_position_by_id et al.) are intentionally not updated;
        they accept either NewType via Python's structural subtyping of
        sqlite3.Connection.
        """
        row = db_get_position_by_id(precheck_con, self._pos_id)

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

        return PrecheckOkToProceed(snapshot=_build_validated_snapshot(snapshot))

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
            raise RuntimeError(
                "PositionClosure has already been executed; instances are single-use"
            )
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

        # PrecheckOkToProceed: write-tx must re-validate ALL snapshot fields.
        # OwnershipValidatedSnapshot guarantees ownership was checked at precheck;
        # the snapshot's mutable fields (everything in PositionSnapshot — tenant_id,
        # status, entry_price, qty, direction, symbol) MUST be re-validated against
        # the fresh row inside BEGIN IMMEDIATE. Schema does not enforce immutability
        # of entry_price/qty/direction/symbol (CLAUDE.md "Capas de enforcement"),
        # so the write-tx is the only place where stale snapshots are caught.
        validated = result.snapshot   # OwnershipValidatedSnapshot
        snap = validated.inner         # PositionSnapshot
        with _tx_module.transaction() as con:
            row = db_get_position_by_id(con, self._pos_id)
            if row is None:
                return CloseOutcome(status="not_found", position=None, pnl_usd=None, pnl_pct=None)

            # Status handling (3 branches: open / closed / other).
            # F1 fix per Voronov: closed branch normalizes CloseOutcome.position shape
            # to snapshot shape (same as precheck-detected already_closed branch).
            # Consumers needing exit_* fields must read the row directly via a separate query.
            # F2 fix per Voronov: status != "open" AND != "closed" (e.g., "cancelled")
            # MUST NOT collapse to already_closed. The consumer needs the real state.
            if row["status"] == "closed":
                race_snap = PositionSnapshot(
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
                    position=self._snapshot_to_dict(race_snap),
                    pnl_usd=None, pnl_pct=None,
                )
            if row["status"] != "open":
                race_snap = PositionSnapshot(
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
                    position=self._snapshot_to_dict(race_snap),
                    pnl_usd=None, pnl_pct=None,
                )

            # Re-validate ALL other mutable fields per-field (#469 + F6).
            # tenant_id is the #461 IDOR-safe path: silent collapse, NO log
            # (a log line would itself leak the row's existence to USER mode).
            # entry_price/qty/direction/symbol drift = data-integrity event
            # (#478): same IDOR-safe user response, but surfaced in operator
            # logs so the event is distinguishable from a pure tenant collapse.
            #
            # entry_price/qty use math.isclose (#475): a future migration that
            # touches these REAL columns via arithmetic may lose byte-identity
            # without changing the mathematical value. Exact equality would
            # false-positive into not_found.
            drift_fields: list[tuple[str, object, object]] = []
            if row["tenant_id"] != snap.tenant_id:
                drift_fields.append(("tenant_id", snap.tenant_id, row["tenant_id"]))
            if not math.isclose(row["entry_price"], snap.entry_price, rel_tol=1e-9, abs_tol=1e-9):
                drift_fields.append(("entry_price", snap.entry_price, row["entry_price"]))
            if not math.isclose(row["qty"], snap.qty, rel_tol=1e-9, abs_tol=1e-9):
                drift_fields.append(("qty", snap.qty, row["qty"]))
            if row["direction"] != snap.direction:
                drift_fields.append(("direction", snap.direction, row["direction"]))
            if row["symbol"] != snap.symbol:
                drift_fields.append(("symbol", snap.symbol, row["symbol"]))

            if drift_fields:
                # Non-tenant drift is a data-integrity event — log per field.
                # tenant_id alone stays silent (IDOR safety: #461).
                for field, precheck_val, write_tx_val in drift_fields:
                    if field == "tenant_id":
                        continue
                    log.error(
                        "integrity event: snapshot field drift for pos_id=%s "
                        "field=%s precheck=%r write_tx=%r",
                        self._pos_id, field, precheck_val, write_tx_val,
                    )
                return CloseOutcome(status="not_found", position=None, pnl_usd=None, pnl_pct=None)

            # All snapshot fields confirmed. Snapshot is trusted; proceed.
            # Schema now enforces qty NOT NULL (CHECK constraint with
            # exemption for status='legacy_unmeasurable' — see #467).
            pnl_usd, pnl_pct = _calc_pnl(
                snap.direction, snap.entry_price, self._exit_price, snap.qty,
            )
            exit_ts = self._now.isoformat()
            closed_row = db_close_position_sql(
                con, self._pos_id, self._exit_price, self._exit_reason,
                exit_ts, pnl_usd, pnl_pct,
            )
            if snap.tenant_id is not None and pnl_usd is not None:
                _capital_module.apply_pnl_to_capital(con, snap.tenant_id, pnl_usd)
            elif snap.tenant_id is None:
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
