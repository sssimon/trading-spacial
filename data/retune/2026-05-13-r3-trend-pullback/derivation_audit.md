# R3 — Trend-pullback sweep — Derivation Audit

**Date:** 2026-05-13
**Status:** Complete. **R3 verdict: FAIL (clean)** — primary criterion FAILS 3/3 sub-windows; mechanism engaged but profitability absent. §1.1 hard-lock → path (a) of issue #321 (stakeholder escalation to Simón).
**Pre-reg:** `docs/superpowers/plans/2026-05-13-r3-trend-pullback-pre-reg.md`
**Audit spec:** `docs/superpowers/specs/es/2026-05-11-strategy-structural-audit.md` (§6 R3, §A.5, §A.6)
**Branch:** `feat/r3-trend-pullback-execution-2026-05-13`
**Closes:** Phase 2 R3 (last defendible structural lever under current basket per audit §A.6)
**Triggers:** Issue #321 path (a) — escalate to Simón with R1+R2+R3 FAIL stack as overwhelming evidence; #271 user-invitation guardrail enforced definitively.

---

## §1 — Executive summary

R3 tested whether replacing the LRC mean-reversion entry signal with a momentum/trend-pullback frame (SMA50 > SMA200 + price retrace to SMA20 ± `pullback_distance` × ATR, regime-gated, with R1's SIGNAL_EXIT kept active at LRC midline = 50) produces edge over the LRC baseline. Sweep grid: 5 × 3 × 5 = 75 cells × 10 symbols × 3 sub-windows = 2,250 backtests + 30 baselines, parallelized 8 workers.

**Result:**

| Window | Primary criterion | Symbols engaged | Net-pnl > 0 |
|---|---|---:|---:|
| A (bear 2022) | **FAIL** | 8 of 10 in-data | 0 |
| B (recovery 2023) | **FAIL** | 5 of 8 in-data | 0 |
| C (recent 2025) | **FAIL** | 9 of 10 in-data | 0 |

- **§10.4 halt did NOT fire.** H1 (signal degenerate): only JUP/PENDLE excluded as pre-registered for window A (start dates outside window). H2 (TL horizon mismatch): TIME_LIMIT% range 2.27%–8.51% across all in-data symbols, well below the 50% threshold — uniform 36h TL is appropriate for the momentum frame.
- **Cells diverge wildly** across A/B/C for every evaluated symbol per §4.4. SUCCESS_CONDITIONAL (2/3 + stable) not reachable: every window primary failed.
- **Mechanism engaged** (8/5/9 of 10 symbols engaged across windows, signal_degenerate check fires=False) but profitability absent in every cell. Mirrors R1 FAIL framing: "mechanism engaged, profitability absent".

**§4.2 verdict: R3_FAIL (clean variant).** §4.6 asymmetric halt-guard preserves the negative verdict under partial-window evidence (not applicable here — all 3 windows completed).

**Joint posterior update (per pre-reg §A.4 + §12):**

| Component | Pre-R3 prior | Post-R3 posterior | Magnitude shift |
|---|---:|---:|---|
| P(R3 SUCCESS) | ~8-10% | **~0%** | Primary FAIL in 3/3 windows; cells diverge; mechanism non-actionable. |
| P(R3 SUCCESS_CONDITIONAL) | ~5-8% | **~0%** | Requires ≥2/3 primary ✓ + cells stable; neither obtains. |
| P(R3 INCONCLUSIVE) | ~15-20% | **~0%** | Requires ≥2/3 primary ✓; 3/3 failed. |
| P(R3 FAIL, signal-degenerate) | ~5-10% | **~0%** | Engagement is fine (8/5/9 of 10); signal not the bottleneck. |
| P(R3 FAIL, clean) | ~50-60% | **~100%** | Mechanism engaged, profitability absent across 3 regimes. |
| **Joint P(viable strategy under current basket)** | **~12-18%** | **~2-4%** | Cleanly resolved to FAIL. R1+R2+R3 stack converges on "no retail signal frame produces edge in this curated basket." |

**Forward direction:** §1.1 hard-lock activates automatically — path (a) of issue #321 (stakeholder escalation to Simón). NO H5 follow-up, NO further phase, NO retry with different signal candidate. §A.2 H5 caveat carried forward but H5 hard-locked NO per operator. See §8 for Simón communication draft outline.

---

## §2 — Methodology recap

Per pre-reg §2.1–§2.5 (no amendments during execution):

- **Variant:** trend-pullback (single, no iteration per audit §A.6 + operator-locked).
- **Entry signal:** LONG when `SMA50 > SMA200` AND `|close - SMA20| ≤ pullback_distance × ATR` AND regime allows LONG; SHORT symmetric with regime-gated BEAR. Per pre-reg §2.2.
- **Entry signal REPLACES LRC entry** (operator §9.1 confirmed): LRC zone check disabled when `cfg.trend_pullback_enabled = True`.
- **5m entry trigger LOCKED active** (operator §9.7 confirmed): bullish/bearish candle + RSI 5m direction match preserved.
- **Score uniform `SCORE_STANDARD = 2`** (operator §9.5 confirmed): 1.0× sizing eliminates score-related confounding within R3.
- **R1's SIGNAL_EXIT kept active** at fixed `lrc_exit_threshold = 50` (LRC midline), `dynamic_exit_enabled = True`. Applied to BOTH baseline and sweep cells so the comparison is apples-to-apples.
- **TL anchor: uniform 36h** (operator §9.2 (a) confirmed): RW-theory-justified conservative mid-range covering ~2.7 ATR target horizon per §10.1.
- **PoV gates: UNCHANGED** (decoupled per R2 §2.2 pattern; cost model v1 limitation, deferred to issue #325).
- **`atr_tp_mult`: UNCHANGED** at per-symbol current value (anti-confounder).
- **Sweep grid (§2.5):** `atr_sl_mult ∈ {0.5, 0.7, 1.0, 1.5, 2.5}` × `atr_be_mult ∈ {1.5, 2.0, 2.5}` × `pullback_distance ∈ {0.3, 0.4, 0.5, 0.6, 0.7}` = 75 cells per (symbol, sub-window).
- **Sub-windows (§3):** A 2022-04-01→2022-07-01, B 2023-04-01→2023-07-01, C 2025-01-30→2025-04-30. All BEFORE holdout_start (exclusive). Non-overlapping. Genuinely outside A.4-1 train window.
- **Cell selection rule (§4.1):** `argmax(net_pnl)` per (symbol, sub-window) subject to `n_trades ≥ 10`. Deterministic tuple tie-break `(net_pnl, -sl, -be, -pullback_distance)` mirrors hardened R1 patterns (#332/#334).

Implementation:
- `strategy/core.py:_evaluate_trend_pullback_direction` — pure helper, 19 unit tests (TDD-developed).
- `backtest.py` unchanged (cfg threads through via `evaluate_signal`).
- `tools/r3_trend_pullback_sweep.py` — sweep harness, applies hardened patterns by construction.
- `tools/r3_verdict.py` — verdict tool, includes §4.6 asymmetric halt-guard.

---

## §3 — Per-sub-window results

### §3.1 — Coverage (per pre-reg §3 `usable_bars ≥ 500`)

| Symbol | Window A | Window B | Window C |
|---|---:|---:|---:|
| BTCUSDT | 2184 ✓ | 2184 ✓ | 2160 ✓ |
| ETHUSDT | 2184 ✓ | 2184 ✓ | 2160 ✓ |
| ADAUSDT | 2184 ✓ | 2184 ✓ | 2160 ✓ |
| AVAXUSDT | 2184 ✓ | 2184 ✓ | 2160 ✓ |
| DOGEUSDT | 2184 ✓ | 2184 ✓ | 2160 ✓ |
| UNIUSDT | 2184 ✓ | 2184 ✓ | 2160 ✓ |
| XLMUSDT | 2184 ✓ | 2184 ✓ | 2160 ✓ |
| RUNEUSDT | 2184 ✓ | 2184 ✓ | 2160 ✓ |
| PENDLEUSDT | 0 (excluded) | 0 (excluded — note pre-reg implied PENDLE in B; actual start exactly at B's exclusive-end boundary) | 2160 ✓ |
| JUPUSDT | 0 (excluded) | 0 (excluded) | 2160 ✓ |

**Notes on PENDLE/B discrepancy:** pre-reg §3.1 stated "Window B 9/10 (excl. JUP)" anticipating PENDLE coverage. Actual: PENDLE first bar is 2023-07 boundary, exclusive of window B end (2023-07-01T00:00 UTC). PENDLE was therefore excluded from window B too. No methodological impact: PENDLE is currently-bankrupt per audit; its exclusion from B reduces window B's in-data set from anticipated 9 to actual 8.

### §3.2 — argmax-by-`net_pnl` cell per symbol per window (§4.1)

**Window A** (8 in-data symbols):

| Symbol | (sl, be, pull) | n_trades | net_pnl | avg_ppt | PF | TL% | SE% |
|---|---|---:|---:|---:|---:|---:|---:|
| BTCUSDT | (2.5, 2.5, 0.4) | 44 | -4,090.28 | -92.96 | 0.07 | 2.3 | 54.5 |
| ETHUSDT | (2.5, 2.0, 0.4) | 48 | -3,283.83 | -68.41 | 0.12 | 4.2 | 66.7 |
| ADAUSDT | (2.5, 2.5, 0.3) | 48 | -7,974.67 | -166.14 | 0.01 | 6.2 | 75.0 |
| AVAXUSDT | (2.5, 2.0, 0.3) | 44 | -7,174.10 | -163.05 | 0.00 | 2.3 | 65.9 |
| DOGEUSDT | (2.5, 2.5, 0.3) | 47 | -8,269.80 | -175.95 | 0.00 | 8.5 | 72.3 |
| UNIUSDT | (2.5, 2.0, 0.3) | 22 | -9,008.90 | -409.50 | 0.00 | 4.5 | 81.8 |
| XLMUSDT | (2.5, 1.5, 0.7) | 14 | -8,080.71 | -577.19 | 0.00 | 7.1 | 57.1 |
| RUNEUSDT | (2.5, 2.0, 0.3) | 27 | -7,490.16 | -277.41 | 0.00 | 3.7 | 66.7 |

**Window A primary criterion: FAIL** (0 of 8 with net_pnl > 0; avg PF on positive subset = 0.00; required ≥3 with net_pnl > 0 AND avg PF > 1.2).

**Window B** (5 in-data symbols engaged; XLM/UNI/RUNE/PENDLE/JUP have no eligible cell):

| Symbol | (sl, be, pull) | n_trades | net_pnl | avg_ppt | PF | TL% | SE% |
|---|---|---:|---:|---:|---:|---:|---:|
| BTCUSDT | (2.5, 2.5, 0.3) | 23 | -4,294.98 | -186.74 | 0.02 | 4.3 | 43.5 |
| ETHUSDT | (2.5, 2.5, 0.3) | 21 | -5,955.46 | -283.59 | 0.00 | 0.0 | 66.7 |
| ADAUSDT | (2.5, 1.5, 0.3) | 10 | -8,638.84 | -863.88 | 0.00 | 0.0 | 30.0 |
| AVAXUSDT | (2.5, 2.5, 0.7) | 10 | -9,007.57 | -900.76 | 0.00 | 0.0 | 70.0 |
| DOGEUSDT | (1.0, 1.5, 0.6) | 14 | -9,001.84 | -642.99 | 0.00 | 0.0 | 50.0 |

**Window B primary criterion: FAIL** (0 of 5 engaged with net_pnl > 0).

**Window C** (9 in-data symbols engaged; XLM has no eligible cell):

| Symbol | (sl, be, pull) | n_trades | net_pnl | avg_ppt | PF | TL% | SE% |
|---|---|---:|---:|---:|---:|---:|---:|
| BTCUSDT | (2.5, 2.5, 0.3) | 13 | -2,031.51 | -156.27 | 0.01 | 0.0 | 38.5 |
| ETHUSDT | (2.5, 2.0, 0.3) | 26 | -2,201.70 | -84.68 | 0.09 | 7.7 | 65.4 |
| ADAUSDT | (2.5, 1.5, 0.3) | 18 | -5,715.37 | -317.52 | 0.00 | 0.0 | 55.6 |
| AVAXUSDT | (2.5, 2.5, 0.3) | 26 | -8,075.06 | -310.58 | 0.00 | 0.0 | 69.2 |
| DOGEUSDT | (2.5, 2.5, 0.3) | 16 | -5,627.44 | -351.71 | 0.00 | 6.2 | 62.5 |
| UNIUSDT | (2.5, 2.5, 0.5) | 13 | -8,522.43 | -655.57 | 0.00 | 7.7 | 61.5 |
| PENDLEUSDT | (2.5, 1.5, 0.7) | 13 | -8,251.58 | -634.74 | 0.00 | 0.0 | 69.2 |
| JUPUSDT | (1.5, 1.5, 0.5) | 19 | -9,002.75 | -473.83 | 0.00 | 0.0 | 57.9 |
| RUNEUSDT | (1.0, 1.5, 0.3) | 16 | -9,000.57 | -562.54 | 0.00 | 0.0 | 37.5 |

**Window C primary criterion: FAIL** (0 of 9 engaged with net_pnl > 0).

**Aggregate:** 0 symbols with positive net_pnl across 3 windows × ~22 engaged-symbol-window pairs = 0 / 22.

### §3.3 — Aggregate exit-reason patterns

SIGNAL_EXIT (R1 mechanism, kept active at threshold=50) fires substantially across all windows — 38.5%–81.8% on argmax cells in window A, similar in B+C. The mechanism IS engaging. But the SIGNAL_EXIT triggers don't translate to profit:

- For LONG trades, SIGNAL_EXIT fires when LRC reaches 50 (midline). With trend-pullback entries (in confirmed uptrend), LRC starts somewhere in the channel and reaching 50 is plausible. But trend-pullback bets on price continuing the trend; SIGNAL_EXIT at midline locks early small gains OR confirms the bet failed (price didn't continue, reverted instead).
- The frame mismatch (LRC-anchored exit on momentum-anchored entry, acknowledged in pre-reg §5.6 + §13 #3) appears to bite: exits fire on noise rather than thesis-confirmation, capturing losses without giving trend continuation room to materialize.

This is consistent with the operator-locked design — the test was "trend-pullback + LRC SIGNAL_EXIT as inherited baseline, holding everything else fixed". The frame mismatch is a known limitation, and R3 INCONCLUSIVE under partial 2/3 + diverging cells might have prompted operator override for trailing-stop investigation, but R3 FAIL in 3/3 windows decisively closes that question.

---

## §4 — §10.4 halt-after-A check (did NOT fire)

Per pre-reg §10.4, H1 and H2 evaluated on sub-window A argmax cells:

**H1 (signal degenerate):** `≥6 of in-data symbols have NO argmax cell with n_trades ≥ 10`.
- Result: 2 of 8 in-data symbols had no eligible cell (JUP, PENDLE — both pre-registered exclusions per §3, not signal degeneracy).
- 2 < 6 threshold → **H1 does NOT fire.** Signal engaged in all 8 truly in-data symbols.

**H2 (TL horizon mismatch):** `≥6 of in-data symbols have TIME_LIMIT% > 50% on argmax cell`.
- Result: 0 of 8 in-data symbols had TIME_LIMIT% > 50%. Range observed: 2.27% (AVAX, BTC) to 8.51% (DOGE).
- 0 < 6 threshold → **H2 does NOT fire.** Uniform 36h TL is appropriate for the trend-pullback frame in this basket.

**Halt diagnostic:** `halt=False`, `halt_reason=[]`. B+C executed normally.

**Methodological note:** H1+H2 both failing to fire means the mechanism plausibility is confirmed — signal does engage, TL choice is appropriate, exits are reaching diverse exit reasons (SL/TP/SIGNAL_EXIT/TIME_LIMIT). This rules out "wrong horizon" or "signal too sparse" as the cause of FAIL. The cause is downstream: per-trade economics under trend-pullback are negative-EV in this basket.

---

## §5 — Cross-sub-window cell stability (§4.4)

Per-symbol argmax cell across windows:

| Symbol | A: (sl, be, pull) | B: (sl, be, pull) | C: (sl, be, pull) | Stability |
|---|---|---|---|---|
| BTCUSDT | (2.5, 2.5, 0.4) | (2.5, 2.5, 0.3) | (2.5, 2.5, 0.3) | diverges |
| ETHUSDT | (2.5, 2.0, 0.4) | (2.5, 2.5, 0.3) | (2.5, 2.0, 0.3) | diverges |
| ADAUSDT | (2.5, 2.5, 0.3) | (2.5, 1.5, 0.3) | (2.5, 1.5, 0.3) | diverges |
| AVAXUSDT | (2.5, 2.0, 0.3) | (2.5, 2.5, 0.7) | (2.5, 2.5, 0.3) | diverges |
| DOGEUSDT | (2.5, 2.5, 0.3) | (1.0, 1.5, 0.6) | (2.5, 2.5, 0.3) | diverges |
| UNIUSDT | (2.5, 2.0, 0.3) | (no eligible cell) | (2.5, 2.5, 0.5) | diverges |
| XLMUSDT | (2.5, 1.5, 0.7) | (no eligible cell) | (no eligible cell) | single window |
| RUNEUSDT | (2.5, 2.0, 0.3) | (no eligible cell) | (1.0, 1.5, 0.3) | diverges |
| PENDLEUSDT | (no eligible cell) | (no eligible cell) | (2.5, 1.5, 0.7) | single window |
| JUPUSDT | (no eligible cell) | (no eligible cell) | (1.5, 1.5, 0.5) | single window |

**0 of 10 symbols** show same argmax cell across the 3 windows. Cells diverge wildly for every multi-window symbol. SUCCESS_CONDITIONAL (2/3 + stable per §4.4) is structurally unreachable in this dataset.

`sl = 2.5` dominates argmax across windows (8 of 10 symbols pick `sl = 2.5` in window A; similar in B+C) — the widest stop is the "best" outcome by `argmax(net_pnl)`, meaning tighter stops produce systematically worse (more negative) results. This is consistent with the H4 / sizing inflation pathology audit-identified — wider SL means lower notional under R-multiple sizing, fewer dollar losses per trade, but doesn't change the underlying negative expectancy.

`pullback_distance = 0.3` dominates argmax in windows A (7/8) and B+C (most) — the tightest envelope. This rules out "loose envelope over-firing" as the failure mode (§5.2 pre-reg edge case): even with the narrowest, highest-conviction trend-pullback entries, the signal produces losses.

---

## §6 — Methodology framing

**Mechanism engaged, profitability absent** — the canonical R3 FAIL (clean) framing per pre-reg §4.2 row 4 + R1 §4 mirror.

Three reformulated post-R3 hypotheses about the underlying cause (consistent with audit §A.7/§A.8/§A.2):

1. **H1 (signal expectancy) reaffirmed at the meta level.** R1 tested exit logic, found edge absent. R3 tested entry signal frame, found edge absent. Both alternative levers fail. Combined evidence: NO RETAIL SIGNAL FRAME has edge in this curated basket. H1 applies to the signal+strategy stack as a whole, not just LRC.

2. **H8 (cost model amplification) persistence.** R3 didn't touch cost model v1 (linear). Issue #325 remains deferred. While H8 isn't the proximate cause of R3 FAIL (engagement is fine), it imposes a slippage tax floor that any signal frame must beat. Combined with negative signal expectancy, R3 was always racing against a headwind.

3. **H5 (basket curation contamination) carried as caveat per audit §A.2.** The 10 curated symbols were selected under the pre-#223 simulator (phantom-profit bug). The "basket" itself may be artifact rather than genuine signal-symbol fit. R3 FAIL is conditional on this basket; H5 escalation HARD-LOCKED NO per kickoff §1.1 operator constraint, so the question of "is the basket the problem, not the signal" remains formally open but operationally closed.

Per §A.2 framing for the operator communication (§8 below): "R3 FAIL means 'NO retail signal frame produces edge on this curated basket under the post-fix simulator'. It does NOT mean 'no strategy can ever work in crypto majors' — the basket may be the contamination, not the signal frame. But operator-locked NO H5 escalation closes that investigation path within Epic A scope."

---

## §7 — Bayesian update + posterior (per pre-reg §A.4 + §12)

| Outcome | Prior (pre-reg §12) | Posterior (post-execution) | Magnitude shift |
|---|---:|---:|---|
| R3 SUCCESS (3/3) | ~8-10% | ~0% | -10pp; 0 of 22 engaged-symbol-window pairs positive |
| R3 SUCCESS_CONDITIONAL (2/3 stable) | ~5-8% | ~0% | -8pp; primary failed 3/3 + cells diverge |
| R3 INCONCLUSIVE | ~15-20% | ~0% | -20pp; primary failed all windows |
| R3 FAIL (signal degenerate) | ~5-10% | ~0% | -10pp; engagement 8/5/9 of in-data, H1 false |
| R3 FAIL (clean) | ~50-60% | **~100%** | +45pp; clean realization of pre-execution most-likely scenario |

**Joint P(viable strategy under current basket): ~12-18% (pre-R3) → ~2-4% (post-R3).**

Auditor reasoning (3 sentences): R3 cleanly failed in the most-likely pre-execution scenario — mechanism engaged sufficiently across 8/5/9 of 10 in-data symbols per window with TIME_LIMIT% well below the §10.4 H2 threshold, ruling out signal-degenerate or wrong-horizon failure modes. Every cell across 2,250 backtests produces negative `net_pnl`; cells diverge wildly across the 3 sub-windows for every multi-window symbol, ruling out SUCCESS_CONDITIONAL. Combined with R1 FAIL (clean) and R2 FAIL (math-deterministic), the R1+R2+R3 stack converges on a single conclusion: the current basket does not support a profitable strategy under any retail signal frame tested within the structural-fix-driven post-#223 simulator.

**Per pre-reg §A.4 trigger:** ~2-4% is below any defendible re-investigation threshold. §1.1 hard-lock activates → path (a) of issue #321 (escalate to Simón). NO H5 follow-up per operator-locked constraint.

---

## §8 — Operator decision hooks → Path (a) of issue #321

Per pre-reg §1.1 hard-lock + §4.5 (R3 FAIL branch): R3 FAIL → path (a) of issue #321 **automatically**, no operator decision required.

### §8.1 — Simón communication draft outline

Per kickoff Phase 4 spec, R3 FAIL deliverables include "initial draft communication outline for Simón (per audit §A.2 framing)". Suggested structure:

**Subject:** Trading strategy validation — Epic A finding (R1+R2+R3 stack converged on FAIL)

**Body:**

> **TL;DR:** después de ejecutar tres alternativas estructurales independientes (R1 dynamic exit, R2 gates re-derivation, R3 trend-pullback entry signal) bajo el simulador post-#223, ninguna produce edge sobre el basket curado actual. La estrategia, tal como está parametrizada hoy, no muestra ventaja demostrable. Recomendación: pausar invitaciones de usuarios per guardrail #271 mientras evaluás los siguientes pasos.
>
> **Qué probamos:**
> 1. R2 (gates re-derivation, PR #327): re-derivamos `time_limit_hours` desde teoría ATR-based en lugar de la calibración contaminada de #281. Resultado matemáticamente determinado: 6 de 8 símbolos con problemas ya estaban en el anchor teórico (~5h). FAIL strong; H7 (gates over-restrict) retractada.
> 2. R1 (dynamic exit, PR #329): reemplazamos el TP estático con un exit signal-reversal anclado al LRC midline. Mecanismo engagement 17.6% (52% cell coverage), pero 0 de 8 símbolos producen P&L positivo en ningún cell. FAIL clean; "mecanismo engaged, profitability absent."
> 3. R3 (trend-pullback entry, PR #336): reemplazamos LRC mean-reversion entry con SMA50/200 trend confirmation + retrace a SMA20 ± 0.5 ATR. Sweep 2,250 backtests. Engagement 8/5/9 de 10 símbolos por ventana, TIME_LIMIT% bajo 9% (uniform 36h TL apropiada para el frame). Pero 0 de ~22 pares (símbolo, ventana) con P&L positivo. Celdas divergen entre ventanas. FAIL clean.
>
> **Interpretación:**
> - Joint posterior P(viable strategy bajo este basket) cayó de ~12-18% pre-R3 a ~2-4% post-R3. Below any defendible re-investigation threshold.
> - El stack R1+R2+R3 converge sobre una sola lectura: **ningún signal frame retail produce edge en este basket curado bajo el simulador post-#223.**
> - El "basket curado" mismo puede ser parte del problema — fue seleccionado bajo el simulador pre-#223 con el bug de phantom-profit (audit §A.2 H5 caveat). La pregunta "¿es el basket o la estrategia?" queda formalmente abierta pero operacionalmente cerrada per hard-lock NO H5.
>
> **Lo que NO probamos:**
> - H5 (basket re-validation bajo simulador post-fix con universo expandido). Hard-locked NO por nuestra decisión operacional — Epic A's scope cerrado con R3 FAIL.
> - Cost model v2 (sqrt-participation Almgren-Chriss). Deferred per issue #325. R3 inherits the linear v1 limitation (H8 confirmed in audit).
> - Estrategias no-retail (market-making, arbitraje cross-exchange, multi-leg derivatives). Fuera del scope original del proyecto.
>
> **Próximas opciones (a tu decisión):**
> 1. **Aceptar finding y pausar Epic A** — honra guardrail #271 (no invitar usuarios). Cerrar #321 con path (a) confirmado. Default si no hay decisión explícita en X semanas.
> 2. **Re-abrir H5 epic** — basket re-validation bajo universo expandido + simulador post-fix. Requiere revertir hard-lock + nueva pre-registración. ~2-4 weeks de trabajo metodológico.
> 3. **Pivot a non-retail signal frames** — investigar arbitraje / market-making / cross-exchange. Cambio de scope significativo; nuevo Epic.
>
> Referencias: pre-reg `docs/superpowers/plans/2026-05-13-r3-trend-pullback-pre-reg.md`, audit spec `docs/superpowers/specs/es/2026-05-11-strategy-structural-audit.md`, R1+R2 derivation_audits.

(This is a draft outline. Operator may revise tone, structure, technical depth as appropriate for Simón's communication preferences.)

### §8.2 — What is automatic vs operator-decided

| Action | Decision authority | Status |
|---|---|---|
| Classify R3 verdict as FAIL (clean) | Automatic per §4.2 row 4 | ✓ verdict.json written |
| Drop joint P(viable) to ~2-4% | Automatic per §A.4 trigger | ✓ documented §7 |
| Path (a) of #321 escalation activation | Automatic per §1.1 hard-lock | Operator confirms by escalating |
| Issue #271 user-invitation guardrail enforcement | Automatic per CLAUDE.md | Enforced |
| H5 escalation | HARD-LOCKED NO per §1.1 | Confirmed NO |
| Operator overrides hard-lock (re-open H5 etc.) | Operator-only, requires explicit override + new pre-reg | Pending |
| Send communication to Simón | Operator | Pending |

### §8.3 — Out of scope

- Operator decision on path (b) or (c) of #321 (operator-only).
- Sending the actual communication to Simón (operator-only).
- Re-opening Epic A scope (requires explicit override of §1.1 hard-lock).
- Running R3 with different signal candidate (single-alternative discipline per audit §A.6 + §1.1 hard-lock prevents this).
- Touching holdout (issue #322 hard block — R3 FAIL does NOT unblock holdout).

---

## §9 — Methodology limitations carried forward

Per pre-reg §13 + audit §A.7/§A.8 + R1 §13 + R2 §6, R3 inherits these caveats:

1. **H1 (signal expectancy gap) confirmed at meta level.** R1+R2+R3 stack: no retail signal frame produces edge in this curated basket under the post-fix simulator.

2. **H8 (cost model v1) untouched.** Trend-pullback entries pay same per-trade cost as LRC entries. Issue #325 unchanged. Note: H8 isn't the proximate cause of R3 FAIL (engagement is fine; mechanism engaged sufficiently), but it imposes a slippage tax floor that any signal frame must beat.

3. **SIGNAL_EXIT frame mismatch (LRC exit on momentum entry).** Per pre-reg §5.6 + §13 #3: R1's mean-reversion-anchored exit applied to trend-pullback's momentum-anchored entry. Operator-locked. SIGNAL_EXIT% range 38.5%–81.8% on argmax cells (substantial firing). Frame mismatch contributes to early-exit pathology — trend-pullback bets on continuation but SIGNAL_EXIT closes when LRC reaches midline, locking small gains or confirming the bet failed. Whether trailing-stop or trend-aware exit would change R3 outcome is an open methodological question outside R3 scope (single-alternative discipline + §1.1 hard-lock).

4. **TL anchor uniform 36h** per operator §9.2 (a) confirmation. RW-theory-justified. Per-symbol empirical derivation (option c) deferred — would not have changed R3 verdict per H2 false (TL is not the bottleneck).

5. **Basket curated under contaminated simulator (audit §A.2 H5 caveat).** Selection by epic #135 used pre-#223 simulator. R3 FAIL is conditional on this basket. H5 escalation HARD-LOCKED NO per operator §1.1. Caveat carried forward in §6 + §8 framing for Simón communication.

6. **Per-symbol bankruptcy halt (#313) reduces noise.** Some symbols bankrupt before generating sufficient evidence (XLM/UNI in B; XLM in C; PENDLE/JUP in A/B per pre-reg exclusions). 5-10 trade thresholds visible in the data. This is structural — does NOT manufacture positive expectancy where none exists.

7. **Score uniformity for trend-pullback during R3.** Per §2.2 + §9.5: all trades got `SCORE = 2` → 1.0× sizing. R3 FAIL doesn't validate "trend-pullback at variable scoring would also fail" — only "trend-pullback at standard sizing fails". This is a known scope limitation per pre-reg.

8. **Regime detector + 5m entry trigger unchanged.** Per §2.2 + §9.7. R3 doesn't test if regime-aware long-only or different 5m trigger would change outcome. Out of scope.

9. **PENDLE B-window coverage discrepancy.** Pre-reg §3.1 anticipated 9/10 in B, actual is 8/10 (PENDLE starts exactly at B's exclusive-end boundary). No methodological impact; PENDLE is currently-bankrupt and would not have changed verdict in B even if included.

---

## §10 — Pre-registered exclusion list verification

| Symbol | Window A (pre-reg) | Window A (actual) | Window B (pre-reg) | Window B (actual) | Window C (pre-reg) | Window C (actual) |
|---|---|---|---|---|---|---|
| JUPUSDT | excluded (0 bars; starts 2024-01-31) | ✓ excluded | excluded | ✓ excluded | included | ✓ included (2160 bars) |
| PENDLEUSDT | excluded (starts 2023-07) | ✓ excluded | included (anticipated) | **excluded actual** (boundary edge) | included | ✓ included (2160 bars) |
| All others | included | ✓ included (2184 bars each) | included | ✓ included (2184 bars each) | included | ✓ included (2160 bars each) |

Window A coverage matches pre-reg exclusions exactly. Window B has a minor discrepancy (PENDLE = 0 bars vs anticipated coverage), documented in §3.1 / §9 #9.

---

## §11 — Outputs

`data/retune/2026-05-13-r3-trend-pullback/`:

| File | Status | Content |
|---|---|---|
| `derivation_audit.md` | committed | This document |
| `manifest.json` | committed | Reproducibility metadata + harness configuration + halt flag |
| `sweep_results_A.json` | committed | 750 cells × per-cell metrics for window A |
| `sweep_results_B.json` | committed | 750 cells × per-cell metrics for window B |
| `sweep_results_C.json` | committed | 750 cells × per-cell metrics for window C |
| `baseline_pre_trend_pullback.json` | committed | 30 cells (10 sym × 3 sub-win) LRC entry baseline (Δ comparison reference) |
| `coverage.json` | committed | Per-(symbol, sub-window) usable_bars |
| `signal_diagnostics.json` | committed | Per-symbol signal-firing count in window A |
| `halt_after_a_diagnostic.json` | committed | Full per-symbol H1/H2 verification |
| `verdict.json` | committed | Formal §4.2 verdict + §4.6 halt-guard classification |
| ~~`halt_after_a.txt`~~ | NOT generated | Halt did NOT fire — `.txt` is conditional |
| `smoke_test.json` | committed | Single-cell smoke test result (from harness validation) |

Reproducibility: `python tools/r3_trend_pullback_sweep.py` reproduces all output deterministically from cached OHLCV (commit recorded in manifest).

---

## §12 — History

| Date | Change | Author |
|---|---|---|
| 2026-05-13 | R3 pre-reg locked + merged (#333) | sssamuelll + Claude Opus 4.7 |
| 2026-05-13 | Code: trend-pullback signal + SMA50/200 indicators committed (4430cbc); harness + verdict tools committed (89a6666, ac2d284) | Claude Opus 4.7 |
| 2026-05-13 | Sweep executed: baselines 30 cells + window A 750 cells (~14 min wall); halt did NOT fire; window B 750 cells; window C 750 cells | Claude Opus 4.7 |
| 2026-05-13 | Verdict tool ran: R3_FAIL (clean) — 0/22 engaged-symbol-window pairs positive, cells diverge, mechanism engaged | Claude Opus 4.7 |
| 2026-05-13 | derivation_audit + Bayesian update + Simón communication draft outline landed; §1.1 hard-lock → path (a) of #321 escalation activated | Claude Opus 4.7 |
