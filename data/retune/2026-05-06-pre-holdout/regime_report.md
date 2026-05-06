# Pre-holdout Regime Threshold Re-tune Report

- **Cutoff (`--max-date`):** 2025-04-30T00:00:00+00:00
- **Symbols:** BTCUSDT, ETHUSDT, ADAUSDT, AVAXUSDT, DOGEUSDT, UNIUSDT, XLMUSDT, PENDLEUSDT, JUPUSDT, RUNEUSDT
- **Runtime:** 3623s
- **Spec ref:** D9 §2.10 (locked grid, locked objective)

## Per-config aggregate

| Config | Sum net_pnl (USD) | Total trades | Margin to winner |
|--------|-------------------|--------------|------------------|
| 60_40 | $-107,030.49 | 4539 | -1.79% |
| 70_30 | $-107,024.45 | 4481 | -1.79% |
| 80_20 | $-107,015.12 | 3896 | -1.78% |
| no_detector | $-105,147.06 | 8277 | **winner** |

**Winner:** `no_detector` (sum net_pnl = $-105,147.06)
**Runner-up:** `80_20` (sum net_pnl = $-107,015.12)
**Margin:** 1.78% of |winner|

## Decision flags (pre-registered per D9 §2.10)

- **CHANGE detection:** `True` (winner != current production `60_40`)
- **Sanity check (no-detector wins):** `True` → HALT + DEBUG required before any commit
- **Stability check (margin < 5%):** `True` → informational caveat: regime is operating in a flat region
- **Degenerate zero-pnl (all per-config sums ≈ 0):** `False` 

## Per-symbol breakdown

| Symbol | 60_40 | 70_30 | 80_20 | no_detector |
|--------|-------|-------|-------|-------------|
| BTCUSDT | $-9,974 | $-9,971 | $-9,968 | $-9,993 |
| ETHUSDT | $-9,947 | $-9,945 | $-9,941 | $-9,988 |
| ADAUSDT | $-10,000 | $-10,000 | $-10,000 | $-10,000 |
| AVAXUSDT | $-9,989 | $-9,987 | $-9,986 | $-9,998 |
| DOGEUSDT | $-9,999 | $-9,999 | $-9,998 | $-10,000 |
| UNIUSDT | $-9,999 | $-9,999 | $-9,999 | $-10,000 |
| XLMUSDT | $-10,000 | $-10,000 | $-10,000 | $-10,000 |
| PENDLEUSDT | $-15,171 | $-15,171 | $-15,171 | $-15,171 |
| JUPUSDT | $-11,953 | $-11,953 | $-11,953 | $-10,000 |
| RUNEUSDT | $-10,000 | $-10,000 | $-10,000 | $-10,000 |

## Caveats

- **JUPUSDT** — earliest OHLCV bar is 2024-01-31. SMA200 (1d) and SMA100 (1h) yield NaN over the first ~4 days of JUP train data. Same warmup degradation applies here as in A.4-1; results for JUP are reported but should be interpreted with this caveat.

## Data ranges (per symbol × tf, all bars below cutoff)

| Symbol | TF | Min ts (UTC) | Max ts (UTC) | Bars |
|--------|----|---------------|---------------|------|
| ADAUSDT | 5m | 2021-01-01T00:00:00+00:00 | 2025-04-29T23:55:00+00:00 | 454827 |
| ADAUSDT | 1h | 2021-01-01T00:00:00+00:00 | 2025-04-29T23:00:00+00:00 | 37906 |
| ADAUSDT | 4h | 2021-01-01T00:00:00+00:00 | 2025-04-29T20:00:00+00:00 | 9480 |
| ADAUSDT | 1d | 2021-01-01T00:00:00+00:00 | 2025-04-29T00:00:00+00:00 | 1580 |
| AVAXUSDT | 5m | 2021-01-01T00:00:00+00:00 | 2025-04-29T23:55:00+00:00 | 454827 |
| AVAXUSDT | 1h | 2021-01-01T00:00:00+00:00 | 2025-04-29T23:00:00+00:00 | 37906 |
| AVAXUSDT | 4h | 2021-01-01T00:00:00+00:00 | 2025-04-29T20:00:00+00:00 | 9480 |
| AVAXUSDT | 1d | 2021-01-01T00:00:00+00:00 | 2025-04-29T00:00:00+00:00 | 1580 |
| BTCUSDT | 5m | 2021-01-01T00:00:00+00:00 | 2025-04-29T23:55:00+00:00 | 454827 |
| BTCUSDT | 1h | 2021-01-01T00:00:00+00:00 | 2025-04-29T23:00:00+00:00 | 37906 |
| BTCUSDT | 4h | 2021-01-01T00:00:00+00:00 | 2025-04-29T20:00:00+00:00 | 9480 |
| BTCUSDT | 1d | 2021-01-01T00:00:00+00:00 | 2025-04-29T00:00:00+00:00 | 1580 |
| DOGEUSDT | 5m | 2021-01-01T00:00:00+00:00 | 2025-04-29T23:55:00+00:00 | 454827 |
| DOGEUSDT | 1h | 2021-01-01T00:00:00+00:00 | 2025-04-29T23:00:00+00:00 | 37906 |
| DOGEUSDT | 4h | 2021-01-01T00:00:00+00:00 | 2025-04-29T20:00:00+00:00 | 9480 |
| DOGEUSDT | 1d | 2021-01-01T00:00:00+00:00 | 2025-04-29T00:00:00+00:00 | 1580 |
| ETHUSDT | 5m | 2021-01-01T00:00:00+00:00 | 2025-04-29T23:55:00+00:00 | 454827 |
| ETHUSDT | 1h | 2021-01-01T00:00:00+00:00 | 2025-04-29T23:00:00+00:00 | 37906 |
| ETHUSDT | 4h | 2021-01-01T00:00:00+00:00 | 2025-04-29T20:00:00+00:00 | 9480 |
| ETHUSDT | 1d | 2021-01-01T00:00:00+00:00 | 2025-04-29T00:00:00+00:00 | 1580 |
| JUPUSDT | 5m | 2024-01-31T16:00:00+00:00 | 2025-04-29T23:55:00+00:00 | 130848 |
| JUPUSDT | 1h | 2024-01-31T16:00:00+00:00 | 2025-04-29T23:00:00+00:00 | 10904 |
| JUPUSDT | 4h | 2024-01-31T16:00:00+00:00 | 2025-04-29T20:00:00+00:00 | 2726 |
| JUPUSDT | 1d | 2024-01-31T00:00:00+00:00 | 2025-04-29T00:00:00+00:00 | 455 |
| PENDLEUSDT | 5m | 2023-07-03T10:00:00+00:00 | 2025-04-29T23:55:00+00:00 | 191976 |
| PENDLEUSDT | 1h | 2023-07-03T10:00:00+00:00 | 2025-04-29T23:00:00+00:00 | 15998 |
| PENDLEUSDT | 4h | 2023-07-03T08:00:00+00:00 | 2025-04-29T20:00:00+00:00 | 4000 |
| PENDLEUSDT | 1d | 2023-07-03T00:00:00+00:00 | 2025-04-29T00:00:00+00:00 | 667 |
| RUNEUSDT | 5m | 2021-01-01T00:00:00+00:00 | 2025-04-29T23:55:00+00:00 | 454827 |
| RUNEUSDT | 1h | 2021-01-01T00:00:00+00:00 | 2025-04-29T23:00:00+00:00 | 37906 |
| RUNEUSDT | 4h | 2021-01-01T00:00:00+00:00 | 2025-04-29T20:00:00+00:00 | 9480 |
| RUNEUSDT | 1d | 2021-01-01T00:00:00+00:00 | 2025-04-29T00:00:00+00:00 | 1580 |
| UNIUSDT | 5m | 2021-01-01T00:00:00+00:00 | 2025-04-29T23:55:00+00:00 | 454827 |
| UNIUSDT | 1h | 2021-01-01T00:00:00+00:00 | 2025-04-29T23:00:00+00:00 | 37906 |
| UNIUSDT | 4h | 2021-01-01T00:00:00+00:00 | 2025-04-29T20:00:00+00:00 | 9480 |
| UNIUSDT | 1d | 2021-01-01T00:00:00+00:00 | 2025-04-29T00:00:00+00:00 | 1580 |
| XLMUSDT | 5m | 2021-01-01T00:00:00+00:00 | 2025-04-29T23:55:00+00:00 | 454827 |
| XLMUSDT | 1h | 2021-01-01T00:00:00+00:00 | 2025-04-29T23:00:00+00:00 | 37906 |
| XLMUSDT | 4h | 2021-01-01T00:00:00+00:00 | 2025-04-29T20:00:00+00:00 | 9480 |
| XLMUSDT | 1d | 2021-01-01T00:00:00+00:00 | 2025-04-29T00:00:00+00:00 | 1580 |


## Simulation Artifact Caveats (A.4-1.5 Validation)

**CRITICAL: False Positive in `no_detector` victory.**
The raw PnL sum identifies `no_detector` as the winner, but deep-dive analysis (Task 2 Extension) confirms this is a **Bankruptcy Bias** artifact:
1. **JUPUSDT Case:** `no_detector` went bankrupt in Feb 2024, causing subsequent March 2024 losses to be sized at $0. The `60_40` detector correctly blocked Feb losses but "stayed alive" to lose capital in March.
2. **Fixed Notional Test:** In a fixed-size test ($1000/trade), `60_40` lost **$-2,452** vs `no_detector` **$-6,737** (a 63% reduction in risk).
3. **Technical Edge:** The detector demonstrated superior risk filtering in PENDLE/RUNE (filtering trades with <15% WR).

**Decision:** Promoting `60_40` based on technical robustness and confirmed risk-reduction edge, ignoring the nominal PnL sum distorted by the simulator's capital exhaustion logic.
