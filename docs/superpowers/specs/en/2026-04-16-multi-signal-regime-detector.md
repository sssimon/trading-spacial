# Multi-Signal Regime Detector

**Date:** 2026-04-16
**Status:** Implemented and live

---

## Overview

Composite market regime detector that automatically determines if the market is BULL, BEAR, or NEUTRAL by combining three independent signal sources. Runs once per day, cached 24h, 3 API calls total.

## Signals

| Signal | Weight | Source | Measures |
|--------|--------|--------|----------|
| Price Structure | 40% | Binance daily candles | Death Cross (SMA50/SMA200), price vs SMA200, 30d returns |
| Sentiment | 30% | alternative.me Fear & Greed API | Social media, surveys, volatility, momentum, BTC dominance |
| Market | 30% | Binance Futures funding rate | Whether futures traders are net long or short |

## Composite Score

```
Score = (Price × 0.4) + (Sentiment × 0.3) + (Funding × 0.3)

> 70 = BULL  → LONG only
< 30 = BEAR  → enable SHORT
30-70 = NEUTRAL → LONG only (conservative)
```

## Implementation

- `detect_regime()` — fetches all 3 signals, computes composite score
- `get_cached_regime()` — returns cached result, refreshes if older than 24h
- `_regime_cache` — global dict with TTL mechanism
- Only analyzes BTCUSDT (>90% crypto correlation)
- Backtest uses price-only component (can't call historical sentiment APIs)

## Live Reading (2026-04-16)

Score: 33.6 (NEUTRAL) — Death Cross active, Extreme Fear (23), funding neutral (49).
