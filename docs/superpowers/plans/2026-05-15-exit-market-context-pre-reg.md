# Pre-registration (light) — Market context at MANUAL exit moment

**Fecha:** 2026-05-15
**Status:** DRAFT — pre-reg before compute. Light scope: lockea WHAT computamos, NO QUÉ counts as edge.
**Autor:** Claude Opus 4.7 en colaboración con sssamuelll
**Tipo:** descriptive feature-engineering EDA for rule discovery
**Trigger:** Post manual-exit EDA (PR #358 draft 2026-05-15). Operator asked: "ver que estaba pasando en el momento de mercado que el decide cerrar y desde allí ver si podemos sacar un elemento determinista que podamos ajustar para crear el take profit nuevo".

---

## §1 · Contexto y alcance

Direction A Phase D2 (PR #357) confirmó Q2 MANUAL > SL_HIT edge. Manual-exit EDA (PR #358) caracterizó hold time + capture rate + per-symbol patterns, pero NO identificó **WHAT TRIGGERS** the operator's exit decision.

Esta study: **feature engineering** — para cada MANUAL exit, capturar features del mercado al exit_ts. Buscar features que CLUSTER fuerte (= deterministic element). Si algún feature clusters, becomes candidate rule for automation TP.

**Output expectations**: hypothesis-generation, NOT validated rule. n=16 → emergent patterns identifiable descriptively but NOT statistically validated. Operator decides which (if any) candidate rule to lock as separate pre-reg + falsifiable test.

### §1.1 — Subset locked (same as PR #358)

16 MANUAL closes on curated 10 (post data-quality filter). Operator-amended Q-LE1 scope per Direction A 2026-05-15.

### §1.2 — Locks operator-resolved 2026-05-15

- **Feature scope**: Narrow (H1-H6 OHLCV-only, ~12 features). Skip scanner state + RSI/LRC reconstruction. Simpler implementation, focused signal.
- **Post-exit window**: 4 hours (4 bars at 1h granularity). Captures immediate continuation pattern without confound from longer regime change.

### §1.3 — Single-iteration discipline

NO iteration on this study. If patterns emerge, candidate rule → separate pre-reg + falsifiable test on the same n=16 sample (Phase ED2 if pursued). NO sweep over feature parameters, NO post-hoc threshold adjustment.

---

## §2 · Methodology

### §2.1 — Feature set (locked)

Six hypothesis groups, ~12 features total:

**H1 — Exit bar pattern** (3 features per position):
- `exit_bar_color`: green if close > open (LONG-adverse for SHORT, LONG-favorable for LONG); red if close < open
- `exit_bar_close_position`: where close sits in bar's range. 0.0 = at low, 1.0 = at high. `(close - low) / (high - low)`
- `exit_bar_range_atr_ratio`: bar range divided by trailing 14-bar ATR. Wide-range bars (>1.5×) suggest climax.

**H2 — Local price extremum** (2 features):
- `dist_from_5bar_high_pct`: for LONG, `(rolling_5bar_high - exit_price) / exit_price × 100`. Lower = closer to local high. For SHORT, `dist_from_5bar_low_pct`.
- `is_new_local_high_or_low`: bool. For LONG: did exit bar's high exceed prior 5 bars' highs? For SHORT: did exit bar's low go below prior 5 bars' lows?

**H3 — Momentum at exit** (2 features):
- `last_3bar_momentum_pct`: `(close_t - close_t-3) / close_t-3 × 100`. Direction-adjusted (favorable for LONG = positive, for SHORT = negative).
- `momentum_deceleration_flag`: bool. True if last bar's favorable move was smaller than prior bar's. Bar-over-bar deceleration signal.

**H4 — Volatility signal** (1 feature):
- `move_from_entry_atr_normalized`: `(exit_price - entry_price) / atr_entry`. Direction-adjusted. Measures move in ATR multiples (universal across symbols).

**H5 — Time-favorable interaction** (2 features):
- `hours_to_first_favorable_5pct`: time from entry to first time max_favorable crossed +5%. NaN if never reached.
- `time_since_max_favorable_hours`: time from max_favorable_peak to exit_ts. Larger = held through peak + pulled back.

**H6 — Post-exit hindsight** (2 features, 4h window):
- `post_exit_4h_favorable_pct`: max favorable price move in 4h post-exit. For LONG: `(max_high_4h - exit_price) / exit_price × 100`. For SHORT: `(exit_price - min_low_4h) / exit_price × 100`.
- `post_exit_4h_adverse_pct`: max adverse move in 4h post-exit. Direction-adjusted.

**Exit quality classification** (derived from H6):
- `exit_quality`: `"GOOD"` if `post_exit_4h_favorable_pct < 1%` (price didn't continue meaningfully favorable → operator caught the turn)
- `"PREMATURE"` if `post_exit_4h_favorable_pct >= 1%` (price continued favorable significantly → exit was premature)
- `"REVERSAL_CAUGHT"` if `post_exit_4h_favorable_pct < 1%` AND `post_exit_4h_adverse_pct >= 1%` (operator caught reversal — best case)

1% threshold is arbitrary anchor; locked pre-execution. Sensitivity at 0.5% / 2% reported as informational.

### §2.2 — Computation

For each MANUAL close:
1. Query 1h OHLCV bars for symbol over `[exit_ts - 14h, exit_ts + 4h]` (14h trailing + 4h forward)
2. Identify exit_bar = bar where `open_time <= exit_ts < open_time + 1h`
3. Compute features H1-H6 from bar slices
4. ATR(14) computed from 14 trailing bars: `mean(TR)` where `TR = max(high-low, |high-prev_close|, |low-prev_close|)`

### §2.3 — Clustering analysis

For each feature, compute:
- Mean / median / std / p25 / p75 (continuous features)
- Frequency table (categorical / binary features)
- Stratified by `is_winner` and `direction` (LONG vs SHORT)

**Clustering criterion (qualitative):**
- Binary: ≥80% of cells share same value → strong cluster
- Continuous: coefficient of variation (std/mean) < 0.5 → tight cluster
- Per-bin frequency: if a histogram bin contains ≥75% of cells → cluster

Strong-clustered features become candidate rules. Reported in findings with explicit clustering metrics.

### §2.4 — Caveats locked upfront

- n=16 prevents statistical validation. Output is hypothesis generation only.
- Single regime (bull Mar-May 2026)
- 1h granularity misses sub-hour features
- ATR(14) requires 14 prior 1h bars; positions with insufficient history (e.g., entry < 14h after first available OHLCV) get NaN
- Some positions may have NaN exit_quality if 4h post-exit data unavailable (window cuts off DB)
- MANUAL is heterogeneous (winner + loser motivations may differ)
- NO operator annotation of actual reasoning — pure data-driven inference

---

## §3 · Deliverable structure

```
data/retune/2026-05-15-manual-exit-market-context/
├── features.json           # 12 features × 16 positions
├── clustering_summary.json # per-feature stats + clustering observations
├── exit_quality.json       # GOOD/PREMATURE/REVERSAL_CAUGHT classification
├── manifest.json           # code commit + locks + subset
└── findings.md             # narrative: per-feature observations + candidate rules + caveats
```

`findings.md` structure:
- §1 Methodology recap
- §2 H1 bar pattern findings
- §3 H2 local extremum findings
- §4 H3 momentum findings
- §5 H4 volatility findings
- §6 H5 time-favorable findings
- §7 H6 + exit quality classification
- §8 Synthesis: which features cluster strongly? Candidate rule shapes
- §9 Caveats + limitations
- §10 Next steps if any rule is to be locked

---

## §4 · What this pre-reg does NOT cover

- Verdict tree (descriptive only)
- Statistical hypothesis testing
- Out-of-sample validation (n=16 + single regime; deferred to separate work)
- Implementation of any rule in production scanner
- Sweep over feature parameters
- Threshold optimization
- ML / supervised learning on features
- PyMC formal posterior
- Multi-tier candidate rule comparison

---

## §5 · Historial

| Fecha | Cambio | Autor |
|---|---|---|
| 2026-05-15 | Pre-reg light initial draft. Feature scope locked NARROW (H1-H6 OHLCV-only). Post-exit window locked 4h. 1% exit_quality threshold locked. | Claude Opus 4.7 + sssamuelll |
| TBD | Phase ED1 execution + findings publication | Claude Opus 4.7 + sssamuelll |
