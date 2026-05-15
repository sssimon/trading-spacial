# Market context at MANUAL exit — findings & candidate rule

**Fecha:** 2026-05-15
**Pre-reg:** `docs/superpowers/plans/2026-05-15-exit-market-context-pre-reg.md`
**Subset:** 16 MANUAL closes on curated 10 (post-quality filter)
**Output:** descriptive feature analysis + emergent deterministic pattern

---

## §1 · Methodology recap

For each of 16 MANUAL exits, extracted ~12 OHLCV-derived features at exit_ts grouped in 6 hypothesis families (H1 bar pattern, H2 local extremum, H3 momentum, H4 volatility, H5 time-favorable, H6 post-exit hindsight). 1h granularity. Classified exit quality based on 4h forward price action:
- **GOOD**: both favorable and adverse <1% post-exit (calm)
- **PREMATURE**: favorable ≥1% post-exit (price continued)
- **REVERSAL_CAUGHT**: adverse ≥1% post-exit (price reversed)

Distribution: 4 GOOD + 8 PREMATURE + 4 REVERSAL_CAUGHT = 16.

---

## §2 · Striking emergent pattern — the green-bar trigger

**6 of 6 LONG winners classified PREMATURE share a uniform feature pattern at exit:**

| id | symbol | pnl% | bar_color | close_position | new_extremum |
|---|---|---:|---|---:|:---:|
| 17 | BTCUSDT | +2.62% | green (favorable) | **0.96** | False |
| 18 | ETHUSDT | +2.47% | green | 0.68 | True |
| 25 | PENDLEUSDT | +5.51% | green | **1.00** | False |
| 29 | ETHUSDT | +2.06% | green | **0.96** | True |
| 33 | RUNEUSDT | +6.00% | green | 0.79 | True |
| 34 | BTCUSDT | +1.38% | green | 0.64 | True |

**Median close_position: 0.87** (close in upper third of bar range). All green bars (= favorable for LONG). 4 of 6 new local extrema (bar's high exceeded prior 5 bars).

These exits represent operator's "this looks good, take profit" decision — exiting when price action is strong AND favorable.

**But in 4h post-exit hindsight, ALL 6 had favorable price continuation ≥1%.** Median post-exit favorable: 2.74%. Operator exited mid-move — captured majority but left material additional upside.

---

## §3 · Contrast — when operator catches the actual reversal

**LONG positions classified REVERSAL_CAUGHT (n=2) show the OPPOSITE feature pattern:**

| id | symbol | pnl% | bar_color | close_position |
|---|---|---:|---|---:|
| 31 | BTCUSDT | +0.68% | red (adverse) | 0.39 |
| 47 | RUNEUSDT | +7.27% | red | **0.20** |

Both: red bars with close near bottom of range. These are the **actual reversal signals** — bar formed, then closed near low. Operator captured the turn (or held until it materialized).

LONG losing position (n=1 in REVERSAL_CAUGHT): id=28 XLMUSDT, color=adverse, close_pos=0.17. Even though it lost, exit was AT a reversal signal (further adverse move continued in next 4h).

---

## §4 · The deterministic element

Cross-tabulating bar pattern features × exit quality reveals an asymmetry:

| Exit quality | n | color median | close_pos median |
|---|---:|---|---:|
| GOOD | 4 | mostly adverse | 0.67 |
| REVERSAL_CAUGHT | 4 | adverse | **0.26** |
| PREMATURE | 8 | favorable | **0.85** |

**The deterministic element**: operator exits LONG positions when:
1. Exit bar is GREEN (favorable color for LONG)
2. Close in upper half/third of bar range (close_position > ~0.6)

This pattern fires 6 of 6 winning LONG cases that turned out PREMATURE. It's behavioral: "strong bar = take profit now".

When operator DOESN'T use this pattern (instead exits on red bar + close near bottom), he catches actual reversals.

---

## §5 · LONG vs SHORT divergence

**4 of 4 SHORT exits classified GOOD** (calm post-exit):

| id | symbol | pnl% | bar_color (relative to direction) | quality |
|---|---|---:|---|---|
| 35 | ETHUSDT | +0.25% | adverse | GOOD |
| 37 | UNIUSDT | +1.54% | adverse | GOOD |
| 38 | ETHUSDT | +0.70% | adverse | GOOD |
| 39 | UNIUSDT | +0.92% | favorable | GOOD |

For SHORTs, 3 of 4 exits happened on bars moving AGAINST the SHORT (favorable bar = green for LONG = adverse for SHORT). Operator closes SHORTs when price moves UP — opposite reaction from LONG closes.

**SHORT exits don't show the premature-exit pattern.** The 4h post-exit data shows minimal continuation (median favorable < 0.4%).

**The green-bar trigger pattern is LONG-specific.** Operator timing on SHORTs is consistent and post-hoc calm.

---

## §6 · H4 atr_entry universally zero — data gap

`atr_entry` is recorded as 0 for all 16 positions (or NULL). H4 volatility metric `move_from_entry_atr_normalized` is 0 across the board — not computable from this data. Either:
- The system doesn't populate `atr_entry` on MANUAL position open
- Field exists but always 0

This is a procedural finding parallel to TP/SL=NULL from previous EDA: papá's MANUAL workflow doesn't capture ATR-based context at entry.

H4 hypothesis can't be evaluated. Not actionable from current data.

---

## §7 · Candidate deterministic rule

Based on the emergent pattern, **LONG exit rule candidate** (hypothesis only, n=16 sample):

```
Rule A — Hold through green strength bars

DO NOT exit a LONG position when:
  - Current 1h bar is green (close > open) AND
  - close_position > 0.7 (close in upper 30% of bar's range)

This blocks the "green strong bar = take profit" reflex.

DO exit a LONG position when:
  - Current bar is red (close < open) OR
  - close_position < 0.5 (close in lower half of bar's range)

This triggers on actual stall/reversal signal.
```

### Simulation against the 16 sample (informational)

Applying Rule A retroactively to the 6 PREMATURE LONG cases:

| id | exit close_pos | exit color | Rule A action |
|---|---:|---|---|
| 17 BTC | 0.96 | green | **HOLD** (rule blocks exit) |
| 18 ETH | 0.68 | green | borderline (≤0.7 → exit kept) |
| 25 PENDLE | 1.00 | green | **HOLD** |
| 29 ETH | 0.96 | green | **HOLD** |
| 33 RUNE | 0.79 | green | **HOLD** |
| 34 BTC | 0.64 | green | exit kept (close_pos < 0.7) |

**5 of 6 PREMATURE LONG exits would be cancelled by Rule A.** Operator would hold positions further. Whether that materializes into better captured P&L depends on subsequent bar action (not simulated end-to-end here — would require modeling "what new exit fires next").

Applied to REVERSAL_CAUGHT LONG cases (good captures):
- id=31 BTC: close_pos=0.39 → exit fires (red + close_pos < 0.5)
- id=47 RUNE: close_pos=0.20 → exit fires (red + close_pos < 0.5)

Both correctly fire under Rule A.

### Rule A applies to LONG only

SHORT exits (n=6) don't show the same emergent pattern. Don't change SHORT exit logic.

### Rule A is NOT a complete TP system

It's a **veto on premature green-bar exits**. Without a complementary exit trigger, position could hold indefinitely waiting for red bar + low close_pos. Need a fallback:

- **Time fallback**: force exit at hours_held > X
- **Adverse threshold fallback**: exit if adverse move from peak > Y%
- **Combined with trailing stop**: most common chandelier-exit pattern

Operator decides whether to formalize this into testable rule.

---

## §8 · Caveats + limitations

1. **n=16 sample is hypothesis generation only.** No statistical validation. The "6 of 6 PREMATURE LONG share green+upper-half pattern" cluster is striking but small sample.

2. **Single regime (bull Mar-May 2026).** Pattern may not generalize to bear or sideways. In bear regime, "green bar with close near top" might be a counter-trend reversion signal where exit IS appropriate.

3. **Premature ≠ universally bad.** Capturing 73-96% of move (per previous capture-rate analysis) is operationally fine. The question is whether the additional 1-3% post-exit is worth holding through additional drawdown risk.

4. **No operator interview.** Rule A is inferred from data; the actual cognitive trigger operator uses may be more nuanced (e.g., he also considers RSI, BTC trend, news context — features not in this study).

5. **Rule A vetoes exits but doesn't define when TO exit.** Needs complementary mechanism (trailing stop, time limit, adverse threshold).

6. **Out-of-sample test required before production.** Even if Rule A holds on these 16 positions retroactively, generalization to future trades requires either: (a) more positions accumulate then re-test, OR (b) walk-forward simulation on extended historical OHLCV with synthesized "what would operator have done".

7. **H4 atr_entry=0 procedural data gap.** ATR-normalized features not computable. Either system doesn't populate or field always zero.

8. **LONG bias in pattern.** SHORT exits (n=6) don't show same pattern. Rule A LONG-specific.

---

## §9 · Next-step options for operator

1. **Implement Rule A as veto layer** in scanner, NO automated exit-on-trigger yet. Scanner alerts operator "green-strong bar exit candidate — Rule A says hold". Operator can override or accept. Manual layer reduces premature exits while preserving operator discretion.

2. **Pre-reg Rule A formal test** as separate study. Lock thresholds (0.7 and 0.5), lock complementary exit mechanism, simulate on extended OHLCV reconstruction over hold periods + posterior measure of additional capture %.

3. **Capture mental targets via UI** instead of automation. Prompt operator at exit decision: "your reason for closing?" → over time, collect labeled examples to verify Rule A inference.

4. **Do nothing — accept pattern as informational.** Operator now knows the green-bar bias exists. Conscious awareness may itself shift behavior.

Auditor recommendation: **option 2 (pre-reg formal test)** if there's appetite to extend the work, or **option 1 (veto alert)** as lowest-cost intervention. Options 3 + 4 are lighter alternatives.

---

## §10 · Synthesis

**Empirical pattern of MANUAL exit (LONG, n=10):**
- Green bar + close in upper 60-100% of range → **PREMATURE** (6 of 6 cases): operator's "take profit now" reflex fires mid-move
- Red bar + close in lower 40% of range → **REVERSAL_CAUGHT** (2 of 2 cases): operator catches actual stall/reversal

**Empirical pattern of MANUAL exit (SHORT, n=6):** 4/4 GOOD quality, mixed bar colors, no clear emergent pattern. Operator's SHORT timing is post-hoc consistent and calm.

**Deterministic element identified**: the green-bar bias on LONG exits is consistent (6/6) and actionable as a veto rule. Whether operator chooses to implement is a separate decision.

---

## §11 · Historial

| Fecha | Cambio | Autor |
|---|---|---|
| 2026-05-15 14:00 UTC | Phase ED1 execution. 16 features extracted per H1-H6 hypothesis suite. Emergent pattern: 6/6 LONG winners PREMATURE share green+upper-half bar pattern. Rule A candidate proposed. n=16, hypothesis generation only. | Claude Opus 4.7 + sssamuelll |
| TBD | Operator decides between 4 next-step options | sssamuelll |
