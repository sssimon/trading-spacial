"""One-shot OHLCV cache warmer for the 10 curated symbols × 4 timeframes.

Surfaced during Phase 3 sesión 2 (epic #338) on 2026-05-14:
ETHUSDT/ADAUSDT/RUNEUSDT/etc had 30-60% gaps in their 5m cache, causing
`_process_lrc_archived_baseline_cell` to crash via the data layer's
`AllProvidersFailedError` when the chunked fetcher (data/_fetcher.py) hit
Binance transient errors AND the Bybit fallback didn't have the
historical 5m range.

This script is environmental hygiene — NOT a methodology change and NOT
a fix to the data layer. It calls `md.get_klines_range(...)` for every
(symbol, timeframe) the LRC archived baseline + regime-allocation sweep
need, retrying transient `AllProvidersFailedError` with exponential
backoff. The market_data cache layer auto-detects gaps and fills them
chunk-by-chunk; each retry resumes from the new (smaller) gap set after
prior chunks succeed and persist.

Run once before `python tools/regime_allocation_sweep.py --baselines-only`.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from typing import Final

# Bootstrap repo path for direct invocation.
import os
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from data import market_data as md
from data.providers.base import AllProvidersFailedError


# Curated 10 symbols (CLAUDE.md DEFAULT_SYMBOLS / pre-reg §3 + epic #135).
SYMBOLS: Final[tuple[str, ...]] = (
    "BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT",
    "UNIUSDT", "XLMUSDT", "PENDLEUSDT", "JUPUSDT", "RUNEUSDT",
)

# Order: small data first (1d) so early progress is visible, then 5m (most
# gaps, longest to warm) last.
TIMEFRAMES: Final[tuple[str, ...]] = ("1d", "4h", "1h", "5m")

# Window A (sim_start = 2022-04-01) - 14 months = 2021-02-01. Use a safe
# floor at 2021-01-01 to cover any incidental margin.
WARM_START: Final[datetime] = datetime(2021, 1, 1, tzinfo=timezone.utc)
# Warm up to now (cache is shared infra; the sweep tool slices to
# cutoff=2025-04-30 internally so post-cutoff data isn't consumed by the
# backtest, but a healthy cache to "now" benefits other callers too).
WARM_END: Final[datetime] = datetime.now(timezone.utc)


MAX_RETRIES: Final[int] = 6
BASE_BACKOFF_SEC: Final[float] = 3.0


def warm_one(symbol: str, timeframe: str) -> tuple[bool, int, str]:
    """Warm a single (symbol, timeframe) cache range.

    Returns (success, n_bars, note). On AllProvidersFailedError, retries
    with exponential backoff up to MAX_RETRIES. Each retry resumes from
    the new gap set since prior chunks have been persisted.
    """
    last_err: str = ""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            df = md.get_klines_range(symbol, timeframe, WARM_START, WARM_END)
            n_bars = len(df) if df is not None else 0
            note = f"OK on attempt {attempt}" if attempt > 1 else "OK"
            return True, n_bars, note
        except AllProvidersFailedError as exc:
            last_err = str(exc)
            wait = BASE_BACKOFF_SEC * (2 ** (attempt - 1))
            print(
                f"  [{symbol} {timeframe}] AllProvidersFailedError on "
                f"attempt {attempt}/{MAX_RETRIES}; waiting {wait:.1f}s",
                file=sys.stderr,
            )
            time.sleep(wait)
    return False, 0, f"EXHAUSTED after {MAX_RETRIES} attempts: {last_err}"


def main() -> int:
    print(
        f"OHLCV cache warmer — {len(SYMBOLS)} symbols × {len(TIMEFRAMES)} "
        f"timeframes, range [{WARM_START.date()}, {WARM_END.date()}]",
        file=sys.stderr,
    )

    t_start = time.time()
    results: list[tuple[str, str, bool, int, str, float]] = []
    n_ok = 0
    n_fail = 0

    for symbol in SYMBOLS:
        for tf in TIMEFRAMES:
            t0 = time.time()
            ok, n_bars, note = warm_one(symbol, tf)
            dt = time.time() - t0
            results.append((symbol, tf, ok, n_bars, note, dt))
            if ok:
                n_ok += 1
                print(
                    f"  [{symbol:12} {tf:>3}] {n_bars:>7} bars in "
                    f"{dt:6.1f}s — {note}",
                    file=sys.stderr,
                )
            else:
                n_fail += 1
                print(
                    f"  [{symbol:12} {tf:>3}] FAILED in {dt:6.1f}s — {note}",
                    file=sys.stderr,
                )

    t_total = time.time() - t_start
    print(
        f"\nDone in {t_total:.1f}s — {n_ok} ok, {n_fail} failed",
        file=sys.stderr,
    )

    if n_fail > 0:
        print("Failed (symbol, timeframe) pairs:", file=sys.stderr)
        for symbol, tf, ok, _, note, _ in results:
            if not ok:
                print(f"  {symbol} {tf}: {note}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
