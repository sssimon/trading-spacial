"""Falsify the v3 cost bound against live realized P&L (read-only).

R1: live data is a sanity CEILING, never a fit target. The bound is falsified
ONLY when it underestimates an indisputable cost, or when it INVERTS a per-symbol
price-winner into a backtest loser. NN#3: reads prod signals.db (mode=ro) + 2026
OHLCV only; NEVER pre-2025-04-29 frames; NEVER imports holdout access.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timezone

from backtest_costs import (
    load_calibration, tier_for_symbol, compute_trade_costs, PUBLISHED_TAKER_FEE_BPS,
)

EXPECTED_MIN = 20
NOISE_BAND_USD = 5.0
MANDATORY_LOWER_BOUND_BPS = 2 * PUBLISHED_TAKER_FEE_BPS   # 10.0 RT bps, exchange-published
_WINDOW_START = "2026-05-21"  # post-cutoff (>> holdout 2025-04-29); NN#3


class InsufficientDataError(RuntimeError):
    pass


class BoundFalsifiedError(AssertionError):
    """The v3 bound failed falsification (mandatory fee floor breached, or a
    per-symbol price-winner inverted to a net loser). Subclasses AssertionError
    so existing pytest.raises(AssertionError, ...) still matches, but is a real
    raise that SURVIVES `python -O` (a bare assert would be silently stripped)."""


_REQUIRED_KEYS = ("symbol", "pnl_usd", "size_usd", "liquidity_per_min")


def _validate_row(r: dict) -> None:
    missing = [k for k in _REQUIRED_KEYS if k not in r]
    if missing:
        raise ValueError(f"position row missing required keys {missing}: {r}")
    size = r["size_usd"]
    if not (isinstance(size, (int, float)) and math.isfinite(size) and size > 0):
        raise ValueError(f"position row has non-positive/non-finite size_usd={size!r}: {r}")
    pnl = r["pnl_usd"]
    if not (isinstance(pnl, (int, float)) and math.isfinite(pnl)):
        raise ValueError(f"position row has non-finite pnl_usd={pnl!r}: {r}")


def _v3_cost_usd(symbol: str, size_usd: float, liq: float, *, force_cost_bps=None) -> float:
    """Return round-trip cost in USD for one position.

    ``force_cost_bps`` is an escape hatch for unit tests that want to inject
    an artificially high cost without touching the calibration file.
    """
    if force_cost_bps is not None:
        return force_cost_bps * size_usd / 10_000.0
    cal = load_calibration()
    tp = cal.tiers[tier_for_symbol(symbol)]
    c = compute_trade_costs(
        entry_notional_usd=size_usd, exit_notional_usd=size_usd,
        entry_liquidity_usd_per_min=liq, exit_liquidity_usd_per_min=liq,
        tier_params=tp, model=cal.active_model, global_params=cal.global_,
    )
    return c["total_cost_usd"]


def score_positions(rows: list[dict], *, force_cost_bps=None) -> list[dict]:
    """Attach v3 cost to each closed-short row. Returns list of scored dicts."""
    scored = []
    for r in rows:
        _validate_row(r)
        cost_usd = _v3_cost_usd(
            r["symbol"], r["size_usd"], r["liquidity_per_min"],
            force_cost_bps=force_cost_bps,
        )
        cost_bps = cost_usd / r["size_usd"] * 10_000.0
        scored.append({**r, "v3_cost_usd": cost_usd, "v3_cost_bps": cost_bps})
    return scored


def assert_no_sign_inversion(scored: list[dict]) -> dict:
    """Raise if the v3 cost model inverts any per-symbol winner or violates the fee floor.

    Two checks are performed in order:

    1. **Precondition guard** — aborts with ``InsufficientDataError`` when the
       sample is too small to be meaningful (< ``EXPECTED_MIN`` rows).

    2. **Mandatory fee-floor tripwire** — raises ``BoundFalsifiedError`` if any
       scored position has a v3 cost < ``MANDATORY_LOWER_BOUND_BPS`` (= 2 ×
       published taker fee, 10.0 RT bps).  This is an *external* reference,
       independent of the model's own internal floor, so it cannot be silently
       lowered by a calibration edit.

    3. **No per-symbol sign inversion** — for each symbol whose aggregate gross
       P&L exceeds ``NOISE_BAND_USD`` (to filter statistical noise), raises
       ``BoundFalsifiedError`` if the sign of net P&L (gross minus v3 cost) does
       not match the sign of gross P&L.  Granularity is per-symbol SUM: this
       guarantees no per-symbol NET sign inversion, not that no individual trade
       inverted.

    Returns a summary dict ``{"n": int, "checked": [...], "skipped_noise": [...]}``
    listing symbols that were evaluated vs. skipped for noise.
    """
    n = len(scored)
    if n < EXPECTED_MIN:
        raise InsufficientDataError(
            f"falsification needs >={EXPECTED_MIN} closed shorts, got {n}"
        )

    # Secondary tripwire: model never sits below the external mandatory fee floor.
    for s in scored:
        if s["v3_cost_bps"] < MANDATORY_LOWER_BOUND_BPS:
            raise BoundFalsifiedError(
                f"{s['symbol']}: v3 cost {s['v3_cost_bps']:.2f}bps below mandatory "
                f"{MANDATORY_LOWER_BOUND_BPS}bps (fee mis-config)")

    # Primary test: no per-symbol sign inversion.
    by_symbol: dict[str, list] = {}
    for s in scored:
        by_symbol.setdefault(s["symbol"], []).append(s)

    checked, skipped_noise = [], []
    for symbol, ss in by_symbol.items():
        gross = sum(s["pnl_usd"] for s in ss)
        if abs(gross) <= NOISE_BAND_USD:
            skipped_noise.append(symbol)
            continue
        checked.append(symbol)
        net = sum(s["pnl_usd"] - s["v3_cost_usd"] for s in ss)
        if (gross > 0) != (net > 0):
            raise BoundFalsifiedError(
                f"{symbol}: v3 cost causes sign inversion (gross {gross:.2f} -> net {net:.2f})")
    return {"n": n, "checked": checked, "skipped_noise": skipped_noise}


def _liquidity_proxy_at(df1h, ts) -> float:
    """30d rolling per-minute USD liquidity proxy at-or-before ts, mirroring the
    backtest (backtest.py): (close*volume)/60, rolling(720, min_periods=120).mean().
    Returns NaN if df is empty or no bar at/<= ts has a valid rolling value.

    Robust to tz-state mismatch: get_cached_data returns a tz-NAIVE index while
    callers may pass a tz-AWARE ts (or vice versa); ts is normalized to the
    index's tz-state before comparison so neither shape raises TypeError."""
    import math
    import pandas as pd
    if df1h is None or len(df1h) == 0:
        return float("nan")
    ts = pd.Timestamp(ts)
    idx_tz = getattr(df1h.index, "tz", None)
    if idx_tz is None and ts.tzinfo is not None:
        ts = ts.tz_localize(None)
    elif idx_tz is not None and ts.tzinfo is None:
        ts = ts.tz_localize(idx_tz)
    usd_per_min = (df1h["close"] * df1h["volume"]) / 60.0
    proxy = usd_per_min.rolling(720, min_periods=120).mean()
    mask = proxy.index <= ts
    if not mask.any():
        return float("nan")
    val = proxy[mask].iloc[-1]
    return float(val) if val == val else float("nan")


def _load_closed_shorts_from_db(db_path: str) -> list[dict]:
    """Read-only load of CLOSED SHORT positions in the post-cutoff window
    (>= 2026-05-21, well past the holdout cutoff 2025-04-29). Case-insensitive
    on direction. NN#3: reads only signals.db (mode=ro), no holdout frames."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(
            "SELECT symbol, direction, pnl_usd, pnl_pct, size_usd, entry_ts, exit_ts "
            "FROM positions WHERE status='closed' AND UPPER(direction)='SHORT' "
            f"AND exit_ts >= '{_WINDOW_START}' ORDER BY exit_ts")
        return [
            {"symbol": r["symbol"], "direction": r["direction"],
             "pnl_usd": r["pnl_usd"], "pnl_pct": r["pnl_pct"],
             "size_usd": r["size_usd"], "entry_ts": r["entry_ts"], "exit_ts": r["exit_ts"]}
            for r in cur.fetchall()
        ]
    finally:
        con.close()


def main(signals_db: str = "signals.db"):
    """Run the falsification gate against the server signals.db (read-only).
    Resolves real per-minute liquidity from cached OHLCV; rows whose liquidity
    cannot be resolved are EXCLUDED and reported as 'unresolved' (NOT scored with
    NaN, which would hit the model fallback and spuriously falsify). MERGE
    PRECONDITION (spec §9): needs the server DB with >= EXPECTED_MIN closed shorts."""
    import pandas as pd  # noqa
    from backtest import get_cached_data

    rows = _load_closed_shorts_from_db(signals_db)
    data_start = datetime(2026, 1, 1, tzinfo=timezone.utc)  # post-cutoff; NN#3-clean
    ohlcv_cache: dict = {}
    scoreable, unresolved = [], []
    for r in rows:
        sym = r["symbol"]
        if sym not in ohlcv_cache:
            ohlcv_cache[sym] = get_cached_data(sym, "1h", start_date=data_start)
        ts = pd.Timestamp(r["entry_ts"])
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        liq = _liquidity_proxy_at(ohlcv_cache[sym], ts)
        if liq != liq or liq <= 0.0:  # NaN or non-positive -> cannot score
            unresolved.append(r)
            continue
        scoreable.append({**r, "liquidity_per_min": liq})

    print(f"loaded {len(rows)} closed shorts; scoreable={len(scoreable)} "
          f"unresolved(liquidity)={len(unresolved)}")
    for u in unresolved:
        print(f"  UNRESOLVED (no liquidity): {u['symbol']} entry={u['entry_ts']}")

    scored = score_positions(scoreable)
    summary = assert_no_sign_inversion(scored)  # raises BoundFalsifiedError / InsufficientDataError
    print(f"checked symbols: {summary['checked']}")
    print(f"skipped (noise band): {summary['skipped_noise']}")
    print("SCOPE CAVEAT: SHORT-only, ~$644 notional, NORMAL regime May-2026, low "
          "participation. Does NOT license long cost, crisis/wide-spread regimes, "
          "high-participation fills, any edge claim, or 'validated'.")
    print("PASS: v3 preserves all per-symbol price-winner signs (no sign inversion, "
          "fee floor intact).")
    return summary


if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else "signals.db")
