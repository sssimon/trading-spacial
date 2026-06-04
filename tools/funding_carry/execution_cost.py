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


def roundtrip_real_cost(perp_book: dict, spot_book: dict, *, notional: float = NOTIONAL,
                        spot_fee: float = SPOT_TAKER_FEE,
                        perp_fee: float = PERP_TAKER_FEE) -> dict:
    """All-in 4-leg roundtrip cost on the SAME snapshot (approximation §6.1):
    open = spot-buy + perp-sell; close = spot-sell + perp-buy.
    Per leg: slippage + taker_fee * notional.
    Leg/denominator convention pinned to the fossil (spec §3.3 / Adrian REV2-F5):
    recost_four_legs = 4 fills with per-leg notional; cost_floor divides the 4-leg
    total by NOTIONAL=10000 per-leg, NOT 2x. This function returns the 4-leg USD total."""
    legs = {
        "spot_buy":  walk_book(spot_book, notional, "buy"),
        "perp_sell": walk_book(perp_book, notional, "sell"),
        "spot_sell": walk_book(spot_book, notional, "sell"),
        "perp_buy":  walk_book(perp_book, notional, "buy"),
    }
    slip = sum(leg["slippage_cost"] for leg in legs.values())
    fees = 2 * notional * spot_fee + 2 * notional * perp_fee
    return {"cost_real": slip + fees, "slippage_total": slip,
            "fees_total": fees, "legs": legs}


def t_floor_real(costs_usd: list[float], *, notional: float = NOTIONAL,
                 h_ref_years: float = H_REF_YEARS, margin: float = MARGIN) -> float:
    """Annualized REAL cost floor: median(cost/notional)/h_ref_years + margin.
    Construction IDENTICAL to power.cost_floor (median — PENDLE's cost is ~40x
    others; per-leg NOTIONAL denominator; H_REF amortization) with the live
    walked-book cost in place of the fossil's cost_v3. Keystone: feeding the
    fossil's cost_v3 values must reproduce the frozen T_FLOOR exactly."""
    per_sym = [c / notional for c in costs_usd]
    return statistics.median(per_sym) / h_ref_years + margin


def settlement_check(now_ms: int, *, window_min: int = SETTLEMENT_WINDOW_MIN) -> int:
    """Return the funding settlement (ms, 8h-grid: 00/08/16 UTC) this run is adjacent
    to. Hard-refuse if now is more than window_min minutes after the last settlement —
    instrument-time co-location is ENFORCED, not trusted (spec §3 / Adrian REV2-F7)."""
    last = (int(now_ms) // _SETTLEMENT_MS) * _SETTLEMENT_MS
    delta_min = (now_ms - last) / 60_000.0
    if delta_min > window_min:
        raise AbortRun(f"off-window: {delta_min:.1f}min after settlement "
                       f"> SETTLEMENT_WINDOW_MIN={window_min}")
    return last


_REQUIRED_STATE_KEYS = ("run_ts_utc", "decay_state", "R_pooled", "R_ci_lo",
                        "R_ci_hi", "calibration_identity_hash")


def read_v01_state(path: str, *, now_ms: int,
                   max_age_hours: float = STATE_MAX_AGE_HOURS) -> dict:
    """Validated read of v0.1's state.json — the verdict's LEFT operand (spec §3
    preconditions / Adrian REV2-F4/F8). ABORT (clean, never KeyError) on:
    missing file; missing keys (v0.1's ERROR branch omits them); staleness
    > max_age_hours; decay_state not in {ALIVE, THIN} (ERROR/INCOMPLETE = no
    operand today; REFUTED = v0.1 already killed the edge, v0.2 is moot)."""
    if not os.path.exists(path):
        raise AbortRun(f"v0.1 state missing: {path}")
    with open(path, encoding="utf-8") as fh:
        st = json.load(fh)
    missing = [k for k in _REQUIRED_STATE_KEYS if k not in st]
    if missing:
        raise AbortRun(f"v0.1 state missing keys {missing} "
                       f"(decay_state={st.get('decay_state')!r})")
    dt = datetime.fromisoformat(st["run_ts_utc"])
    if dt.tzinfo is None:
        raise AbortRun("v0.1 state run_ts_utc is tz-naive — ambiguous on non-UTC machines")
    run_ms = int(dt.timestamp() * 1000)
    age_h = (now_ms - run_ms) / 3_600_000.0
    if age_h > max_age_hours:
        raise AbortRun(f"v0.1 state stale: {age_h:.1f}h > {max_age_hours}h")
    if st["decay_state"] not in ("ALIVE", "THIN"):
        raise AbortRun(f"v0.1 decay_state={st['decay_state']!r} — no valid left operand")
    return st


def verdict(t_floor_real_val: float, state: dict) -> str:
    """PASS/THIN/FAIL — same semantics as v0.1 REV 5 but against the REAL floor.
    A snapshot: does NOT touch v0.1's kill counter (spec §8)."""
    if state["R_ci_lo"] >= t_floor_real_val:
        return "PASS"
    if state["R_ci_hi"] < t_floor_real_val:
        return "FAIL"
    return "THIN"


def _liq_ro(ohlcv_db: str, symbol: str, ts_ms: int, *,
            busy_timeout_ms: int = 5000) -> float:
    """spot_liquidity's exact query with an explicit busy_timeout on an own read-only
    connection — v0.2 must not modify v0.1 functions and must not hit SQLITE_BUSY
    against scanner writes (spec §3 / Adrian REV2-F10). Same semantics: 30-day
    rolling USD/min proxy from spot 1h bars; NaN under 120 bars."""
    with closing(sqlite3.connect(f"file:{ohlcv_db}?mode=ro", uri=True)) as con:
        con.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        rows = con.execute(
            "SELECT close, volume FROM ohlcv WHERE symbol=? AND timeframe='1h' "
            "AND open_time<=? ORDER BY open_time DESC LIMIT 720",
            (symbol, ts_ms)).fetchall()
    if len(rows) < 120:
        return float("nan")
    return sum(c * v / 60.0 for c, v in rows) / len(rows)


def cost_v3_today(symbol: str, *, spot_mid: float, perp_mid: float, liq: float,
                  holding_hours: float = HOLDING_HOURS_DIAG) -> float:
    """Same-epoch v3 cost via the SAME function that produced the fossil's cost_v3
    (recost_four_legs — one invocation, not a hand reconstruction of
    compute_trade_costs; Adrian REV2-F9), with live mids/liq and the frozen
    diagnostic holding. Same 4-leg basis as roundtrip_real_cost by construction."""
    units = NOTIONAL / spot_mid
    return simulate.recost_four_legs(symbol=symbol, units=units, spot_price=spot_mid,
                                     perp_price=perp_mid, liq=liq,
                                     holding_hours=holding_hours)
