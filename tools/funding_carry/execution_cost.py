"""Funding-carry execution-realism v0.2 — Unidad 1 (spec 2026-06-03 REV 2.1 §3).

ONE-SHOT, settlement-adjacent (enforced), paper-only. Computes T_FLOOR_REAL — the
cost floor measured against the LIVE orderbook, identical construction to
power.cost_floor — and renders PASS/THIN/FAIL against the LIVE pooled rate read
from v0.1's state: same epoch, same type, same denominator (Axiom-0 co-location
invariant, spec §0). The fossil replay is dead as a verdict; cost_v3_today
(same-epoch, via the same recost_four_legs that produced the fossil's cost_v3)
survives as a per-symbol upper-bound diagnostic.

Fail-LOUD: FetchFailed -> ABORT (the verdict sample is never a function of network
weather); InsufficientDepth -> per-symbol flag, INVALID above MAX_INSUFFICIENT_SYMBOLS.
Never writes data/shadow/ (v0.1's namespace); never reads funding.db; ohlcv.db only
via _liq_ro (read-only + busy_timeout). No positions, no orders, no holdout."""
from __future__ import annotations
import json  # noqa: F401
import os  # noqa: F401
import sqlite3  # noqa: F401
import statistics  # noqa: F401
from contextlib import closing  # noqa: F401
from datetime import datetime, timezone  # noqa: F401
from . import simulate  # noqa: F401
from .constants import (NOTIONAL, H_REF_YEARS, MARGIN, T_FLOOR, SHADOW_SYMBOLS,
                        SHADOW_OUTPUT_DIR, OHLCV_DB, PERP_TAKER_FEE, SPOT_TAKER_FEE,
                        SETTLEMENT_WINDOW_MIN, MAX_INSUFFICIENT_SYMBOLS,
                        STATE_MAX_AGE_HOURS, HOLDING_HOURS_DIAG,
                        EXEC_REALISM_OUTPUT_DIR, EXEC_REALISM_VERSION)  # noqa: F401

_SETTLEMENT_MS = 8 * 3_600_000          # funding settles 00:00/08:00/16:00 UTC


class InsufficientDepth(Exception):
    """The real book cannot fill NOTIONAL within the fetched levels — a FINDING
    against the edge (opposite meaning to FetchFailed = network weather)."""


class AbortRun(Exception):
    """Hard-refuse: precondition violated (off-window, stale/invalid v0.1 state,
    calibration drift, fetch failure). No verdict is computed."""


def walk_book(book: dict, notional_usd: float, side: str) -> dict:
    """Walk asks (buy) or bids (sell) to fill a fixed-USD target converted at mid.

    mid = (best_bid + best_ask) / 2 of THIS book; qty_target = notional_usd / mid;
    slippage_cost = |VWAP_fill - mid| * qty_target  (>= 0 both sides by construction).
    Raises InsufficientDepth if the levels cannot fill qty_target (spec §3.2)."""
    if not book["bids"] or not book["asks"]:
        raise InsufficientDepth(f"empty book side ({side})")
    best_bid, best_ask = book["bids"][0][0], book["asks"][0][0]
    mid = (best_bid + best_ask) / 2.0
    qty_target = notional_usd / mid
    levels = book["asks"] if side == "buy" else book["bids"]
    filled = 0.0
    fill_cost = 0.0
    for price, qty in levels:
        take = min(qty, qty_target - filled)
        filled += take
        fill_cost += take * price
        if filled >= qty_target - 1e-12:
            break
    if filled < qty_target - 1e-12:
        raise InsufficientDepth(
            f"filled {filled:.8f} < target {qty_target:.8f} ({side})")
    vwap = fill_cost / qty_target
    return {"mid": mid, "qty_target": qty_target, "vwap": vwap,
            "slippage_cost": abs(vwap - mid) * qty_target}
