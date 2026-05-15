# Phase D2 derivation audit — Direction A live-edge analysis

**Fecha:** 2026-05-15
**Phase D2 execution PR:** TBD
**Verdict:** `EDGE_WEAK` (Q1 FAIL ∧ Q2 PASS ∧ Q3 FAIL; n_pass = 1 of 3)
**Pre-reg ref:** `docs/superpowers/plans/2026-05-15-live-edge-analysis-pre-reg.md`
**Code commit:** TBD (post-commit)
**Halt fired:** N/A (no halt mechanism in single-dataset analysis)

---

## §1 · Methodology recap

Three pre-registered sub-questions executed over papá's production data backup (`signals.db` 358 MB, sandbox path outside repo). Locks Q-LE1..Q-LE5 from AskUserQuestion 2026-05-15 + runtime amendment to Q-LE1 (curated 10 instead of 16 traded symbols, see §2.1).

**Data subset (post-quality filter):**
- 47 total positions → 43 closed → 27 on curated 10 → **20 real positions** after excluding 7 zero-P&L-entry-equals-exit anomalies on curated subset
- Per-symbol: BTC=6, ETH=5, RUNE=4, UNI=2, AVAX=1, PENDLE=1, XLM=1, DOGE=0
- Exit reason mix: MANUAL=16, SL_HIT=4, TP_HIT=0 (no take-profits hit on curated subset)

Sub-questions:
- **Q1** strategy return vs equal-weighted basket B&H over Mar 30 - May 7
- **Q2** MANUAL vs SL_HIT exit P&L bootstrap CI
- **Q3** APPROVED vs REJECTED signal counterfactual (24h primary + 1h/4h/72h sensitivity)

---

## §2 · Q-LE1 runtime amendment (operator decision)

Original Q-LE1 lock (pre-reg §2.2): "Equal-weighted basket of 16 traded symbols (BTC, ETH, RUNE, ZEC, TRX, XAUT, UNI, TON, PENDLE, SUI, SOL, LINK, HBAR, DOGE, XLM, AVAX)".

**Data gap surfaced during Phase D2 reconnaissance:** OHLCV.db has 8 of the 16 traded symbols. Missing: ZEC, TRX, XAUT, TON, SUI, SOL, LINK, HBAR — all off-curated-basket per epic #135 ("13 removed tokens not profitable with this strategy regardless of parameters" per CLAUDE.md).

**Operator amendment 2026-05-15:** "antes teníamos 20 y curamos a 10, trabajemos con las 10 que tenemos actualmente". Basket restricted to curated 10 (CLAUDE.md `DEFAULT_SYMBOLS`). All 10 curated symbols have full OHLCV coverage in window.

Effect on analysis:
- **Q1 strategy scope**: positions on curated 10 only (20 of 33 real-quality-filtered positions; 13 off-curated positions excluded)
- **Q3 universe**: scans on curated 10 only (1,422 signal scans in window vs 3,157 across all 36 scanner-watched symbols)
- **Basket B&H denominator**: 10 symbols equal-weighted (vs 16 originally locked)

Methodologically defensible per epic #135 audit (the 8 missing symbols are not part of strategy scope; including them in basket would dilute with non-strategy-aligned signals).

Documented as runtime amendment in `data_quality.json::amendment_2026_05_15` field; pre-reg PR #356 NOT amended retrospectively (single-iteration discipline: pre-reg is locked at merge time; runtime data constraints recorded in audit).

---

## §3 · Q1 results — Overall edge vs basket B&H (FAIL)

| Metric | Value |
|---|---:|
| Strategy P&L (USD) | **+$56** |
| Strategy capital basis (USD, sum of size_usd) | $5,948 |
| Strategy return | **+0.77%** |
| Basket B&H return (equal-weighted curated 10) | **+22.04%** |
| **Gap (strategy − basket)** | **−21.26 pp** |
| Q-LE2 threshold | +1.0 pp |
| **Q1 verdict** | **FAIL** |

Per-symbol basket B&H over window (Mar 30 → May 7, ~5.5 weeks):
- BTC: +20.4% ($66,280 → $79,832)
- ETH: +14.5% ($1,997 → $2,287)
- RUNE: +42.3%
- ADA: not traded but in basket → +X%
- AVAX, DOGE, UNI, XLM, PENDLE, JUP all +X% individually
- Equal-weighted basket: +22.04%

**Interpretation:** strong bull regime during window. Strategy essentially flat (+0.77%) vs basket nearly +22%. The strategy massively under-performed buy-and-hold in absolute terms.

This is consistent with operator's actual trading behavior (47 positions total, 20 real on curated, n=20 over 5 weeks suggests average ~$300 position with limited capital deployed at any given time). The strategy is doing low-frequency, low-exposure trading vs basket B&H assuming full deployment.

A regime-artifact interpretation is partially valid: in a strong bull, ANY strategy that doesn't fully deploy capital long under-performs B&H. But the magnitude (-21pp) is too large to dismiss as artifact — even risk-adjusted, the deployment + selection didn't add value vs passive holding.

---

## §4 · Q2 results — Operator filtering edge (PASS)

| Metric | Value |
|---|---:|
| MANUAL avg P&L | **+$4.81** |
| MANUAL sum P&L | +$76.88 |
| MANUAL n | 16 |
| MANUAL std (sample) | $12.33 |
| SL_HIT avg P&L | **−$5.24** |
| SL_HIT sum P&L | −$20.94 |
| SL_HIT n | 4 |
| SL_HIT std (sample) | $1.01 |
| TP_HIT count | 0 (no take-profits hit on curated subset) |
| Bootstrap diff (MANUAL − SL_HIT) | **+$10.04** |
| Bootstrap 95% CI | **[+$3.81, +$15.34]** |
| **CI excludes zero?** | **YES** |
| **Q2 verdict** | **PASS** |

**Interpretation:** the strongest finding of the analysis. Bootstrap 95% CI [+$3.81, +$15.34] cleanly excludes zero with substantial buffer. The mean difference of +$10/trade is large relative to typical trade sizes ($100-$500).

Operator's MANUAL exit decisions add ~$10 per trade on average vs letting the stop-loss fire. This is consistent across the 16 MANUAL trades (CI is reasonably tight despite n=16).

Caveats:
- n=4 SL_HIT is small. The SL_HIT distribution itself may have wider true variance than the sample std=$1.01 suggests. Bootstrap CI assumes the empirical distribution is representative.
- This finding is window-specific. In a different regime (e.g., 2022 bear), SL_HIT might fire on different symbols with different magnitudes.
- This is **exit-timing edge** specifically, not entry-selection or overall return — see §5 and §6 for the broader picture.

---

## §5 · Q3 results — Approved vs rejected counterfactual (FAIL)

| Forward window | APPROVED mean | n_A | REJECTED mean | n_R | Diff (A−R) | CI 95% low | CI 95% high | CI excludes 0? |
|---|---:|---:|---:|---:|---:|---:|---:|:---:|
| **24h (PRIMARY)** | +0.41% | 13 | +0.53% | 1409 | **−0.12 pp** | -1.46 | +1.28 | NO |
| 1h | −0.13% | 13 | +0.20% | 1409 | −0.33 pp | -0.93 | +0.20 | NO |
| 4h | −0.01% | 13 | +0.17% | 1409 | −0.18 pp | -0.86 | +0.41 | NO |
| 72h | +0.52% | 13 | +2.08% | 1409 | −1.55 pp | -3.79 | +0.93 | NO |

Q-LE5 primary threshold: APPROVED − REJECTED ≥ +0.5 pp AND CI excludes 0. **Both criteria fail** at the 24h primary window.

**Striking finding: APPROVED < REJECTED at every forward window tested.** The direction is consistently negative across 1h/4h/24h/72h, though no individual CI excludes zero. Operator's entry selection is NOT differentiated from random selection from the scanner signal universe; if anything, slightly worse than random (though within noise).

**No_scan_match count: 7 of 20 positions** had no scanner signal within ±1h on the same symbol around position entry. This means operator opened those trades **without a corresponding scanner signal** — manual asset selection independent of scanner output. These 7 positions are excluded from APPROVED (no matching signal to "approve").

If the 7 no_scan_match positions are dominated by off-scanner-coverage symbols, that's expected. But the 7 may also include on-curated positions where operator opened independent of scanner — would need per-position inspection (out of scope for this audit; potentially follow-up).

Sensitivity across forward windows: results robust. All 4 windows (1h/4h/24h/72h) show APPROVED slightly worse than REJECTED, none CI-significant. Direction stability suggests not noise artifact at primary window choice.

---

## §6 · Verdict + interpretation

**EDGE_WEAK** (1 of 3 sub-questions pass).

| Sub-Q | Result | Diagnosis |
|---|---|---|
| Q1 | FAIL | Strong bull regime; strategy +0.77% vs basket +22% absolute gap (regime artifact + low deployment). |
| Q2 | PASS | **Filtering edge confirmed and robust.** MANUAL exits beat SL_HIT by ~$10/trade with tight CI. |
| Q3 | FAIL | Entry selection NOT differentiated from random; APPROVED < REJECTED at all 4 forward windows tested. 7/20 positions have no scanner-signal correspondence. |

**Composite interpretation:** the operator's confirmed edge is **exit timing**, NOT entry selection or overall return. The signal-generator + operator-filtering combo does not produce absolute outperformance vs B&H, and the operator's signal-selection criteria don't differentiate from random.

This is **partial support** for Marco 1 of the operator's 2026-05-15 meta-discussion ("edge may exist in signal+operator combo"), but only in a narrow exit-timing sense. It does NOT support the broader "operator's discretion captures edge that backtest misses" framing — exit-timing edge doesn't translate to absolute return advantage in this window.

Marco 2 ("edge realmente no existe") is partially supported by Q1+Q3 failures. The strategy is structurally unable to capture meaningful absolute return in a bull regime.

Marco 3 ("research más fundamental") is the framework that EDGE_WEAK leans toward per pre-reg §3 + §10.

---

## §7 · Bayesian update (§A.4 default-prose convention)

PyMC skill NOT invoked — this is informational verdict per CLAUDE.md auto-memory convention.

**Prior (pre-Phase-D2):**

| Outcome | Prior P |
|---|---:|
| EDGE_STRONG | ~8-12% |
| EDGE_PARTIAL (any sub-combo) | ~30-40% |
| EDGE_WEAK | ~30% |
| NO_EDGE | ~15-25% |
| INSUFFICIENT_DATA | ~5% |

**Posterior (observed):**

| Outcome | Posterior P | Δ vs prior |
|---|---:|---|
| EDGE_STRONG | 0% | -8/12 pp (falsified Q1, Q3) |
| EDGE_PARTIAL (any sub-combo) | 0% | -30/40 pp (need 2/3 pass; only 1 pass observed) |
| **EDGE_WEAK** | **100%** | **+70/85 pp (materialized)** |
| NO_EDGE | 0% | -15/25 pp (Q2 prevented this) |

**Magnitude shift on key sub-question priors:**

| Sub-Q | Prior pass P | Posterior pass | Direction |
|---|---:|---:|---|
| Q1 (overall edge vs basket B&H) | ~30% | 0% (fail strongly, -21pp gap) | regime-dominant + low deployment |
| Q2 (MANUAL > SL_HIT bootstrap) | ~60% | 100% (pass with CI [+3.81, +15.34]) | filtering edge robust |
| Q3 (APPROVED > REJECTED counterfactual) | ~45% | 0% (fail; APPROVED < REJECTED at all 4 forward windows) | selection not informative |

**Composite Bayesian update:**

- **P(live edge real, broadly defined) drops from prior ~50-70% → posterior ~15-25%.** The only confirmed edge is narrow (exit timing); it doesn't translate to absolute return outperformance.
- **P(exit-timing edge real) increases from ~60% → ~85-90%** (Q2 with tight CI).
- **P(entry-selection edge real) drops from ~45% → ~10-20%** (Q3 fail with consistent negative direction across 4 windows).
- **P(signal-generator+operator combo captures edge missed by backtest) drops from ~50% → ~15-25%.** The data doesn't support this framing in the way Marco 1 hypothesized.

---

## §8 · Decision hook — operator §4.5 self-policing

Per pre-reg §3 EDGE_WEAK row: "Marginal evidence. Consider Direction B (signal-family swap) or C (asset swap) before more iterations of Direction A."

Per pre-reg §10 Bayesian update plan: "EDGE_WEAK: P(live edge marginal) → ~15-25%; lean toward Direction B/C exploration."

### Options for operator §4.5 decision

**(a) Archive Direction A as `EDGE_WEAK_TERMINAL`.** Accept the verdict: confirmed exit-timing edge but not actionable for absolute return advantage; the signal generator + selection combo does not produce material edge. Pivot to Direction B or C, OR close trading-research efforts entirely.

**(b) Pursue narrow exit-timing tooling (acknowledge limited scope).** Invest in operator-tooling that amplifies MANUAL exit decisions (better SL adjustment UI, exit signal alerts, etc.) since Q2 shows this is the genuine edge. Acknowledge it doesn't address Q1 absolute under-performance — the strategy may still under-perform B&H even with better exit tooling.

**(c) Direction B (signal-family swap).** Try fundamentally different signal generation (ML-based features, on-chain data, microstructure, mean-reversion). Acknowledge: 4 architectural experiments (A.4 + R1/R2/R3 + #338 + epic C) already failed; this would be the 5th attempt. Cost-of-being-wrong: high (weeks of work). Benefit if right: meaningful absolute return.

**(d) Direction C (asset/market swap).** Smaller-cap alts where inefficiencies persist, options, different asset class. Higher infra cost, may surface inefficiencies that BTC/USDT lacks at current liquidity.

**(e) Ramp-down systematic research, keep paper-trading.** Accept that systematic edge isn't readily available in this signal+basket+window framework; continue running scanner + manual approval for operator's exit-timing benefit, but stop investing in research towards finding scalable edge.

### Auditor recommendation (NOT operator decision)

**(a) or (e) are the highest-EV options given the cumulative evidence:**

- 5 architectural experiments all negative/ambiguous OR weak
- Strong bull regime missed entirely (Q1 -21pp)
- Entry selection NOT informative (Q3 consistently negative)
- Only narrow exit-timing edge (Q2) genuine

(b) is defensible but narrow — invests in real edge but doesn't change the fundamental "we can't beat B&H" picture.
(c) and (d) are speculative bets; 5 prior negative experiments shift the prior against finding edge in nearby search space.

**If continuing to invest time matters more than acknowledging limits, (c) before (d) is the auditor's preference order** — signal-family swap has more potential surface area than asset swap, and BTC/USDT depth means inefficiencies elsewhere are smaller absolute opportunities anyway.

---

## §9 · Caveats heredados

1. **Single 5-week window context.** Mar 30 - May 7 2026 was strongly bullish (basket +22%). Q1 verdict heavily window-dependent; in a flat or bear window, gap could narrow or reverse. CANNOT generalize "no edge" beyond this regime.

2. **Small position sample (n=20 real on curated subset).** Bootstrap CIs reflect this. Q2 with CI [3.81, 15.34] is notably tight despite small sample because effect size (mean diff ~$10) is large relative to spread. Q3 bootstrap is wider but consistent direction across 4 forward windows mitigates noise concern somewhat.

3. **Capital basis assumption: sum of size_usd ($5,948).** This is total deployed across all 20 positions cumulatively, not peak concurrent. If positions overlap, true capital basis is smaller and return rate higher. Without per-bar capital walk, conservative interpretation assumed.

4. **Off-curated trades excluded from analysis** per Q-LE1 runtime amendment. Papa's 6 off-curated symbols (LINK, SOL, TON, TRX, XAUT, ZEC) account for 13 of 33 quality-filtered positions, NOT analyzed. If those positions are systematically better/worse than curated subset, analysis bias possible. Defensible per epic #135 strategy scope but worth flagging.

5. **Counterfactual hypothetical returns ≠ realized.** Q3 assumes operator could trade at signal_ts at signal_price; real execution has slippage + spread (cost model v2 not applied per scope). Upper-bound bias on REJECTED returns; if applied, REJECTED would be ~1-5bp lower (typical slippage), bringing it closer to APPROVED. Wouldn't change qualitative direction (APPROVED < REJECTED still).

6. **7 of 20 positions have no_scan_match within ±Q-LE3 (1h).** Operator opened these without scanner-signal correspondence. Three possibilities: (a) operator's independent selection (off-strategy), (b) match window too narrow, (c) scan_id NULL caused approximate matching to miss legitimate links. Sensitivity at ±15min/±4h not run; potential follow-up. For primary verdict, these 7 are excluded from APPROVED (correct by methodology lock).

7. **Q2 n=4 SL_HIT is small.** Bootstrap CI conservative on small samples, but assumes empirical distribution is representative. If the next 4 SL_HIT events have very different P&L distribution, finding may not generalize.

8. **Single-iteration discipline.** Re-running with different window, different threshold, or different match criteria requires §4.5 self-policing (sub-spec + counter-signoff) per pre-reg §6.

9. **PyMC skill NOT invoked.** §A.4 default-prose. Formal posterior (e.g., hierarchical model on position × exit_reason × symbol, or beta-binomial on P(EDGE_STRONG | observed)) would be operator-on-demand.

10. **Production data is papá's personal record.** Analysis stays in repo (audit doc + JSON artifacts). Raw signals.db remains in sandbox path. Personal data sensitivity acknowledged.

---

## §10 · Historial

| Fecha | Cambio | Autor |
|---|---|---|
| 2026-05-15 12:32 UTC | Phase D2 execution. signals.db backup extracted to sandbox. Q-LE1 amended at runtime (16 → curated 10) per operator decision after OHLCV coverage gap. Q1=FAIL, Q2=PASS, Q3=FAIL → EDGE_WEAK. | Claude Opus 4.7 + sssamuelll |
| TBD | Operator §4.5 decision (option a/b/c/d/e) | sssamuelll |
| TBD | If (a) archive: Direction A closed; meta-decision about Direction B/C/E open | sssamuelll |
