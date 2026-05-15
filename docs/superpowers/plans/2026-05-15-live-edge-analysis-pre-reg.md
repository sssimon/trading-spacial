# Pre-registration sub-spec — Direction A live-edge analysis

**Fecha:** 2026-05-15
**Status:** DRAFT — pre-registration ANTES de cualquier analysis execution.
**Autor:** Claude Opus 4.7 en colaboración con sssamuelll
**Tipo:** pre-registration sub-spec — fija metodología antes del exploratory data analysis sobre data de producción real
**Trigger:** Operator decision 2026-05-15 (post-epic-C closure + post-#338-hierarchy closure) to pursue Direction A: medir live edge desde data de producción real provista por papá's instance backup
**Cierre objetivo:** Phase D2 (analysis execution) emite verdict tiered: `EDGE_STRONG` / `EDGE_PARTIAL` / `EDGE_WEAK` / `NO_EDGE` / `INSUFFICIENT_DATA`
**Tracking issue:** TBD (no epic issue todavía; if results actionable, follow-up epic abierto post-D2)

---

## §1 · Contexto y alcance

### §1.1 — Trigger inmediato

Epic C (signal calibration, #350) cerrado 2026-05-15 como `AMBIGUOUS_TERMINAL`. #338 hierarchy también cerrada (decision tree fully resolved). Después de 4 architectural experiments todos terminando en negative/ambiguous (A.4 LRC re-tune, R1/R2/R3 alternative-strategy sweeps, #338 regime-allocation pivot, epic C signal calibration), surge la pregunta: **¿existe edge en alguna forma?**

Operator articula 3 marcos en el meta-discussion (2026-05-15):
1. **Marco 1**: edge existe en (signal+operator-judgment combo); backtest validation no captura discretion.
2. **Marco 2**: edge realmente no existe; el A.4 audit + #338 + R1/R2/R3 todos negative son evidencia fuerte.
3. **Marco 3**: hemos exhausted obvious strategy directions; necesitamos research más fundamental.

Operator eligió **Dirección A (Marco 1)**: medir live performance del operator antes de invertir en B (signal-family swap) o C (asset/market swap). Razón: data probably exists, low-cost experiment with high information value.

Reconnaissance reveló que el repo local NO tiene production data (`signals.db` 0 bytes, `Crypto_Trading_Tracker.xlsx` empty template). Operator provided backup zip `trading_backup_20260515_0900.zip` (55.6 MB compressed) containing papá's instance backup with **358 MB `signals.db`** + production logs.

### §1.2 — Data sources

Extracted to sandbox path `C:\Users\simon\Desktop\Papa\trading_backup_extracted\` (read-only access, no repo pollution):

| Source | Description |
|---|---|
| `signals.db` 358 MB | Production DB de papá. 6 tables, 95K+ rows. |
| `positions_summary.json` | Snapshot at 2026-05-15 08:59 |
| OHLCV cache `data/ohlcv.db` 610 MB (repo local) | 4.5M rows OHLCV for counterfactual reconstruction |

**signals.db tables relevantes:**

| Table | Rows | Use |
|---|---:|---|
| `positions` | 47 | Operator-approved trades. 43 closed, 2 cancelled, 2 open. |
| `scans` | 92,318 | All scanner snapshots. 3,157 with `señal=1`. Date range 2026-03-24 → 2026-05-15. |
| `signal_outcomes` | 2,499 | **Empty payload** — 0 completed, 0 with price_24h, 0 with max_runup_pct. **NOT USED** for analysis; counterfactual rebuilt from OHLCV. |
| `webhooks_sent` | 106 | Telegram webhook events |

### §1.3 — Iteración

Esta es la **primera iteración** del live-edge methodology sobre este dataset. Single-iteration discipline heredar de patrón #338/epic C: si verdict ≠ STRONG, operator §4.5-style decision required to break single-iteration discipline OR pivot to Direction B/C.

### §1.4 — Alcance del pre-reg

**Hace:**
- Lockea methodology completa (data sources, sub-question decomposition, threshold values, exclusion criteria, verdict tree) ANTES de cualquier query/compute sobre la data
- Documenta los 4 Q-LE locks operator-chosen via AskUserQuestion 2026-05-15
- Pre-registra Bayesian prior + decision hooks

**No hace:**
- No ejecuta ningún query sobre la data más allá del reconnaissance ya hecho (table schemas + row counts + dummy samples)
- No modifica `signals.db` extracted ni cualquier otro file
- No commits the production data extracted to repo (lives in sandbox path)
- No promueve cualquier feature a producción
- No re-litigates the closure de epic C o #338

---

## §2 · Methodology

### §2.1 — Data quality filtering (locked exclusion criteria)

**Positions table (43 closed):**

| Filter | n excluded | n remaining |
|---|---:|---:|
| `status = "closed"` | 4 (2 cancelled + 2 open) | 43 |
| `pnl_usd = 0 AND entry_price = exit_price` (data anomalies) | 10 | **33** |

**Real analysis subset: 33 positions with non-zero P&L** (operator's actual closed trades with realized return).

`signal_outcomes` table excluded entirely (0 completed entries despite 2,499 rows — instrumentation never populated). Counterfactual rebuilt from OHLCV directly.

Positions with `size_usd = NULL` (4 of 33): include in win-rate count, exclude from capital-basis calculations (no notional info).

### §2.2 — Sub-question decomposition

Three independent sub-questions, each with locked pass criterion:

| ID | Question | Locked threshold |
|---|---|---|
| **Q1** | Overall edge vs equal-weighted basket B&H | Strategy total_return_pct − basket_bh_return_pct ≥ **1 percentage point** |
| **Q2** | Operator filtering edge (MANUAL exits vs SL/TP auto exits) | avg(MANUAL P&L) > avg(SL_HIT P&L) AND bootstrap 95% CI on difference excludes 0 |
| **Q3** | Approved vs rejected signal counterfactual | avg(APPROVED hypothetical_24h_return) − avg(REJECTED hypothetical_24h_return) ≥ **Q-LE5 threshold (0.5 percentage point)** AND bootstrap 95% CI excludes 0 |

**Q-LE locks** (operator-resolved via AskUserQuestion 2026-05-15):

| ID | Question | Lock |
|---|---|---|
| Q-LE1 | B&H benchmark choice | **Equal-weighted basket of 16 traded symbols** (BTC, ETH, RUNE, ZEC, TRX, XAUT, UNI, TON, PENDLE, SUI, SOL, LINK, HBAR, DOGE, XLM, AVAX) |
| Q-LE2 | Q1 edge threshold | **≥ 1 percentage point** outperformance |
| Q-LE3 | Counterfactual matching window | **± 1 hour** around `entry_ts` |
| Q-LE4 | Verdict tree structure | **Tiered**: 3/3 STRONG, 2/3 PARTIAL (3 sub-combos), 1/3 WEAK, 0/3 NO_EDGE |
| Q-LE5 | Q3 counterfactual threshold | **≥ 0.5 percentage point** (APPROVED − REJECTED hypothetical 24h return). Rationale: Q3 has n ~3,157 vs Q1's effective n=1 → higher statistical power justifies lower minimum effect size (mitad of Q1's 1pp). Combined with bootstrap 95% CI excludes 0. |

### §2.3 — Q1 operationalization

**Strategy total return:**
```
strategy_pnl_usd = SUM(pnl_usd) FROM positions WHERE status='closed' AND data_quality_filtered
strategy_capital_basis_usd = SUM(size_usd) FROM positions WHERE status='closed' AND size_usd IS NOT NULL AND data_quality_filtered
strategy_return_pct = strategy_pnl_usd / strategy_capital_basis_usd × 100
```

**Equal-weighted basket B&H:**
```
For each symbol S in {16 traded symbols}:
    price_start = closest OHLCV close to 2026-03-30T00:00:00 UTC
    price_end   = closest OHLCV close to 2026-05-07T23:59:59 UTC
    bh_return_pct[S] = (price_end - price_start) / price_start × 100
basket_bh_return_pct = MEAN(bh_return_pct over 16 symbols)
```

**Q1 verdict:**
```
gap_pct = strategy_return_pct - basket_bh_return_pct
Q1_pass = gap_pct >= 1.0
```

### §2.4 — Q2 operationalization

```
For exit_reason in {MANUAL, SL_HIT, TP_HIT}:
    cells = positions WHERE status='closed' AND exit_reason=$reason AND data_quality_filtered
    avg_pnl[reason] = MEAN(pnl_usd over cells)
    n[reason] = COUNT(cells)

bootstrap_diff = BOOTSTRAP(n=10000) of (MEAN(MANUAL.pnl_usd) - MEAN(SL_HIT.pnl_usd))
ci_95 = (percentile(bootstrap_diff, 2.5), percentile(bootstrap_diff, 97.5))

Q2_pass = avg_pnl[MANUAL] > avg_pnl[SL_HIT] AND ci_95[0] > 0
```

Bootstrap usado dado small sample (n=32 MANUAL, n=9 SL_HIT) — parametric assumptions cuestionables.

### §2.5 — Q3 operationalization

**Counterfactual hypothetical return per scan:**
```
For each scan in {scans WHERE señal=1 AND ts BETWEEN '2026-03-24' AND '2026-05-07'}:
    signal_ts = scan.ts (UTC)
    signal_symbol = scan.symbol
    signal_price = scan.price
    
    target_ts = signal_ts + 24 hours
    target_price = OHLCV[signal_symbol].price closest to target_ts
    
    hypothetical_24h_return_pct = (target_price - signal_price) / signal_price × 100
    
    # If scan.estado contains 'SHORT', flip sign
    if 'SHORT' in scan.estado.upper():
        hypothetical_24h_return_pct = -hypothetical_24h_return_pct
```

**Position ↔ scan matching (per Q-LE3 lock):**
```
For each position in real_subset (n=33):
    candidate_scans = scans WHERE
        symbol = position.symbol
        AND señal = 1
        AND |scan.ts - position.entry_ts| <= 1 hour
    
    If candidate_scans empty:
        position.scan_match = None  # tagged as 'no_scan_match'
    Else:
        position.scan_match = candidate_scans ORDER BY |ts - entry_ts| ASC LIMIT 1
        scan_match.bucket = 'APPROVED'
        
All scans not matched to any position → scan.bucket = 'REJECTED'
```

**Q3 verdict:**
```
avg_approved = MEAN(hypothetical_24h_return_pct for scans WHERE bucket='APPROVED')
avg_rejected = MEAN(hypothetical_24h_return_pct for scans WHERE bucket='REJECTED')

bootstrap_diff = BOOTSTRAP(n=10000) of (avg_approved - avg_rejected)
ci_95 = (percentile(bootstrap_diff, 2.5), percentile(bootstrap_diff, 97.5))

# Q-LE5 lock: ≥ 0.5 percentage point minimum effect size
Q3_pass = (avg_approved - avg_rejected) >= 0.5 AND ci_95[0] > 0
```

### §2.6 — Counterfactual caveats

**Methodological:**
- Hypothetical return assumes operator could have traded AT signal_ts at signal_price. Real execution would have slippage + spread. v2 cost model (Almgren-Chriss sqrt-participation, PR #341) NOT applied to counterfactual — too computationally heavy for ~3K signals. Acknowledge as upper-bound on rejected returns; effect on Q3 verdict directional (rejected returns slightly inflated; if APPROVED > REJECTED gap is robust, more confident).
- Direction (LONG vs SHORT) inferred from `scan.estado` string ('SHORT' substring). Edge case: ambiguous cases → flag, not include.
- **Primary verdict uses 24h forward window** (established in §2.5 `hypothetical_24h_return_pct` definition). 1h, 4h, 72h returns computed only as informational sensitivity views; the primary Q3 verdict criterion is locked to 24h to prevent post-hoc window-choice cherry-picking. If sensitivity views diverge materially from 24h primary, flag in audit doc but do NOT override primary verdict.

**Data:**
- Some scans may have NULL price or be near OHLCV gaps. Exclude from counterfactual.
- Approximate matching (±1h) may misclassify edge cases. Report `no_scan_match` count separately for transparency.

---

## §3 · Verdict tree (tiered per Q-LE4 lock)

| Verdict | Condition | Action |
|---|---|---|
| **EDGE_STRONG** | Q1 ∧ Q2 ∧ Q3 all pass | Strong evidence of operator-filtered edge. Commit to operator-tooling investment (Direction A original framing). Open follow-up epic. |
| **EDGE_PARTIAL_RETURN_FILTER** | Q1 ∧ Q2 ∧ ¬Q3 | Strategy beats B&H + operator filtering edge confirmed, but counterfactual fails. Interpretation: operator's MANUAL closes may capture edge that signal-at-entry alone doesn't (exit-timing edge), OR matching window misclassified positions. Action: dig deeper into MANUAL exit timing distribution + sensitivity views on match window. |
| **EDGE_PARTIAL_RETURN_SELECTION** | Q1 ∧ Q3 ∧ ¬Q2 | Strategy beats B&H + operator selects winners (counterfactual pass), but MANUAL doesn't differ from SL_HIT. Interpretation: operator's edge is in entry selection, not exit timing. Letting SL fire is equivalent to manual closing. Action: focus follow-up on entry filtering signal (Direction A Phase D3 scope), de-prioritize exit-timing tooling. |
| **EDGE_PARTIAL_FILTER_SELECTION** | Q2 ∧ Q3 ∧ ¬Q1 | Operator filtering + selection edge confirmed, but absolute return below B&H. Interpretation: regime artifact most likely (B&H went up a lot during window, operator's risk-adjusted edge real but absolute lagged). Action: extend evaluation window OR use risk-adjusted metric (Sharpe-like) in follow-up before committing to operator-tooling investment. |
| **EDGE_WEAK** | Exactly 1 of 3 pass | Marginal evidence. Consider Direction B (signal-family swap) or C (asset swap) before more iterations of Direction A. |
| **NO_EDGE** | All 3 fail | Strong evidence against operator-filtered edge. Operator decides: ramp-down, switch to alternative directions, or pivot to entirely different research. |
| **INSUFFICIENT_DATA** | Data quality issues prevent ≥ 1 sub-question evaluation | Halt; document gaps. Likely require instrumentation + data collection going forward. |

Operator decision required for `EDGE_PARTIAL`, `EDGE_WEAK`, `NO_EDGE`, `INSUFFICIENT_DATA` (auto-advance only for `EDGE_STRONG`).

---

## §4 · Edge cases pre-registrados

### §4.1 — Position data anomalies

Already filtered (§2.1): 10 of 43 closed positions excluded as data-quality anomalies (entry_price = exit_price AND pnl_usd = 0). Sample anomaly: `id=9 TRXUSDT entry=$0.32 exit=$0.32 size=$300 pnl=$0`. Patterns observed:
- TRX positions 9, 10: both same-bar entry+exit
- RUNE positions 26, 27, 32: same-bar with NULL size_usd in some
- ETH 36, XLM 40, 41: same pattern

These are likely test entries OR positions opened/closed in API tests. Exclusion is conservative.

### §4.2 — Off-basket symbols

6 of 16 traded symbols outside curated 10 (ZEC, TRX, XAUT, TON, HBAR, SUI, LINK, SOL):
- Equal-weighted basket B&H (Q-LE1) **includes all 16**, so off-basket trades + their B&H contribution both in denominator
- Sub-analysis: report on-basket-only vs off-basket-only return separately (informational, not verdict-driving)

### §4.3 — Concurrent vs sequential positions (capital basis)

Sum of `size_usd` across 39 sized closed positions ≈ $11,700. This is **total notional deployed**, not peak capital. If positions overlap, real capital is smaller. Pre-registered handling:
- Compute max-concurrent-open count by walking entry_ts/exit_ts; report
- Use sum-of-sizes as primary denominator for return % (conservative — lower implied return rate)
- Report peak-concurrent denominator as sensitivity view

### §4.4 — Small sample (n=33 real trades)

Bootstrap CIs with n=10,000 iterations. Effect sizes emphasized over p-values. Pre-reg explicitly acknowledges low statistical power.

For Q2: n=32 MANUAL vs n=9 SL_HIT. Bootstrap diff CI will be wide. Pre-reg threshold `CI excludes 0` is conservative — may fail even with real effect under small n.

For Q3: n≈3,157 scans = much higher power. Bootstrap CI should be tight.

### §4.5 — Short period (5 weeks)

Single regime context (whatever Mar 30 - May 7 2026 was — likely depends on BTC trend). Cannot generalize beyond this window. Verdict explicitly scoped: "live edge in this window", not "operator always has edge".

### §4.6 — scan_id NULL on all 47 positions

All matching done via approximate (symbol + ts ± 1h per Q-LE3). Positions without a matching `señal=1` scan in ±1h tagged `no_scan_match` and excluded from Q3 APPROVED bucket. Report count for transparency.

Sensitivity sub-analysis: re-run Q3 with ±15min and ±4h match windows; report whether verdict robust. Primary verdict uses ±1h (locked).

---

## §5 · Deliverable structure (Phase D2 execution PR)

After operator approval of this pre-reg, **Phase D2 execution** (separate PR) produces:

```
data/retune/2026-05-15-live-edge-analysis/
├── q1_overall_edge.json          # strategy P&L + basket B&H + gap + Q1 verdict
├── q2_filtering_edge.json        # exit_reason breakdown + bootstrap CIs + Q2 verdict
├── q3_counterfactual.json        # APPROVED vs REJECTED hypothetical returns + bootstrap CIs + Q3 verdict
├── data_quality.json             # exclusion log, sample sizes, no_scan_match count
├── analysis_verdict.json         # final tiered verdict
├── analysis_manifest.json        # data sources, code commit, locks applied
└── analysis_derivation_audit.md  # methodology recap + per-question findings + Bayesian update + decision hook
```

Analysis script(s) lives in repo at `tools/live_edge_analysis.py` (NEW). NO modifications to `signals.db` extracted (read-only). NO data committed to repo from sandbox path (the extracted DB stays outside repo).

**Live-path safety:** all analysis is read-only on a backup snapshot. Production scanner unaffected (it's on papá's machine, not here).

---

## §6 · What this pre-reg does NOT cover

- **Phase D3 (operator-tooling investment)** if EDGE_STRONG verdict. Out of scope per Direction A original framing — that would be a follow-up epic.
- **Re-analysis on extended date range.** Single-iteration discipline applies. If operator wants more windows, separate pre-reg required.
- **Modifying production system in any way.** Strict read-only.
- **Statistical hypothesis testing** beyond bootstrap CIs (p-values, multiple-testing corrections, etc.). The 3 sub-questions are pre-registered with effect-size thresholds; no multiple-testing adjustment needed when verdict is tiered.
- **Sharing analysis results outside operator + auditor.** Personal trading data of papá.
- **Modeling operator decision process explicitly** (what features drive his MANUAL closes). That's a follow-up if EDGE_STRONG.
- **Cost model v2 (PR #341) application to counterfactual.** Acknowledged as upper-bound bias; out of scope to apply per-signal.
- **Iteration on this pre-reg itself.** Single-iteration discipline.

---

## §7 · Pre-registered decision branches (summary table)

| Branch point | Rule | Reference |
|---|---|---|
| Data exclusion | $0 P&L + entry=exit anomalies excluded; 33 of 43 closed positions remain | §2.1 |
| signal_outcomes table | NOT used (empty); counterfactual rebuilt from OHLCV | §1.2, §2.5 |
| Q1 benchmark | Equal-weighted basket of 16 traded symbols B&H over Mar 30 - May 7 | Q-LE1 lock |
| Q1 threshold | strategy_return_pct − basket_bh_return_pct ≥ 1 pp | Q-LE2 lock |
| Q2 comparison | avg(MANUAL P&L) vs avg(SL_HIT P&L) | §2.4 |
| Q2 threshold | avg(MANUAL) > avg(SL_HIT) AND bootstrap 95% CI excludes 0 | §2.4 |
| Q3 matching | ± 1 hour around entry_ts (symbol-matched) | Q-LE3 lock |
| Q3 counterfactual primary window | **24h forward return** from OHLCV (1h/4h/72h informational sensitivity only); direction inferred from estado string | §2.5, §2.6 |
| Q3 threshold | (avg(APPROVED) − avg(REJECTED)) ≥ 0.5 pp AND bootstrap 95% CI excludes 0 | Q-LE5 lock |
| Bootstrap iterations | 10,000 | §4.4 |
| Verdict tree | Tiered: 3/3 STRONG, 2/3 PARTIAL (3 sub-combos: RETURN_FILTER / RETURN_SELECTION / FILTER_SELECTION), 1/3 WEAK, 0/3 NO_EDGE | Q-LE4 lock |
| Auto-advance | Only on EDGE_STRONG; all others operator decision | §3 |
| Live-path safety | Read-only on backup; production untouched | §5 |
| Single-iteration discipline | First iteration on this dataset; no Phase D1.5 | §1.3, §6 |

---

## §8 · Pre-execution math sanity

### §8.1 — Capital basis estimate

- 39 sized closed positions, sum size_usd ≈ $11,700, median $300, range $100-$500
- 33 real-trade subset (after data quality filter): sum size_usd will be ≤ $11,700
- Strategy realized P&L (real subset only): TBD post-filter (was +$73.73 unfiltered 43, will be slightly different on 33)
- Approximate return rate: +$73 / $11K ≈ +0.66% over 5 weeks → ~6.6%/yr annualized IF non-overlapping AND extrapolated (which is unjustified)

### §8.2 — Basket B&H sanity

- 16 symbols × ~5.5 weeks. Reasonable range for crypto: ±10-20pp basket return
- If basket up 10% and strategy +0.66%, Q1 FAILS clean
- If basket down 5% and strategy +0.66%, Q1 PASSES (gap = +5.66 pp ≥ 1 pp)
- Prior: BTC was ~$80K-90K range in Mar-Apr 2026 with some volatility but no clear trend; unclear direction for basket. Coin-flip whether Q1 passes.

### §8.3 — Q2 sanity

- avg(MANUAL P&L) +$3.08 (n=32 unfiltered) → likely +$3-4 after data quality filter (removes most $0 cases)
- avg(SL_HIT P&L) -$4.10 (n=9)
- Difference ~$7. Bootstrap CI on small n=9 SL_HIT group will be wide.
- Prior: 60% Q2 passes (MANUAL > SL_HIT is robust; CI bound depends on variance)

### §8.4 — Q3 sanity

- 3,157 señal=1 scans → ~3,110 REJECTED (assuming ~47 APPROVED via matching)
- Hypothetical 24h returns for ~3K samples → tight CI
- If operator-filtering edge is genuine, APPROVED should have higher avg hypothetical return than REJECTED
- BUT: hypothetical return measures pre-execution potential, not realized. If operator picks signals that work intra-bar then close manually, signal-aligned 24h return may NOT reflect his realized edge.
- Prior: 45% Q3 passes (genuinely uncertain)

---

## §9 · Compute estimate (Phase D2)

| Stage | Estimate |
|---|---|
| Q1 implementation (SQL + OHLCV joins) | 1h |
| Q2 implementation (groupby + bootstrap) | 30 min |
| Q3 implementation (counterfactual reconstruction from OHLCV) | 2 h |
| Data quality + manifest emission | 30 min |
| Audit prose (§A.4 default-prose) | 1-1.5 h |
| **Total Phase D2** | **~5-6 h** wall-clock, single session feasible |

No compute-heavy sweeps. Most expensive part is counterfactual reconstruction (3K OHLCV lookups), which should complete in <1 min with proper indexing.

---

## §10 · Auditor prior on outcomes

Default §A.4 prose convention (CLAUDE.md auto-memory 2026-05-15). PyMC skill NOT invoked — this is an informational verdict, not a formal posterior checkpoint.

### Prior (pre-Phase-D2):

| Sub-question | Pass prior |
|---|---:|
| Q1 (overall edge vs basket B&H) | ~30% |
| Q2 (MANUAL > SL_HIT with CI) | ~60% |
| Q3 (APPROVED > REJECTED counterfactual) | ~45% |

**Joint priors (under assumption of partial independence):**
- P(EDGE_STRONG = 3/3): ~8-12%
- P(EDGE_PARTIAL = 2/3, any of 3 sub-combos): ~30-40%
  - P(EDGE_PARTIAL_RETURN_FILTER = Q1∧Q2∧¬Q3): ~10-15% (Q2 is strongest preliminary; Q3 most uncertain)
  - P(EDGE_PARTIAL_RETURN_SELECTION = Q1∧Q3∧¬Q2): ~8-12%
  - P(EDGE_PARTIAL_FILTER_SELECTION = Q2∧Q3∧¬Q1): ~10-15%
- P(EDGE_WEAK = 1/3): ~30%
- P(NO_EDGE = 0/3): ~15-25%
- P(INSUFFICIENT_DATA): ~5%

### Bayesian update plan (post-Phase-D2):

- **EDGE_STRONG**: P(live edge real) → ~70-80%; commit to Direction A Phase D3 (operator-tooling).
- **EDGE_PARTIAL_RETURN_FILTER** (Q1∧Q2∧¬Q3): P(exit-timing edge real, entry-selection unclear) → ~40-50%; follow-up on MANUAL exit timing distribution.
- **EDGE_PARTIAL_RETURN_SELECTION** (Q1∧Q3∧¬Q2): P(entry-selection edge real, exit-timing not edge) → ~40-50%; focus follow-up on entry filtering signal.
- **EDGE_PARTIAL_FILTER_SELECTION** (Q2∧Q3∧¬Q1): P(filtering+selection real but regime-masked) → ~30-45%; extend window or use risk-adjusted metric before Phase D3.
- **EDGE_WEAK**: P(live edge marginal) → ~15-25%; lean toward Direction B/C exploration.
- **NO_EDGE**: P(live edge absent) → ~75-85%; strong signal for ramp-down or fundamental research pivot.
- **INSUFFICIENT_DATA**: P preserved at prior; identify data gap remediation.

---

## §11 · Methodology limitations

1. **Single regime context.** 5-week window (Mar 30 - May 7 2026); cannot generalize beyond.
2. **Small position sample (n=33 real trades).** Bootstrap CI will be wide on Q2. Effect-size emphasis required.
3. **Counterfactual hypothetical returns ≠ realized.** Assumes operator could trade at signal_ts at signal_price. Real execution would have slippage + spread (acknowledged in §2.6; not corrected).
4. **No cost-model v2 application to counterfactual.** Upper-bound bias on REJECTED returns; effect direction on Q3 is favorable to APPROVED comparison.
5. **Direction inference from estado string.** Some scans may not parseable; flagged + excluded.
6. **±1h match window choice.** Sensitivity analysis on ±15min and ±4h to verify robustness; primary verdict uses ±1h.
7. **Capital basis: sum-of-sizes vs peak-concurrent.** Two views reported; primary uses sum-of-sizes (conservative).
8. **Off-basket symbols mixed into basket B&H.** Treats all 16 symbols equally regardless of curated-10 status; sub-analysis reports on-basket vs off-basket separately (informational).
9. **scan_id NULL forces approximate matching.** ±1h window may misclassify edge cases (operator decision latency > 1h).
10. **Single-iteration discipline.** If verdict ≠ STRONG, breaking discipline for re-analysis requires sub-spec + operator counter-signoff per established §4.5 pattern.
11. **PyMC skill NOT invoked.** §A.4 default-prose; if operator wants formal hierarchical Bayesian model on (position × exit_reason × symbol) — separate decision, on-demand.
12. **Production data is papá's personal record.** Analysis stays in repo (audit doc) but raw DB stays in sandbox path. Personal data sensitivity acknowledged.

---

## §12 · Historial

| Fecha | Cambio | Autor |
|---|---|---|
| 2026-05-15 | Pre-reg initial draft. Q-LE1..Q-LE4 locked via AskUserQuestion. Reconnaissance findings incorporated (data quality + signal_outcomes empty + scan_id NULL handling). Mirror del epic C pre-reg pattern, lighter scope (no sweep grid, single dataset analysis). | Claude Opus 4.7 + sssamuelll |
| 2026-05-15 | **Review-fix amendments** (PR #356 reviewer feedback, 3 FLEXIBLE items): (1) Q-LE5 lock added formalizing Q3 threshold ≥ 0.5 pp with statistical-power rationale; (2) §3 EDGE_PARTIAL enumerated into 3 sub-combos (RETURN_FILTER / RETURN_SELECTION / FILTER_SELECTION) with distinct actions; (3) §2.6 re-affirms primary verdict uses 24h window (sensitivity views informational only); §7 summary table + §10 Bayesian priors updated to reflect enumeration. | Claude Opus 4.7 + sssamuelll |
| TBD | Operator re-review + final approval | sssamuelll |
| TBD | Phase D1 pre-reg PR merged | sssamuelll |
| TBD | Phase D2 execution (separate PR after this pre-reg merge) | sssamuelll + Claude Opus 4.7 |
| TBD | Phase D2 verdict registration | sssamuelll + auditor |

Reservar líneas para iteración post-operator-re-review y verdict registration en Phase D2 closure.
