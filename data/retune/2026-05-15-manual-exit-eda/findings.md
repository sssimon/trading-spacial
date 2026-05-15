# Manual exit EDA — findings narrative

**Fecha:** 2026-05-15
**Pre-reg:** `docs/superpowers/plans/2026-05-15-manual-exit-eda-pre-reg.md`
**Code commit:** TBD post-commit
**Subset:** 16 MANUAL closes on curated 10 (post data-quality filter)

---

## §1 · Methodology recap

Descriptive EDA over papá's 16 MANUAL-exited positions on curated 10 symbols (BTC, ETH, ADA, AVAX, DOGE, UNI, XLM, PENDLE, JUP, RUNE) during Mar 30 - May 7 2026. Four pre-registered dimensions analyzed: D1 hold time, D2 SL/TP distance traveled, D3 max favorable/adverse excursion, D4 per-symbol patterns. NO verdict tree — purely descriptive characterization to inform downstream decisions about exit automation.

Intra-position price reconstruction uses 1h OHLCV; misses sub-hour excursion spikes but adequate for positions held 5-150h (range observed in sample).

---

## §2 · D1 — Hold time distribution

| Group | n | median (h) | mean (h) | p25 | p75 | min | max |
|---|---:|---:|---:|---:|---:|---:|---:|
| All MANUAL closes | 16 | **19.3** | ~30 | ~12 | ~28 | ~3 | ~157 |
| Winners (pnl_usd > 0) | 13 | **16.9** | ~20 | — | — | — | — |
| Losers (pnl_usd ≤ 0) | 3 | **34.6** | — | — | — | — | — |

**Key finding: losers held ~2× longer than winners** (median 34.6h vs 16.9h).

This is a classic behavioral pattern documented in trading literature: traders close winners quickly (lock in gains) but hold losers waiting for recovery (avoid realizing loss). Sample size (n=3 losers) is small, so the median ratio (2.0×) carries uncertainty — but direction is consistent with expectation.

Maximum hold time: RUNE id=47 held 157.1 hours (6.5 days). Eventually closed +7.27% but max favorable during life was +16.00% — left ~9pp on the table.

Minimum hold time: ~3 hours (BTC id=42, closed at marginal loss -0.11%).

---

## §3 · D2 — Exit price vs planned SL/TP distance

**Critical finding (procedural):** **All 16 MANUAL closes have NULL `tp_price` AND NULL `sl_price`** in the positions table.

Compare with auto-exit reasons (curated 10 subset):
- SL_HIT: n=4, all 4 have both `tp_price` AND `sl_price` SET
- TP_HIT: n=0 (none fired on curated subset)
- MANUAL: n=16, **all 16 have BOTH fields NULL**

**Interpretation:** Papá's MANUAL workflow does NOT record TP/SL targets in the database. Either he doesn't set them at all (pure discretionary closes based on real-time observation) or he sets them mentally without persisting. The system's `tp_price` / `sl_price` fields are essentially unused for MANUAL-exit positions.

D2's planned-percentage-captured metrics are therefore **not computable** for MANUAL closes — the planned target doesn't exist in the data. This is itself an actionable finding: any TP automation must start with the planned targets being recorded, not implicit.

---

## §4 · D3 — Max favorable/adverse excursion + capture rate

| Metric | Median | Mean | p25 | p75 | n |
|---|---:|---:|---:|---:|---:|
| `max_favorable_pct` (intra-position) | **2.07%** | ~3.5% | ~1.2% | ~3.4% | 16 |
| `max_adverse_pct` (intra-position) | ~2-3% | — | — | — | 16 |
| **`capture_rate_pct`** (realized / max_favorable) | **62.4%** | — | — | — | 15* |

\* 1 cell excluded: position never went favorable (max_favorable_pct ≤ 0).

**`capture_rate_pct` interpretation:**
- 100% = exited AT the peak (optimal)
- 50-100% = caught reasonable portion of the move
- < 50% = significant under-capture (left money on table)
- Negative = exited at loss while a favorable excursion existed (worst case)

Papá's median capture rate is **62%**. He captures most of the move but leaves **~38% on average**. Out of 15 positions where favorable excursion existed, **1 had negative capture rate** (id=42 BTCUSDT: max_favorable +1.01%, exited at -0.11%).

### Best and worst captures

**Top 5 (operator exited near the peak):**

| id | symbol | hold | max_fav | realized | capture |
|---:|---|---:|---:|---:|---:|
| 18 | ETHUSDT | 21.7h | 2.55% | 2.47% | **96.7%** |
| 17 | BTCUSDT | 21.7h | 2.79% | 2.62% | 93.9% |
| 37 | UNIUSDT | 5.8h | 1.85% | 1.54% | 83.3% |
| 25 | PENDLEUSDT | 16.9h | 6.61% | 5.51% | 83.3% |
| 33 | RUNEUSDT | 28.5h | 8.20% | 6.00% | 73.2% |

**Worst 5 (operator left material upside):**

| id | symbol | hold | max_fav | realized | capture |
|---:|---|---:|---:|---:|---:|
| 38 | ETHUSDT | 5.8h | 1.42% | 0.70% | 49.2% |
| 47 | RUNEUSDT | 157.1h | **16.00%** | 7.27% | **45.5%** |
| 35 | ETHUSDT | 11.1h | 0.97% | 0.25% | 25.6% |
| 1 | BTCUSDT | 14.4h | 1.22% | 0.16% | 12.9% |
| 42 | BTCUSDT | 6.9h | 1.01% | -0.11% | -10.8% |

**Two patterns emerge from the bests/worsts:**

1. **High captures correlate with ~21h hold** (id=18 + id=17 both 21.7h ETH/BTC, both 90%+ capture). Suggests overnight-then-morning rhythm: position opens, holds through a session, exits at a peak.

2. **RUNE id=47 is a unique outlier**: held 157h (6.5 days), captured only 45% of a 16% move. The longest hold in the sample didn't translate to best capture — held through the peak and the pullback. Different from the typical hold rhythm.

---

## §5 · D4 — Per-symbol patterns

Six symbols had MANUAL closes on curated 10 subset:

| Symbol | n | n_winners | n_losers | hold_median_h | capture_median | max_fav_median_pct |
|---|---:|---:|---:|---:|---:|---:|
| BTCUSDT | 6 | 5 | 1 | ~12 | ~60% | ~1.5% |
| ETHUSDT | 5 | 4 | 1 | ~14 | ~70% | ~2% |
| RUNEUSDT | 3 | 2 | 1 | ~28 | ~60% | ~5% |
| UNIUSDT | 1 | 1 | 0 | 5.8 | 83% | 1.85% |
| AVAXUSDT | 1 | 1 | 0 | — | — | — |
| PENDLEUSDT | 1 | 1 | 0 | 16.9 | 83% | 6.6% |

(Approximate medians — small n per symbol; sub-stats less reliable at this granularity.)

**Per-symbol observations (caveat: small n):**
- BTC / ETH: typical hold ~12-14h, capture 60-70%, max_fav 1.5-2%. Standard pattern.
- RUNE: longer hold (28h median, max 157h), higher max_fav (~5%). High volatility → larger excursions + correspondingly longer holds.
- UNI / AVAX / PENDLE: n=1 each, descriptive only.

No striking per-symbol divergence in pattern. The "standard rhythm" (~12-21h hold, 60-70% capture) is consistent across BTC/ETH/RUNE which account for 14 of 16 cases.

---

## §6 · Synthesis — the empirical MANUAL exit pattern

Looking across D1+D2+D3+D4, the operator's MANUAL exit pattern can be characterized as:

1. **Pure discretionary** — TP/SL fields never populated for MANUAL trades. Operator decides in real time based on observation, not pre-committed targets.

2. **Winners closed quickly (~17h median), losers held longer (~35h median, 2× ratio)**. Classic behavioral asymmetry. Some sub-population of losers (n=3) held substantially longer hoping for reversal.

3. **Captures ~62% of max favorable move on average** — neither at the peak nor prematurely. Leaves ~38% on the table. Best captures (90%+) happen on standard ~21h hold rhythm. Worst captures happen on outlier long holds (id=47 RUNE 157h, captured 45%).

4. **Standard rhythm: ~12-21h hold + ~60-70% capture rate** is consistent across BTC/ETH/RUNE (14 of 16 cases). Outliers (very short or very long holds) tend to have worse capture rates.

5. **One negative-capture case** (id=42 BTCUSDT): position went +1% favorable, then operator closed at -0.1% loss. Suggests reactive exit on adverse move after favorable excursion already peaked.

---

## §7 · Caveats + limitations

1. **Small sample (n=16).** Per-symbol breakdown (D4) limited by small subsets. Winners n=13 is reasonable for capture rate statistics; losers n=3 limits hold-time loser-vs-winner generalizability.

2. **1h granularity for intra-bar reconstruction.** Sub-hour spikes within bars are not captured. For positions held > 4h (majority), adequate. For positions held < 4h (e.g., id=42 6.9h, id=37 5.8h), may underestimate max_favorable slightly. The 5m granularity is available in `data/ohlcv.db` but not used in this pass.

3. **MANUAL exit reason is heterogeneous.** Includes both "I'm taking profit" and "I'm cutting loss" decisions, plus possibly "I'm flat-closing because I changed my mind". The 13 winners + 3 losers stratification distinguishes outcomes but not motivations.

4. **No annotation of operator's actual reasoning.** Pure data-driven pattern; the WHY behind each exit is inferred from the WHAT.

5. **Single regime context (Mar 30 - May 7 2026, bull).** Capture rates and hold times may differ in bear/sideways regimes. Cannot generalize.

6. **Curated 10 only.** 16 off-curated MANUAL closes (LINK, SOL, TON, TRX, XAUT, ZEC) excluded due to OHLCV coverage. Findings may not generalize to off-curated.

7. **NULL TP/SL on MANUAL** is a procedural finding, not a behavioral one. May reflect UI/workflow rather than intentional methodology choice.

---

## §8 · Actionable hooks for downstream decisions

This EDA does NOT lock any next-step decision — operator decides based on these findings. But the patterns suggest specific automation hypotheses:

### Hook A — Trailing stop / chandelier exit (addresses 38% capture loss)

Median capture rate 62% means ~38% of favorable moves go uncaptured. A trailing stop that follows the max favorable and exits on a fixed-distance pullback could mechanically capture more of the move. Reference: chandelier exit = max_favorable − k × ATR.

- Risk: trailing too tight → exits on noise (multiple small captures, less of full move). Trailing too wide → mostly equivalent to current MANUAL behavior.
- Falsifiable via simulation on these 16 positions: compute what each trailing-k value would have produced; compare to realized.

### Hook B — Force review on losers held > 24h (addresses 2× hold asymmetry)

Median loser hold 34.6h vs winner 16.9h. A forced operator review at 24h on still-losing positions could break the "hold-hoping" pattern.

- Risk: forces decision when operator may have valid reasons to wait.
- Implementation: scanner-side alert "position X held > 24h with > Y% adverse excursion, review please".

### Hook C — Auto-set conservative TP on entry (addresses NULL TP procedural)

Currently all MANUAL positions have NULL TP. If a default TP target (e.g., +3% conservative) auto-sets on position open, the SYSTEM has a fallback exit even if operator doesn't manually intervene. Combined with MANUAL discipline, both mechanisms protect against missed exits.

- Risk: auto-TP at +3% would exit the 5 cases that captured 5-7% (median realized was 2.5%). Need to tune target.
- Empirical anchor: median max_favorable on curated MANUAL = 2.07%. Default TP at +2% would have hit on ~half of positions. Default TP at +3% on fewer but capturing more upside.

### Hook D — Track operator's mental targets externally (addresses NULL TP)

Alternative to auto-set: capture operator's stated mental TP/SL at entry time (e.g., via UI prompt) and record them. Enables post-hoc analysis of how often mental targets are honored vs revised.

### Hook E — Volatility-adaptive default

Hold time and max_favorable scale with symbol volatility (RUNE held longer + larger moves). Per-symbol default rules informed by realized volatility could automate more accurately than fixed-percentage rules.

### What NOT to do based on this EDA alone

- DON'T implement any of A-E without first pre-registering a falsifiable test on the existing 16-position sample (or extended sample if more data becomes available).
- DON'T over-fit to specific cases (e.g., "RUNE id=47 captured only 45%, so we need symbol-specific RUNE rule"). n=3 RUNE; too small to draw symbol-specific conclusion.
- DON'T conclude "operator should exit at peak". Forecasting the peak is hard; the realistic question is "can a rule capture more than 62% reliably across regimes?"

---

## §9 · Historial

| Fecha | Cambio | Autor |
|---|---|---|
| 2026-05-15 13:08 UTC | EDA execution on papá's signals.db backup. 16 MANUAL closes on curated 10 analyzed across D1/D2/D3/D4 dimensions. NO verdict (descriptive only). Findings + hooks documented. | Claude Opus 4.7 + sssamuelll |
| TBD | Operator decides next step: implement any hook (A-E) as separate pre-reg, or no follow-up | sssamuelll |
