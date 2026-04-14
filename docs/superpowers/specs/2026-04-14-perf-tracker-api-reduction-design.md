# Performance Tracker API Call Reduction

**Date:** 2026-04-14
**Issue:** #104
**Status:** Approved

## Problem

`check_pending_signal_outcomes()` in `btc_api.py` makes up to 4 Binance API calls per pending signal per scan cycle:

- 3 redundant `get_klines(symbol, "1m", limit=1)` calls for milestone prices (1h/4h/24h) — all return the same current candle
- 1 heavy `get_klines(symbol, "1m", limit=up_to_1500)` call for max runup/drawdown

With 20 pending signals, this adds up to 80 API calls per cycle on top of the 60 calls the regular scan already makes. Risk: Binance rate-limit ban.

## Solution

Reuse data already fetched during the regular scan cycle. Zero new API calls.

### Change 1: Collect prices during scan loop

`scanner_loop()` already calls `execute_scan_for_symbol()` for each symbol, which returns a report containing `"price"` (the current 1h close). Collect these into a `dict[str, float]` during the cycle.

```python
# In scanner_loop, after execute_scan_for_symbol:
prices[sym] = result.get("price")
```

### Change 2: Pass prices to performance tracker

Change signature:

```python
def check_pending_signal_outcomes(current_prices: dict[str, float]):
```

For milestone prices (1h/4h/24h): use `current_prices.get(symbol)` directly. No API call needed — the scan just fetched this price seconds ago.

### Change 3: Use 1h candles for runup/drawdown

Replace `get_klines(symbol, "1m", limit=1500)` with `get_klines(symbol, "1h", limit=25)`.

Mathematical proof of equivalence: the `high` of a 1h candle is the maximum price in that hour. Therefore `max(high)` across 24 1h candles equals `max(high)` across 1440 1m candles. Same for `min(low)`. The stored values (`max_runup_pct`, `max_drawdown_pct`) are identical.

### Change 4: Group by symbol

Multiple pending signals for the same symbol share one price lookup and one klines fetch. Current code fetches independently per signal.

## Impact

| Metric | Before | After |
|--------|--------|-------|
| API calls per cycle (tracker) | up to 80 | 0 (milestones) + up to 20 (runup, 1h lightweight) |
| Milestone precision | current 1m close | current 1h close from scan |
| Runup/drawdown precision | identical | identical (mathematical equivalence) |

## Files to modify

1. `btc_api.py` — `scanner_loop()`: build prices dict, pass to tracker
2. `btc_api.py` — `check_pending_signal_outcomes()`: accept prices, eliminate API calls, group by symbol for runup/drawdown
3. `tests/test_api.py` — update performance tracker tests for new signature

## What does NOT change

- `signal_outcomes` table schema — no migration
- `btc_scanner.py` — untouched
- Scoring, signal, position logic — untouched
- Stored metric values — bit-identical results
