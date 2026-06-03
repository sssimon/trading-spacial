"""Funding-carry shadow-deploy v0.1 — pure-rate decay path (spec 2026-06-03 REV 5).

Monitors the ANNUALIZED mean funding rate (pure: no cost/mark/basis) over a trailing
W-week window, pooled equal-weight via evaluate.gate_a, against two frozen fossil anchors:
  - R_FOSSIL_LO / R_FOSSIL_HI : historical fossil band (THIN / ALIVE signal)
  - T_FLOOR                   : cost floor (REFUTED kill after N consecutive blocks below)

The decay-kill counter advances ONLY on non-overlapping W-week blocks (epoch-aligned grid).
Paper-only: no positions, no orders, no holdout. Fail-soft: never raises into the scheduler."""
from __future__ import annotations
import json
import logging
import os
from datetime import datetime, timezone
from . import simulate, evaluate
from .constants import (SHADOW_SYMBOLS, SHADOW_OUTPUT_DIR, SHADOW_VERSION,
                        DECAY_WEEKS_W, DECAY_KILL_N, FUNDING_DB, FUNDING_FETCH_LIMIT,
                        INTERVALS_PER_YEAR, R_FOSSIL_LO, R_FOSSIL_HI, T_FLOOR,
                        H_REF_YEARS)

log = logging.getLogger("funding_carry.shadow")

_WEEK_MS = 7 * 24 * 3_600_000
_MAX_GAP_MS = int(1.5 * 8 * 3_600_000)        # >1.5 funding intervals = a hole


# ---------------------------------------------------------------------------
# Pure-rate statistics (REV 5)
# ---------------------------------------------------------------------------

def symbol_rate(symbol: str, *, funding_db: str, start_ms: int, end_ms: int) -> float:
    """Annualized mean funding rate over the window (PURE: no cost/mark/basis).
    mean(fundingRate) * INTERVALS_PER_YEAR. Raises ValueError if no settlements."""
    funding = simulate.load_funding(funding_db, symbol, start_ms, end_ms)
    if not funding:
        raise ValueError(f"{symbol}: no settlements in window")
    rates = [r for _, r in funding]
    return (sum(rates) / len(rates)) * INTERVALS_PER_YEAR


def pooled_rate(symbols: list[str], *, funding_db: str, start_ms: int, end_ms: int) -> dict:
    """Equal-weight pooled CI of per-symbol annualized mean rate, via gate_a."""
    vals, dropped, per = [], [], {}
    for s in symbols:
        try:
            v = symbol_rate(s, funding_db=funding_db, start_ms=start_ms, end_ms=end_ms)
            vals.append(v)
            per[s] = v
        except ValueError:
            dropped.append(s)
    out = evaluate.gate_a(vals) if vals else {
        "mean": 0.0, "ci_lo": 0.0, "ci_hi": 0.0, "loo_min_mean": 0.0, "pass_a": False, "n": 0}
    out["dropped"] = dropped
    out["per_symbol"] = per
    return out


def block_start(now_ms: int, w_weeks: int) -> int:
    """Epoch-aligned non-overlapping W-week block containing now_ms."""
    block_ms = int(w_weeks) * _WEEK_MS
    return (int(now_ms) // block_ms) * block_ms


def decay_state(*, ci_lo: float, ci_hi: float, r_mean: float, blocks_below: int,
                r_fossil_lo: float, t_floor: float) -> dict:
    """Classify the live pooled rate vs the two frozen fossil anchors (spec §6).

    REFUTED: ci_hi < t_floor for DECAY_KILL_N consecutive blocks (rate no longer covers cost).
    ALIVE  : ci_lo >= r_fossil_lo (rate holds in/above its historical band).
    THIN   : between (compressed below band but ci still above cost floor).

    `blocks_below` is the prior consecutive count; below-floor increments, else resets."""
    below_floor = ci_hi < t_floor
    new_count = (blocks_below + 1) if below_floor else 0
    if new_count >= DECAY_KILL_N:
        state = "REFUTED"
    elif ci_lo >= r_fossil_lo:
        state = "ALIVE"
    else:
        state = "THIN"
    return {"decay_state": state, "blocks_below": new_count}


# ---------------------------------------------------------------------------
# Window completeness (kept from prior version)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ingest_funding(symbols: list[str], db: str, limit: int) -> None:
    """Fetch and append recent funding settlements. No marks — pure-rate path (spec §4)."""
    from . import live_ingest
    for s in symbols:
        rows = live_ingest.fetch_recent_funding(s, limit=limit)
        if rows:
            live_ingest.append_funding(db, s, rows)


def _cal_hash() -> str:
    from backtest_costs import calibration_identity_hash, load_calibration
    return calibration_identity_hash(load_calibration())


def _window_settlement_times(funding_db: str, symbols: list[str],
                             start_ms: int, end_ms: int) -> list[int]:
    """Union of settlement times across symbols in the window (for gap detection)."""
    ts: set[int] = set()
    for s in symbols:
        ts.update(t for t, _ in simulate.load_funding(funding_db, s, start_ms, end_ms))
    return sorted(ts)


def _read_prev_state(path: str) -> dict:
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"blocks_below_floor": 0}


# ---------------------------------------------------------------------------
# Main daily cycle
# ---------------------------------------------------------------------------

def run_once(*, out_dir: str = SHADOW_OUTPUT_DIR, now_ms: int,
             funding_db: str = FUNDING_DB, symbols: tuple = SHADOW_SYMBOLS,
             w_weeks: int = DECAY_WEEKS_W) -> dict:
    """One daily shadow cycle: ingest live funding, recompute the windowed pooled rate,
    update the block-aligned decay state, append to the immutable .jsonl, write derived
    state. Fail-soft: never raises into the scheduler. No positions, no orders, no holdout."""
    os.makedirs(out_dir, exist_ok=True)
    jsonl = os.path.join(out_dir, "funding_carry_signals.jsonl")
    state_path = os.path.join(out_dir, "funding_carry_state.json")
    run_ts = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat()
    prev = _read_prev_state(state_path)

    try:
        try:
            _ingest_funding(list(symbols), funding_db, FUNDING_FETCH_LIMIT)
        except Exception:
            pass  # fail-soft ingest; eval on existing data

        cal = _cal_hash()
        block = block_start(now_ms, w_weeks)
        win_start = now_ms - int(w_weeks) * _WEEK_MS
        times = _window_settlement_times(funding_db, list(symbols), win_start, now_ms)
        complete = window_complete(times, start_ms=win_start, end_ms=now_ms,
                                   max_gap_ms=_MAX_GAP_MS)
        pooled = pooled_rate(list(symbols), funding_db=funding_db,
                             start_ms=win_start, end_ms=now_ms)

        last_block = prev.get("last_counted_block")
        blocks_below = int(prev.get("blocks_below_floor", 0))
        is_new_block = (last_block is None) or (block != last_block)

        if not complete:
            # Incomplete window: do NOT advance the counter; classify as INCOMPLETE.
            decay = "INCOMPLETE"
            new_blocks_below = blocks_below
            new_last_block = last_block
        elif is_new_block:
            # New non-overlapping block: evaluate and advance counter.
            ds = decay_state(ci_lo=pooled["ci_lo"], ci_hi=pooled["ci_hi"],
                             r_mean=pooled["mean"], blocks_below=blocks_below,
                             r_fossil_lo=R_FOSSIL_LO, t_floor=T_FLOOR)
            decay = ds["decay_state"]
            new_blocks_below = ds["blocks_below"]
            new_last_block = block
        else:
            # Same block already counted: re-classify for display but DO NOT advance counter.
            # Recompute the display state as if blocks_below is unchanged.
            ds = decay_state(ci_lo=pooled["ci_lo"], ci_hi=pooled["ci_hi"],
                             r_mean=pooled["mean"], blocks_below=max(0, blocks_below - 1),
                             r_fossil_lo=R_FOSSIL_LO, t_floor=T_FLOOR)
            # If already REFUTED (blocks_below >= N), preserve that verdict.
            decay = "REFUTED" if blocks_below >= DECAY_KILL_N else ds["decay_state"]
            new_blocks_below = blocks_below
            new_last_block = last_block

        line = {
            "run_ts_utc": run_ts,
            "block_start_ms": block,
            "is_new_block": bool(is_new_block),
            "R_pooled": pooled["mean"],
            "R_ci_lo": pooled["ci_lo"],
            "R_ci_hi": pooled["ci_hi"],
            "n": pooled["n"],
            "per_symbol_rate": pooled.get("per_symbol", {}),
            "dropped": pooled["dropped"],
            "R_fossil_lo": R_FOSSIL_LO,
            "t_floor": T_FLOOR,
            "window_complete": complete,
            "decay_state": decay,
            "blocks_below_floor": new_blocks_below,
            "calibration_identity_hash": cal,
            "shadow_version": SHADOW_VERSION,
        }
        with open(jsonl, "a", encoding="utf-8") as f:    # append-only ledger
            f.write(json.dumps(line) + "\n")
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({**line, "last_counted_block": new_last_block,
                       "decay_weeks_w": int(w_weeks),
                       "R_fossil_hi": R_FOSSIL_HI,
                       "h_ref_years": H_REF_YEARS,
                       "decay_kill_n": DECAY_KILL_N}, f, indent=2)
        return line

    except Exception as e:                               # noqa: BLE001 — fail-soft
        log.warning("run_once failed: %s", e, exc_info=True)
        err = {
            "run_ts_utc": run_ts,
            "decay_state": "ERROR",
            "error": str(e),
            "blocks_below_floor": int(prev.get("blocks_below_floor", 0)),
            "last_counted_block": prev.get("last_counted_block"),
            "shadow_version": SHADOW_VERSION,
        }
        try:
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(err, f, indent=2)
        except Exception:                                # noqa: BLE001
            pass
        return err


if __name__ == "__main__":
    import time
    print(run_once(now_ms=int(time.time() * 1000)))
