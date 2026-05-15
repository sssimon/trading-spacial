# Pre-registration (light) — Manual exit EDA on papá's prod data

**Fecha:** 2026-05-15
**Status:** DRAFT — pre-registration before any compute. Light scope (no verdict tree, no threshold locks).
**Autor:** Claude Opus 4.7 en colaboración con sssamuelll
**Tipo:** descriptive EDA pre-reg — lockea WHAT computamos, NO QUÉ counts as edge
**Trigger:** Direction A Phase D2 verdict EDGE_WEAK 2026-05-15 (PR #357). Q2 confirmó que MANUAL closes son el único edge medible; operator request: "estudiar la estadística de cómo papá cerró manualmente, en qué se enfocó".
**Cierre objetivo:** descriptive findings narrative documenting empirical MANUAL exit patterns. NO verdict pass/fail.

---

## §1 · Contexto y alcance

### §1.1 — Trigger

Direction A Phase D2 (PR #357 draft 2026-05-15) emitió verdict `EDGE_WEAK`:
- Q1 (overall edge vs basket) FAIL (strategy +0.77% vs basket +22%, gap -21pp)
- **Q2 (MANUAL vs SL_HIT) PASS** (avg diff +$10/trade, CI [+3.81, +15.34] excludes 0)
- Q3 (entry selection counterfactual) FAIL

Q2 confirma exit-timing edge real pero NO entry selection edge. Antes de invertir en re-design del TP mechanism (Path 1 mentioned en audit §8), operator solicita estudio descriptivo: **¿qué patrón empírico está siguiendo papá cuando cierra manualmente?**

Esto NO es hypothesis test — es **descriptive characterization**. Output informa decisions sobre eventual automation (TP reinvention, exit rule design, per-symbol customization) sin pre-locked pass/fail criteria.

### §1.2 — Data sources

| Source | Detail |
|---|---|
| `signals.db` (sandbox path) | papá's positions table — 16 MANUAL closes on curated 10 post-quality-filter |
| `data/ohlcv.db` | OHLCV cache 1h granularity for intra-position price reconstruction |

Subset locked (per operator decision 2026-05-15): **curated 10 only, 16 MANUAL closes**. Consistent con Q-LE1 amendment from Direction A. Off-curated MANUAL closes excluded (no OHLCV cache for those symbols).

### §1.3 — Iteración + scope discipline

First descriptive iteration on this dataset. Single-iteration discipline applies for any downstream hypothesis tests informed by this EDA, but the EDA itself is open-ended characterization (no verdict tree to lock).

**Strict scope discipline:**
- Compute pre-registered metrics ONLY (locked in §3)
- Report sub-sample sizes per analysis where stratification is used
- Avoid claims with n < 3 in sub-groups
- NO post-hoc "let me also look at X" additions — if interesting follow-up surfaces, document as future work, not added inline

---

## §2 · Methodology

### §2.1 — Subset definition

Apply same data-quality filter as Direction A Phase D2:
- `status = 'closed'`
- `symbol IN CURATED_10`
- NOT (`pnl_usd = 0 AND entry_price = exit_price`)  (excludes 7 zero-P&L anomalies)
- `exit_reason = 'MANUAL'`

Expected n = 16 (per Direction A reconnaissance).

### §2.2 — Intra-bar reconstruction (for D3 max excursion)

For each position in subset:
- Query `ohlcv.db` for `timeframe='1h'`, `symbol=position.symbol`, `open_time` in `[entry_ts, exit_ts]`
- Compute max favorable and max adverse excursion based on direction:
  - LONG: max_favorable = `max(high)` across bars; max_adverse = `min(low)`
  - SHORT: max_favorable = `min(low)`; max_adverse = `max(high)`

Granularity caveat: 1h misses sub-hour spikes. For positions held < 1h, single-bar reconstruction. For positions held days, 1h is adequate. 5m granularity available but adds 60× query cost; defer to follow-up if 1h findings ambiguous.

### §2.3 — Locked metrics per dimension

**D1 — Hold time distribution:**
- `hold_hours = (exit_ts - entry_ts).total_seconds() / 3600`
- Statistics: median, mean, p25, p75, min, max
- Stratified: winner subset (pnl_usd > 0) vs loser subset (pnl_usd ≤ 0)

**D2 — Exit price vs planned SL/TP distance traveled:**
For winners (pnl_usd > 0):
- `tp_distance = abs(tp_price - entry_price)` (skip if tp_price NULL)
- `realized_distance = abs(exit_price - entry_price)`
- `pct_of_tp_captured = realized_distance / tp_distance × 100`

For losers (pnl_usd ≤ 0):
- `sl_distance = abs(entry_price - sl_price)` (skip if sl_price NULL)
- `realized_loss_distance = abs(entry_price - exit_price)`
- `pct_of_sl_traveled = realized_loss_distance / sl_distance × 100`

Statistics per group: median, mean, p25, p75. Report n excluded due to NULL tp/sl.

**D3 — Max favorable/adverse excursion (intra-position):**

For each position:
- `max_favorable_pct` = max price move in favorable direction during position life
- `max_adverse_pct` = max price move in adverse direction
- `realized_pct = float(position.pnl_pct)` (already signed)
- `capture_rate_pct = realized_pct / max_favorable_pct × 100` (% of max favorable that was captured at exit)
  - capture_rate = 100% → exited AT the peak (optimal)
  - capture_rate < 100% → premature exit (left money on table)
  - capture_rate > 100% → impossible by construction (realized cannot exceed max favorable)
  - capture_rate negative → exited at loss while max_favorable was positive (worst case)
- `mae_to_realized_ratio = max_adverse_pct / abs(realized_pct)` for losers

Statistics: median, p25, p75, n with positive max_favorable, n with capture_rate negative.

**D4 — Per-symbol patterns:**
For each symbol with n ≥ 2 MANUAL closes (curated subset):
- Group D1, D2, D3 metrics by symbol
- Report median per group + n
- Avoid claims for symbols with n < 2

### §2.4 — NOT computed (excluded from this study)

- D5 time-of-day patterns (out of scope)
- D6 reaction to bar patterns / candle types (out of scope)
- Statistical tests on group differences (no hypothesis testing in EDA scope)
- Counterfactual analysis ("what if operator held longer / shorter")
- Multi-tier TP simulation (separate future work if EDA findings warrant)

---

## §3 · Deliverable structure

```
data/retune/2026-05-15-manual-exit-eda/
├── d1_hold_time.json           # per-position hold time + group statistics
├── d2_sl_tp_distance.json      # per-position TP/SL distance traveled + group stats
├── d3_excursion.json           # per-position max favorable/adverse + capture rate
├── d4_per_symbol.json          # symbol-grouped D1/D2/D3 summary
├── eda_manifest.json           # code commit + data sources + locked metric list
└── findings.md                 # descriptive narrative across all 4 dimensions
```

`findings.md` structure:
- §1 Methodology recap (1-2 paragraphs)
- §2 D1 hold time findings
- §3 D2 TP/SL distance findings
- §4 D3 max excursion findings
- §5 D4 per-symbol findings
- §6 Synthesis: "the empirical MANUAL exit pattern looks like ..."
- §7 Caveats + limitations
- §8 Actionable hooks: what these findings suggest for downstream automation (if any)

---

## §4 · What this pre-reg does NOT cover

- Verdict tree (no pass/fail — EDA only)
- Threshold locks (no Q-LE-style minimum effect sizes)
- Decision branches (operator decides based on findings, not pre-locked)
- Implementation of any TP/SL change in production
- Modifications to scanner or webhook behavior
- Re-analysis on different time periods (single-iteration discipline)
- Hypothesis testing on group differences
- Optimization sweeps over hypothetical exit rules
- Multi-tier TP simulation
- Cost model v2 application (irrelevant for descriptive EDA)
- PyMC formal posterior (default-prose convention)

---

## §5 · Methodology limitations (acknowledged upfront)

1. **Small sample n=16.** Statistical power for per-symbol breakdown (D4) limited. Symbols with n=1 reported descriptively only.
2. **1h granularity for intra-bar reconstruction.** Misses sub-hour spikes. For positions held > 4h, adequate. For 1-4h holds, may underestimate excursion.
3. **OHLCV provider artifacts.** OHLCV cache reflects whatever provider data was cached at time of fetch. Real-time tick data could show different intra-bar paths.
4. **Curated 10 only.** 16 off-curated MANUAL closes (LINK, SOL, TON, TRX, XAUT, ZEC) excluded for OHLCV coverage reason. Findings may not generalize to off-curated.
5. **MANUAL exit_reason includes both winning and losing MANUAL closes.** Stratified by win/loss but underlying decision process may be heterogeneous (different reasons for closing in winners vs losers).
6. **No operator interview / annotation.** Pure data-driven characterization. The reasons papá actually had in mind when closing are unknown; we observe only the empirical pattern.
7. **Single regime context.** Mar 30 - May 7 2026, bull window. Findings may not generalize to bear or sideways regimes.
8. **No follow-up automation locked in.** This study informs but does NOT pre-commit to any TP reinvention or rule change.

---

## §6 · Historial

| Fecha | Cambio | Autor |
|---|---|---|
| 2026-05-15 | Pre-reg light initial draft. Subset locked to curated 10 / 16 MANUAL closes per operator 2026-05-15. 4 dimensions (D1+D2+D3+D4) locked. NO verdict tree (descriptive EDA). | Claude Opus 4.7 + sssamuelll |
| TBD | Phase execution + findings publication (separate or same PR) | sssamuelll + Claude Opus 4.7 |
