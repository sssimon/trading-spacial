"""Funding-carry shadow-deploy v0.1 (spec 2026-06-03).

Recomputes the FOSSIL'S OWN statistic (simulate.carry_for_symbol -> net_return_annual,
span-annualized, entry-mark funding, $/notional) over a trailing W-week window, pools
it equal-weight via evaluate.gate_a (identical bootstrap CI), and fires a pre-registered
decay-kill when the live CI-hi falls below the backtest CI-lo (0.0502) for N consecutive
non-overlapping windows. Paper-only: no positions, no orders, no holdout. The statistic
reuses carry_for_symbol verbatim (audit N1) — no new annualization formula."""
from __future__ import annotations
from . import simulate, evaluate
from .constants import DECAY_CI_LO, DECAY_KILL_N


def symbol_window_return(symbol: str, *, funding_db: str, ohlcv_db: str,
                         start_ms: int, end_ms: int) -> float:
    """net_return_annual for `symbol` over [start_ms, end_ms], computed by the fossil's
    carry_for_symbol (span-annualized). Raises ValueError on missing prices (drop upstream)."""
    funding = simulate.load_funding(funding_db, symbol, start_ms, end_ms)
    if len(funding) < 2:
        raise ValueError(f"{symbol}: <2 settlements in window")
    entry_ms, exit_ms = funding[0][0], funding[-1][0]
    rec = simulate.carry_for_symbol(
        symbol=symbol, funding=funding,
        spot_entry=simulate.spot_price_at(ohlcv_db, symbol, entry_ms),
        spot_exit=simulate.spot_price_at(ohlcv_db, symbol, exit_ms),
        perp_entry=simulate.perp_price_at(funding_db, symbol, entry_ms),
        perp_exit=simulate.perp_price_at(funding_db, symbol, exit_ms),
        liq=simulate.spot_liquidity(ohlcv_db, symbol, entry_ms))
    return rec["net_return_annual"]


def pooled_decay(symbols: list[str], *, funding_db: str, ohlcv_db: str,
                 start_ms: int, end_ms: int) -> dict:
    """Equal-weight pooled CI of net_return_annual over the window — identical to gate_a.
    Symbols with <2 settlements / missing prices are dropped loud (not poisoned)."""
    annual, dropped = [], []
    for s in symbols:
        try:
            annual.append(symbol_window_return(
                s, funding_db=funding_db, ohlcv_db=ohlcv_db,
                start_ms=start_ms, end_ms=end_ms))
        except ValueError:
            dropped.append(s)
    out = evaluate.gate_a(annual) if annual else {
        "mean": 0.0, "ci_lo": 0.0, "ci_hi": 0.0, "loo_min_mean": 0.0, "pass_a": False, "n": 0}
    out["dropped"] = dropped
    return out


_HEADLINE = 0.0633        # backtest gate_a mean — top of the thin band


def decay_state(*, ci_lo: float, ci_hi: float, weeks_below: int) -> dict:
    """State machine over the live CI vs the in-sample threshold (spec §6).

    REFUTED   : ci_hi < DECAY_CI_LO for DECAY_KILL_N consecutive non-overlapping windows.
    THIN      : CI overlaps [DECAY_CI_LO, headline] — compressing, not dead.
    ALIVE     : CI sits at/above the band.

    `weeks_below` is the prior consecutive count; this call updates it. A window whose
    ci_hi recovers to/above the threshold RESETS the counter (consecutive, not cumulative)."""
    below = ci_hi < DECAY_CI_LO
    new_count = (weeks_below + 1) if below else 0
    if new_count >= DECAY_KILL_N:
        state = "REFUTED"
    elif ci_hi >= _HEADLINE:
        state = "ALIVE"
    elif ci_hi >= DECAY_CI_LO:
        state = "THIN"            # CI overlaps [DECAY_CI_LO, headline] — compressing, not dead
    else:
        state = "THIN"            # below once but not yet N — still compressing, not dead
    return {"decay_state": state, "weeks_below": new_count}


def reconcile_settlement(*, prev_rate: float, settled_rate: float,
                         mark: float, units: float) -> dict:
    """Secondary operational sanity check (spec §5). Measures ONE-STEP surprise, NOT decay
    (the naive random-walk baseline persists the prior rate, so it absorbs monotone decay —
    decay is judged in §6 on the pooled CI, not here). Useful only for ingest/mark anomalies."""
    expected_net = prev_rate * mark * units
    realized_net = settled_rate * mark * units
    return {"expected_net": expected_net, "realized_net": realized_net,
            "drift": realized_net - expected_net}


def window_complete(settlement_times_ms: list[int], *, start_ms: int, end_ms: int,
                    max_gap_ms: int) -> bool:
    """True iff the settlement series covers [start_ms, end_ms] with no gap > max_gap_ms.
    A gap marks the window incomplete -> the daily job SKIPS the decay-kill eval (spec §4
    fail-safe: a data hole must not trigger a false REFUTED)."""
    ts = sorted(t for t in settlement_times_ms if start_ms <= t <= end_ms)
    if len(ts) < 2:
        return False
    if ts[0] - start_ms > max_gap_ms or end_ms - ts[-1] > max_gap_ms:
        return False
    return all((b - a) <= max_gap_ms for a, b in zip(ts, ts[1:]))


import json
import os
from datetime import datetime, timezone
from .constants import (SHADOW_SYMBOLS, SHADOW_OUTPUT_DIR, SHADOW_VERSION,
                        DECAY_WEEKS_W, FUNDING_DB, OHLCV_DB, FUNDING_FETCH_LIMIT)

_WEEK_MS = 7 * 24 * 3_600_000
_MAX_GAP_MS = int(1.5 * 8 * 3_600_000)        # >1.5 funding intervals = a hole


def _ingest(symbols, db, limit):
    from . import live_ingest
    live_ingest.ingest_live(symbols, db_path=db, limit=limit)


def _cal_hash():
    from backtest_costs import calibration_identity_hash, load_calibration
    return calibration_identity_hash(load_calibration())


def _window_settlement_times(funding_db, symbols, start_ms, end_ms):
    """Union of settlement times across symbols in the window (for gap detection)."""
    ts = set()
    for s in symbols:
        ts.update(t for t, _ in simulate.load_funding(funding_db, s, start_ms, end_ms))
    return sorted(ts)


def _read_prev_state(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"weeks_below": 0}


def run_once(*, out_dir: str = SHADOW_OUTPUT_DIR, now_ms: int,
             funding_db: str = FUNDING_DB, ohlcv_db: str = OHLCV_DB,
             symbols: tuple = SHADOW_SYMBOLS) -> dict:
    """One daily shadow cycle: ingest live data, recompute the windowed pooled CI, update
    the decay state, append a per-symbol line to the immutable .jsonl, write derived state.
    Fail-soft: never raises into the scheduler. No positions, no orders, no holdout."""
    os.makedirs(out_dir, exist_ok=True)
    jsonl = os.path.join(out_dir, "funding_carry_signals.jsonl")
    state_path = os.path.join(out_dir, "funding_carry_state.json")
    start_ms, end_ms = now_ms - DECAY_WEEKS_W * _WEEK_MS, now_ms
    cal = _cal_hash()
    ts_utc = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat()

    try:
        _ingest(list(symbols), funding_db, FUNDING_FETCH_LIMIT)
    except Exception:                            # noqa: BLE001 — fail-soft; eval on what we have
        pass

    times = _window_settlement_times(funding_db, list(symbols), start_ms, end_ms)
    complete = window_complete(times, start_ms=start_ms, end_ms=end_ms, max_gap_ms=_MAX_GAP_MS)
    pooled = pooled_decay(list(symbols), funding_db=funding_db, ohlcv_db=ohlcv_db,
                          start_ms=start_ms, end_ms=end_ms)
    prev = _read_prev_state(state_path)

    if complete:
        ds = decay_state(ci_lo=pooled["ci_lo"], ci_hi=pooled["ci_hi"],
                         weeks_below=int(prev.get("weeks_below", 0)))
    else:
        ds = {"decay_state": "INCOMPLETE", "weeks_below": int(prev.get("weeks_below", 0))}

    line = {"settlement_ts_utc": ts_utc, "window": [start_ms, end_ms],
            "pooled_mean": pooled["mean"], "ci_lo": pooled["ci_lo"], "ci_hi": pooled["ci_hi"],
            "n": pooled["n"], "dropped": pooled["dropped"], "window_complete": complete,
            "decay_state": ds["decay_state"], "weeks_below": ds["weeks_below"],
            "calibration_identity_hash": cal, "shadow_version": SHADOW_VERSION}
    with open(jsonl, "a", encoding="utf-8") as f:    # append-only ledger
        f.write(json.dumps(line) + "\n")
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump({**line, "decay_weeks_w": DECAY_WEEKS_W}, f, indent=2)
    return line


if __name__ == "__main__":
    import time
    print(run_once(now_ms=int(time.time() * 1000)))
