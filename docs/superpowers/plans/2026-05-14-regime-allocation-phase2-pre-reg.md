# Phase 2 (epic #338) — Pre-registration sub-spec for regime-allocation sweep

**Fecha:** 2026-05-14
**Status:** DRAFT — pre-registration ANTES de cualquier execution. Operator review desired before Phase 3 sweep runs.
**Autor:** Claude Opus 4.7 (sesión kickoff post-Phase 1D merge) en colaboración con sssamuelll
**Tipo:** pre-registration sub-spec — fija metodología antes del sweep ejecutable
**Trigger:** Epic spec §7 Phase 2 + Phase 1D merged 2026-05-13 (PR #346) + 3 kickoff decisions locked 2026-05-14
**Cierre objetivo:** Phase 3 verdict (PASS / SUCCESS-CONDITIONAL / INCONCLUSIVE / FAIL) per §4 → PASS branches to Phase 4 paper trade (epic §7); FAIL is hard-locked to either basket re-evaluation (separate epic) OR strategy class archive (operator decides per §4.5)
**Tracking issue:** #347 (parent epic: #338)

---

## §0 · Lectura mínima requerida

Antes de revisar este pre-reg, leer en este orden (≈40 min):

1. `docs/superpowers/specs/es/2026-05-13-epic-regime-allocation-strategy-pivot.md` — epic spec completo, especialmente §4 (architecture), §5 (benchmarks), §6 (criterios), §7 (phases), §8 (params locked)
2. `docs/superpowers/specs/es/2026-05-13-r3-fail-closure-path-a-honoring.md` — A.4 closure prerequisite (por qué este epic existe)
3. `docs/superpowers/plans/2026-05-13-r3-trend-pullback-pre-reg.md` — R3 pre-reg pattern (este doc mirror su estructura)
4. `data/retune/2026-05-13-r3-trend-pullback/derivation_audit.md` — R3 FAIL verdict (referencia metodológica)
5. CLAUDE.md "Regime-allocation strategy class (epic #338, post-Phase 1)" — arquitectura post-Phase-1

Quien ya leyó esos 5 puede saltar a §1.

---

## §1 · Contexto y alcance

### §1.1 — Trigger inmediato

Phase 1D mergeada 2026-05-13 (PR #346) cierra la implementación de la arquitectura regime-allocation detrás del feature flag `cfg.regime_allocation.enabled` (nested) / `cfg.regime_allocation_enabled` (flat para test convenience). Modules nuevos: `strategy/donchian_ensemble.py`, `strategy/vol_targeting.py`. Branches nuevos en `strategy/core.py:evaluate_signal` y `backtest.py:_simulate_strategy_regime_allocation`. LRC path byte-identical confirmado por `test_strategy_core` (20 tests) + parity regression. Default OFF en `config.defaults.json` (opt-in only).

**Estado del epic post-Phase-1D:**
- ✅ Phase 0 (cost model v2) — mergeada (PR #341)
- ✅ Phase 1A (modules pure-function) — mergeada (PR #343)
- ✅ Phase 1B (dispatch in evaluate_signal) — mergeada (PR #344)
- ✅ Phase 1C (simulation path in backtest.py) — mergeada (PR #345)
- ✅ Phase 1D (config.defaults + docs) — mergeada (PR #346)
- 🔄 **Phase 2 (este pre-reg)** — en redacción
- ⏸️ Phase 3 (sweep + verdict) — bloqueado por este pre-reg
- ⏸️ Phase 4 (paper trade shadow 30-60d) — bloqueado por Phase 3 PASS
- ⏸️ Phase 5 (holdout bala única) — bloqueado por Phase 4 success criteria met
- ⏸️ Phase 6 (live promotion) — bloqueado por Phase 5 + revisor externo

**Operator-locked decisions del kickoff 2026-05-14** (vía 1 round de AskUserQuestion):

1. **Evaluation framework**: sub-windows conjunctive (mirror R3). Primary criterion ✓ en 3/3 sub-windows = PASS.
2. **Sub-window dates**: R3 pre-reg exact dates (preserva comparability con cadena R1/R2/R3 evidence stack).
3. **Sensitivity sweep verdict map**: 3-4 PASS = robust; 2 PASS = SUCCESS-CONDITIONAL (operator decide); ≤1 PASS = FAIL.

### §1.2 — Alcance del pre-reg

**Hace:**
- Locks la operationalización completa del sweep + verdict Phase 3 ANTES de cualquier compute.
- Carry forward los 7 params locked en epic §8 (aggregation, freq, vol_target primary, sensitivity sweep range, lookbacks, SHORT, leverage cap).
- Pre-registra sub-windows exact, success criterion conjuntivo, halt conditions concretas (integer thresholds sobre 10 símbolos × 4 vol_target), verdict matrix, asymmetric halt-guard scope (mirror R3 §4.6).
- Pre-registra cell selection rule, tie-break determinism, cross-window stability diagnostic.
- Aplica pre-execution math sanity check: ensemble signal frequency lower bound, warmup math (390 daily bars), halt threshold algebraic justification.
- Pre-registra auditor prior P(PASS) + Bayesian update plan post-Phase-3.

**No hace:**
- No ejecuta nada todavía. Phase 3 (code adicional para sweep tool + verdict tool + backtests) son commits subsecuentes solo si operator approves §9.
- No modifica `config.defaults.json`, `backtest.py`, `strategy/core.py`, ni cualquier code path. Pre-reg only.
- No re-litiga las 7 decisiones locked en epic §8. Esas son hardcoded carry-forward.
- No toca holdout (issue #246 hard block remains hasta Phase 5).
- No promueve `cfg.regime_allocation.enabled = True` to production. Promotion is post-Phase-3 PASS + Phase 4 paper trade + separate operator decision.
- No diseña Phase 4 (paper trade) ni Phase 5 (holdout) — out of scope per epic §7.

### §1.3 — Iteración

Esta es la **primera iteración** del Phase 2 methodology. Está abierta a operator pushback en §9.

---

## §2 · Methodology

### §2.1 — Signal: ensemble Donchian (locked per epic §8.1 + §8.4)

**Definición carry-forward del epic spec:**

- Lookbacks: `ZARATTINI_LOOKBACKS = (5, 10, 20, 30, 60, 90, 150, 250, 360)` días — implementado en `strategy/donchian_ensemble.py:ZARATTINI_LOOKBACKS`.
- Per-lookback signal: `-1` (SHORT breakout — close < lower channel previous bar), `+1` (LONG breakout — close > upper channel previous bar), `0` (no breakout, sticky to previous direction).
- Aggregation: **equal-weight vote**. Sum of 9 signals ∈ {-9, ..., +9}. Position direction = `sign(sum)`. Confidence = `|sum| / 9` ∈ [0, 1]. Flat si sum = 0.
- Channels computados sobre **daily aggregated bars** (resampled from 1H OHLCV via Pandas `freq="D"` close-of-day UTC 23:00).
- Warmup mínimo: **390 daily bars** (longest lookback 360 + vol window 30). Símbolos con < 390 daily bars retornan `NONE` con reason `regime_allocation_warmup`.

**No-touched de Phase 2:** los 9 lookbacks, el método de aggregation, y la sticky direction logic están locked. Phase 3 sweep NO los varía. La única dimensión swept es `portfolio_vol_target` (sensitivity sweep §2.5).

### §2.2 — Sizing: volatility-targeting (locked per epic §8.3 + §8.6; single-symbol scope per Phase 1 implementation)

**Definición operacional (matches `backtest.py:_simulate_strategy_regime_allocation` line 601 + 815):**

```
target_vol_per_symbol = portfolio_vol_target          # single-symbol scope, n_active=1
position_size_usd = capital × target_vol_per_symbol / realized_vol_30d_annualized
```

Donde:
- `portfolio_vol_target = 0.30` para primary pass; ∈ {0.25, 0.30, 0.35, 0.40} para sensitivity sweep.
- `realized_vol_30d_annualized = std(daily_log_returns[-30:]) × sqrt(365)`.
- `capital` = per-symbol stream capital (each símbolo runs as independent $10K stream per backtest.py architecture).

**Divergencia from Zarattini paper formula (documented per BLOCK 5 review 2026-05-14):**

The Zarattini paper uses `target_vol_per_symbol = portfolio_vol_target / n_active_symbols` over **pooled portfolio capital**. Our architecture differs structurally: `backtest.py:_simulate_strategy_regime_allocation` runs each símbolo as **independent $10K stream** (CLAUDE.md confirms: "Single-symbol scope... Portfolio-level orchestration not built in Phase 1"). Cross-symbol n_active coordination is NOT implemented in Phase 1; building it in Phase 3 would expand the §6 lock ("NO patches on backtest.py / strategy/core.py") significantly.

**Path B locked (kickoff 2026-05-14 operator decision):** Phase 2 + Phase 3 use the single-symbol formula (`n_active=1` effective). Position sizes are larger than literal Zarattini interpretation would suggest, but:
- `max_position_pct = 0.20` cap is **more frequently binding** under single-symbol scope (acts as the effective constraint).
- `min_position_usd = 50.0` floor unchanged.
- "Portfolio aggregate" §4 primary criterion sums independent stream returns (no cross-symbol capital pooling).
- The leverage cap 2x epic §8.6 becomes effectively per-symbol (each stream is independent; cross-stream leverage NOT enforced in Phase 1).

This divergence is acknowledged as a **methodological choice**, not an oversight: it preserves §6 implementation lock and aligns pre-reg with shipped code. Future epics may add portfolio-level orchestration (separate scope; not Phase 2/3 here).

**Hard caps (post-sizing):**

- `position_size_usd ≤ 0.20 × capital` per símbolo (`cfg.regime_allocation.max_position_pct`).
- `position_size_usd ≥ 50.0` (Binance min, `cfg.regime_allocation.min_position_usd`).
- `sum(|position_size_usd|) ≤ 2.0 × capital` per símbolo (leverage cap effective per-stream under independent-stream architecture).

**R-multiple sizing está estructuralmente eliminado**. NO se usa `risk_amount × 100 / sl_pct_actual`. NO se usa SL distance.

### §2.3 — Exits: signal-based (locked per epic §4.4)

**Definición carry-forward del epic spec:**

Exit triggers (única vía `_simulate_strategy_regime_allocation`):
1. **`SIGNAL_FLIP`** — ensemble vote cambia sign. Cierra posición actual + abre opposite.
2. **`SIGNAL_EXIT`** — ensemble vote a flat (sum = 0). Cierra posición a cash.
3. **`BANKRUPT`** — equity ≤ `BANKRUPTCY_THRESHOLD = 0.1 × INITIAL_CAPITAL` ($1000). Halt new entries; existing closes via SIGNAL_*.
4. **`SIM_END`** — fin de sub-window evaluation. Cierra todas las posiciones abiertas.

**NO hay SL fijo. NO hay TP fijo. NO hay TIME_LIMIT.** Esos son LRC-specific y están deshabilitados cuando `cfg.regime_allocation.enabled = True` (confirmado en Phase 1C, PR #345).

**Mecanismos preservados:**
- Bankruptcy halt (#280, #313) — operacional.
- K-cap (#309) — operacional vía pnl_pct cap, aunque sin SL fijo la binding del K-cap es rara en este path (signal-flip cierra antes de movimientos catastróficos).

### §2.4 — Cost model: v2 sqrt-participation + funding (locked per epic Phase 0)

**Carry-forward de PR #341 (mergeado):**

- `slippage_bps = base_bps + size_factor × sqrt(notional / liquidity_per_min)`, capped at `EXTREME_PARTICIPATION_CAP_BPS = 500` per fill.
- Funding rate por tier: `major=1.0 bps / 8h`, `mid=2.0`, `small=5.0` (per `costs_calibration.json`).
- Conservative mode: cost siempre positivo regardless of position direction (LONG paga funding, SHORT también — proxy de "shorts pay positive carry on average" assumption).
- Funding charged at every 8h interval position is held (floor semantics: 7h pays 0, 8h pays 1, 24h pays 3).

**Phase 2 NO modifica cost model.** Anchored a Almgren-Chriss (2001) + Donier-Bonart (2015) + Tóth et al (2011) citados en `costs_calibration.json`.

### §2.5 — Sweep grid: primary + sensitivity passes

**Primary pass (locked vol_target=30%):**

```
cells_submitted = 10 símbolos × 3 sub-windows × 1 vol_target = 30 cells
cells_running   = 8 + 8 + 9 = 25 cells (5 NO_DATA per §3 coverage: PENDLE in A+B, JUP in A+B+C)
```

Cada celda evalúa el strategy con params locked exactos. NO_DATA cells return warmup-fail marker without running ensemble. Output: `data/retune/2026-05-14-regime-allocation/sweep_primary_{A,B,C}.json`.

**Sensitivity pass (vol_target sweep):**

```
cells_submitted = 10 símbolos × 3 sub-windows × 4 vol_target = 120 cells
cells_running   = 25 × 4 = 100 cells (same NO_DATA exclusion per sub-window)
vol_target ∈ {0.25, 0.30, 0.35, 0.40}
```

Output: `data/retune/2026-05-14-regime-allocation/sweep_sensitivity_{A,B,C}.json`.

**Total compute Phase 3:** 30 + 120 = **150 cells submitted**, **125 backtests actually running** (post-coverage exclusion). Excluyendo BTC B&H baseline benchmark de §5.1.

**Baseline benchmark (separate, not in sweep):**

- BTC B&H sobre cada sub-window: 3 backtests (long-only BTC, no costs, no leverage). Comparison anchor.
- Hubrich 200-DMA filter sobre BTC: 3 backtests. Academic baseline (epic §5.2).
- LRC archived strategy sobre cada sub-window: 3 backtests (con cost model v2 para apples-to-apples). Internal control (epic §5.4).

---

## §3 · Sub-windows specification

**Locked per §1.1 operator decision (R3 pre-reg exact dates):**

| ID | Window | Regime characterization | In-coverage (regime-allocation 390-daily warmup) |
|---|---|---|---|
| A | 2022-04-01 → 2022-07-01 | Bear market 2022 (Terra/Luna May) | **8/10** (excl. PENDLE first bar 2023-07-03, JUP first bar 2024-01-31) |
| B | 2023-04-01 → 2023-07-01 | Recovery 2023 (post-FTX) | **8/10** (excl. PENDLE — first bar 2023-07-03 is AFTER B end; JUP — no bars yet) |
| C | 2025-01-30 → 2025-04-30 | Recent pre-holdout 3 months | **9/10** (excl. JUP — only ~364 daily bars by C start, < 390 warmup requirement) |

**Properties (identical to R1+R2+R3):**
- Non-overlapping ✓
- All BEFORE `holdout_start = 2025-04-30 00:00:00 UTC` ✓ (Window C ends at holdout_start exclusive)
- Genuinely OUTSIDE A.4-1 train window `[2024-01-30, 2025-01-30]` ✓
- 3 distinct regime characterizations

**Per-symbol coverage rule (revised post-review 2026-05-14):**

Símbolos con menos de **390 daily bars** disponibles antes del START de cada sub-window son excluidos de esa sub-window (warmup requirement per §2.1). First-bar dates verified empirically via `data.market_data` provider query 2026-05-14:

| Símbolo | First 1H bar (UTC) | Days pre-A_start | Days pre-B_start | Days pre-C_start |
|---|---|---:|---:|---:|
| BTCUSDT | (long history) | ≥ 1000 | ≥ 1000 | ≥ 1000 |
| ETHUSDT | (long history) | ≥ 1000 | ≥ 1000 | ≥ 1000 |
| ADAUSDT, AVAXUSDT, DOGEUSDT, UNIUSDT, XLMUSDT, RUNEUSDT | (all pre-2022) | ≥ 390 | ≥ 390 | ≥ 390 |
| PENDLEUSDT | 2023-07-03 10:00 | -457 (excl) | -94 (excl) | 576 (incl) |
| JUPUSDT | 2024-01-31 16:00 | -730 (excl) | -670 (excl) | 364 (**excl** — 364 < 390) |

**Coverage por sub-window:**
- Window A: 8 símbolos (BTC, ETH, ADA, AVAX, DOGE, UNI, XLM, RUNE) — PENDLE + JUP excluded.
- Window B: 8 símbolos (mismos que A) — PENDLE still not trading (first bar 2023-07-03 > B end 2023-07-01); JUP still not trading.
- Window C: 9 símbolos (anteriores + PENDLE) — JUP excluded because only ~364 daily bars < 390 warmup at C_start 2025-01-30.

**Warmup consideration:** el sub-window evaluation period comienza al primer daily bar **después** de los 390 bars de warmup. Para cada (símbolo, sub-window) pair en el sweep, harness extrae OHLCV cubriendo `[sub_window_start - 391 days, sub_window_end]` y descarta los primeros 390 daily bars para warmup. Evaluation effective bars: ~91 daily bars per sub-window (3-month windows; daily granularity).

**Evaluation cadence:** ensemble se computa una vez por día UTC 23:00 close (per §2.1 + epic §8.2). Position se sostiene 24h hasta próxima evaluation. Trade count per (símbolo, sub-window): natural upper bound ~91 daily evaluations; realistic count 5-30 trades dado que la mayoría de bars no producen signal flip.

---

## §4 · Success criterion

**Primary criterion (conjuntive over 3 sub-windows, mirror R3 §4 pattern):**

Phase 3 PASS = en CADA de las 3 sub-windows, **simultáneamente**:
- **Strategy total return > BTC B&H total return** sobre la misma sub-window (portfolio aggregate of in-coverage symbols, net of v2 costs)

**Required conjunctive holding:** PASS en 3/3 sub-windows. Per operator §1.1 + epic §6.1 wording ("beat BTC B&H net of v2 costs sobre portfolio aggregate").

**Amendment to epic §5.1 (acknowledged post-review 2026-05-14):** epic spec §5.1 originally anchored the comparison to a **single 15-month pre-holdout window** `[2024-01-30, 2025-04-29]` (BTC buy-and-hold over the entire pre-holdout period). Operator decision §1.1 (kickoff 2026-05-14) replaced this with a **3-sub-window conjunctive** comparison using R3-exact dates, to preserve comparability with the R1/R2/R3 evidence stack. Implication: 2 of the 3 sub-windows (A, B) fall **outside** the original epic §5.1 anchor window. This amendment is operator-approved and documented here so future readers don't assume the window was always 3×3-month.

**Notes:**
- "Portfolio aggregate" se computa como `sum(per_symbol_final_equity) - sum(per_symbol_initial_capital)` sobre los símbolos in-coverage del sub-window. Cada símbolo arranca con `INITIAL_CAPITAL = 10000.0` independiente (carry-forward del backtest architecture actual + Path B locked per §2.2). Initial capital aggregates: **Window A: 8 × $10K = $80K; Window B: 8 × $10K = $80K; Window C: 9 × $10K = $90K** (per §3 coverage).
- BTC B&H equivalente: hold BTC long-only desde sub-window start (con allocation prorata: $80K en A, $80K en B, $90K en C; o equivalentemente, multiplicar BTC % return × n_in_coverage × $10K para apples-to-apples).
- "Net of v2 costs" para BTC B&H: solo cuenta una buy fee + una sell fee, no funding (long spot, no perp). Para regime-allocation strategy: full v2 costs incluyendo funding rate sobre todas las posiciones held.

### §4.1 — Cell selection rule (primary pass)

**Pre-registered (no post-hoc maximization):**

Primary pass tiene **1 cell per (símbolo, sub-window)** (vol_target=30%, params locked). NO hay grid sobre el cual maximize — la celda primary está fija.

**Cell exclusion rule:** si una (símbolo, sub-window) cell produce `n_trades < 5` AND `simulation_completed = True` (no NO_DATA / warmup fail), marcar como `INSUFFICIENT_DATA` y excluir del aggregation. **Halt threshold for INSUFFICIENT_DATA prevalence is operationalized in §10.4 (H2)** — single source of truth para evitar internal inconsistency.

**Deterministic tie-break** (en el improbable evento que un comparison genere ties): por `(strategy_total_return, -btc_bh_total_return, alphabetical_symbol)`.

### §4.2 — Sensitivity sweep verdict mapping

**Pre-registered per §1.1 operator decision:**

Sensitivity sweep evalúa `vol_target ∈ {0.25, 0.30, 0.35, 0.40}` × 10 símbolos × 3 sub-windows = 120 cells. Para cada `vol_target` value, computar primary criterion (¿strategy beat BTC B&H en 3/3 sub-windows? Yes/No → PASS/FAIL).

| vol_target PASS count | Verdict modifier | Notes |
|---:|---|---|
| 4 of 4 | **STRONG** | Robust edge across vol_target spectrum |
| 3 of 4 | **ROBUST** | Edge present; minor sensitivity acceptable |
| 2 of 4 | **SUCCESS-CONDITIONAL** | Operator decides (per §4.5): advance with sensitivity caveat OR treat as INCONCLUSIVE |
| 1 of 4 | **SWEET-SPOT ARTIFACT (FAIL)** | Suggests calibration overfit to specific vol_target value; not generalizable |
| 0 of 4 | **FAIL clean** | No edge at any vol_target; strategy class doesn't work in this basket |

**Important:** the primary criterion gating (§4 wording) is computed at `vol_target=30%` only. The sensitivity sweep is **an additional gating layer**, not a replacement. Possible outcome combinations:

- Primary PASS at vol=30 AND sensitivity 4/4 = STRONG PASS (advance to Phase 4)
- Primary PASS at vol=30 AND sensitivity 3/4 = ROBUST PASS (advance to Phase 4)
- Primary PASS at vol=30 AND sensitivity 2/4 = SUCCESS-CONDITIONAL (operator §4.5)
- Primary PASS at vol=30 AND sensitivity 1/4 = SWEET-SPOT (FAIL — vol=30 happened to pass but isolated point)
- Primary FAIL at vol=30 AND sensitivity ≥1/4 = INCONCLUSIVE (operator §4.5 — investigates why vol=30 doesn't but other does)
- Primary FAIL at vol=30 AND sensitivity 0/4 = FAIL clean

### §4.3 — Failure modes pre-registrados

**Precondition:** las rows below asumen §10.4 halt did NOT fire. If a halt did fire: H1 → FAIL automatically (signal degenerate or universal bankruptcy); H2 → FAIL automatically (insufficient trades to evaluate). Both per §10.4 + §4.6 asymmetric guard.

| Outcome | Verdict | Phase 4 action |
|---|---|---|
| Primary ✓ at vol=30 in 3/3 AND sensitivity 3-4/4 | **PHASE 3 PASS (strong/robust)** | Automatic advance to Phase 4 paper trade (30-60d shadow). Document strength tier in verdict.json. |
| Primary ✓ at vol=30 in 3/3 AND sensitivity 2/4 | **SUCCESS-CONDITIONAL** | Operator decides (§4.5): advance with caveat OR treat INCONCLUSIVE. |
| Primary ✓ at vol=30 in 3/3 AND sensitivity ≤1/4 | **SWEET-SPOT ARTIFACT (FAIL)** | Strategy archived. No Phase 4. Document hipótesis: calibration overfit. |
| Primary ✓ at vol=30 in 2/3 sub-windows | **PARTIAL SUCCESS** | Operator decides (§4.5). Regime-specific edge may justify regime-gating notation. |
| Primary ✗ at vol=30 in ≥2/3 sub-windows AND sensitivity ≥1/4 PASS at other vol_target | **INCONCLUSIVE** | Operator decides (§4.5). **Default:** treat as PHASE 3 FAIL clean (no Phase 4). Override path: investigate why vol=30 specifically fails (sweet-spot inversa hypothesis); requires sub-spec separate doc + auditor counter-signoff per §4.5 self-policing rule. |
| Primary ✗ at vol=30 in ≥2/3 sub-windows AND sensitivity 0/4 AND mechanism engaged (≥75% of in-coverage símbolos n_trades ≥ 5) | **PHASE 3 FAIL (clean)** | Mechanism engaged but no edge. Default per §4.5: open question — basket adequacy vs strategy class viability. |
| Primary ✗ at vol=30 in ≥2/3 sub-windows AND mechanism degenerate (≥75% of in-coverage símbolos n_trades < 5) | **PHASE 3 FAIL (signal degenerate)** | Ensemble doesn't fire enough. Signal calibration issue OR basket non-trending. Document signal-firing diagnostic. |

**Default fall-through:** any primary-✗ outcome that does NOT trigger the INCONCLUSIVE row (sensitivity ≥1/4) AND does NOT trigger the degenerate row (≥75% n_trades < 5) lands in FAIL clean. Mirror R1 FAIL framing — "mechanism engaged, profitability absent". Per BLOCK 4 review fix 2026-05-14: the INCONCLUSIVE row is now explicit in this verdict table (previously only enumerated in §4.2 + §4.5; this row was missing from §4.3, creating three-place inconsistency).

### §4.4 — Cross-sub-window stability (informative, not gating)

For each in-coverage símbolo, report:
- Strategy total_return en window A: `ret_A`
- Strategy total_return en window B: `ret_B`
- Strategy total_return en window C: `ret_C`
- BTC B&H total_return en window A/B/C como reference

**Reading rules:**
- All 3 windows positive (`ret > 0`) for ≥3 símbolos → "robust per-symbol edge".
- Símbolo positive en uno y negative en otros con magnitud ≥ 50% → "high regime sensitivity" (flag para operator review).
- All 3 windows negative for ≥6 símbolos → "basket-wide breakdown" (refuerza P4 epic §3.2: basket revision needed in future epic).

### §4.5 — Operator decision hooks (only SUCCESS-CONDITIONAL / PARTIAL / INCONCLUSIVE)

**PASS branch (strong/robust):** automatic advance to Phase 4 paper trade. No operator decision needed.

**SUCCESS-CONDITIONAL branch (sensitivity 2/4 at primary PASS):** operator decides:
- (a) Advance to Phase 4 with notation "Phase 3 PASS at vol_target=30 con sensitivity caveat — solo 2/4 values pass". Phase 4 paper trade evalúa robustez en vivo.
- (b) Treat as INCONCLUSIVE; trigger §4.5 INCONCLUSIVE decision tree.

**PARTIAL SUCCESS branch (primary ✓ in 2/3 sub-windows):** operator decides:
- (a) Advance with regime-conditional notation (e.g., "PASS in recovery+recent regimes, FAIL in bear-2022"). Document constraint as Phase 4 scope. Phase 4 advances.
- (b) Treat as INCONCLUSIVE → operator override path.
- **Default:** treat as INCONCLUSIVE unless explicit override with documented Bayesian update + new Phase 4 scope.

**INCONCLUSIVE branch (primary FAIL at vol=30 but sensitivity ≥1/4):** operator decides:
- (a) **Default:** treat as PHASE 3 FAIL clean. No Phase 4. Strategy class not validated.
- (b) Operator override: investigate why vol=30 specifically fails (e.g., sweet-spot inversa). Open separate ticket. Phase 4 advance defendido con override rationale.

**FAIL branches (clean OR signal degenerate OR sweet-spot artifact):** automatic. No operator decision.
- (a) FAIL clean → mecanism engaged sin edge. Open question: ¿es el basket adequado para trend-following retail? Considerar Future Epic B (basket revision).
- (b) FAIL signal degenerate → ensemble doesn't fire enough. Either basket no-trending in test windows OR ensemble miscalibrated. Document diagnostic.
- (c) FAIL sweet-spot → vol=30 isolated success; calibration not robust. Strategy class archive.

**Asymmetric guard scope caveat (CR3 review fix 2026-05-14):** the §4.6 asymmetric halt-guard applies **only to §10-halt-fired scenarios** (favorable verdicts overridden when partial-window data). It does **NOT** cover operator override paths in this §4.5 (SUCCESS-CONDITIONAL / PARTIAL / INCONCLUSIVE → "advance to Phase 4 with caveat"). The bias risk identified in §4.6 (operator + project momentum favor declaring success early) applies equally to these override paths, but they are not symmetrically guarded.

**Self-policing requirement for §4.5 override paths:** any override that promotes a non-PASS-strong/robust verdict to Phase 4 advancement MUST:
1. Document explicit Bayesian update with magnitude shift in `derivation_audit.md` (mirror §A.4 checkpoint pattern from R1/R2/R3).
2. Open a separate sub-spec document (mirror "issue separado A.4-1.5" mechanism from CLAUDE.md §Caveats) capturing the override rationale + new Phase 4 scope BEFORE Phase 4 advances.
3. Require auditor counter-signoff (operator may invoke `code-review-excellence` agent or equivalent) confirming the override is methodologically defendible — NOT just operator-discretionary.
4. The override decision is logged in `verdict.json` under `operator_override` block with timestamp, rationale, and link to sub-spec doc.

This is **not a soft guideline** — it is a pre-reg lock with the same standing as the halt-guard. Any Phase 4 advancement from a non-PASS-strong/robust verdict that lacks these 4 elements is methodologically invalid under this pre-reg.

### §4.6 — Halt-guard scope (mirror R3 §4.6)

§10 halt + `n_windows < 3` → `PHASE_3_INSUFFICIENT_DATA` **only** when the naive verdict is favorable (PASS / SUCCESS-CONDITIONAL / PARTIAL). Negative verdicts (FAIL clean / FAIL degenerate / FAIL sweet-spot) on partial windows are **preserved** — §10 acts on dispositive partial negative evidence, no on inferential weight suspension.

**Asymmetry rationale (mirror R3 §4.6):** spurious favorable verdicts have one-sided incentive bias (operator + project momentum favor declaring success early); honest negative evidence does not carry the same bias. Demanding symmetric sample-size discipline here would penalize the discipline-preserving move (acting on dispositive evidence) and reward the discipline-eroding move (burning compute to formalize an already-decided outcome).

**Scope of this amendment:** clarifies §4.3 verdict-table behavior under §10 halt. Does **not** modify the verdict criteria themselves nor the §10 halt thresholds. Implementation lives at `tools/regime_allocation_verdict.py:_classify_verdict` (NEW para Phase 3 — mirror `tools/r3_verdict.py` pattern with the asymmetric guard already in place).

**Methodology framing:** this is an **explicit pre-reg lock**, not a soft post-hoc clarification. The asymmetric scope is pre-registered now, before any Phase 3 sweep runs, to prevent future readers from interpreting the asymmetry as silent rationalization.

---

## §5 · Edge cases pre-registrados

### §5.1 — Ensemble warmup insufficient (<390 daily bars pre-sub-window)

**Risk:** símbolos con history corta (PENDLE, JUP) no cubren los 390 daily bars de warmup pre-sub-window. Ensemble retorna `NONE` con reason `regime_allocation_warmup`.

**Pre-registered handling (revised per BLOCK 1+2 review fix 2026-05-14):**
- PENDLE first bar 2023-07-03 (verified empirically): excluida de Window A (no existía) Y Window B (first bar es AFTER B end 2023-07-01). Incluida en Window C (576 daily bars ≥ 390).
- JUP first bar 2024-01-31 (verified empirically): excluida de Window A + B (no existía) Y Window C (solo ~364 daily bars < 390 warmup threshold at C_start 2025-01-30).
- Coverage exact per §3 corrected table: **A=8, B=8, C=9**.
- Excluded símbolos NO cuentan en aggregation portfolio para esa sub-window — el initial capital de la portfolio aggregate ajusta: **Window A: $80K, Window B: $80K, Window C: $90K**.
- Diagnostic: `coverage_by_window` reportado en `manifest.json` per Phase 3 deliverable.

### §5.2 — Signal degenerate: ensemble never votes (`sum == 0` siempre)

**Risk:** si el basket entra en un período sostenido sideways donde ninguno de los 9 Donchian lookbacks emite breakout, ensemble vote stays at 0 → flat position → no trades.

**Pre-registered handling:**
- Per-cell `n_trades < 5` → INSUFFICIENT_DATA marker (§4.1).
- Halt H2 fires if ≥6 of 10 in-coverage cells in window A are INSUFFICIENT_DATA across vol_target=30% primary + 4 sensitivity values (operationalization in §10.4).
- Diagnostic: `signal_diagnostics.json` reports per-symbol per-window vote distribution (count of bars with `sum > 0`, `sum < 0`, `sum == 0`) for forensic understanding regardless of halt firing.

### §5.3 — Signal over-active (ensemble flips daily)

**Risk:** si el basket entra en un período whipsaw donde lookbacks cortos (5d, 10d) dominan votes y flip semanalmente, el strategy paga funding + slippage en cada flip sin capturar trend.

**Pre-registered handling:**
- Per-cell `n_trades > 60` (más de 60 trades en 91 daily bars = más de 60% de days con flip) → flagged en derivation_audit.md como "potentially degenerate over-flipping". No automatic FAIL — operator review.
- Vol-targeting + leverage cap actúan como dampener: incluso si el strategy flippea, position sizes están bounded.

### §5.4 — Cost model v2 calibration drift

**Risk:** cost model v2 (PR #341) está anchored a Almgren-Chriss + Donier-Bonart academic references. Si la calibración de `size_factor` o `base_bps` está mal, Phase 3 puede over- or under-estimate cost impact.

**Pre-registered handling:**
- Phase 2 NO re-calibra cost model. Carry-forward del Phase 0 calibration.
- Phase 3 deliverable incluye `cost_attribution.json` per (símbolo, sub-window, vol_target): `total_gross_pnl`, `total_slippage_usd`, `total_funding_usd`, `net_pnl`. Permite operator review post-hoc del cost impact.
- Si cost impact > 30% del gross_pnl en >50% de cells, flag como "cost-dominated outcome" — diagnostic, not gating.

### §5.5 — Bankruptcy halt interaction

Per `_simulate_strategy_regime_allocation` (PR #345): BANKRUPT halts new entries for that símbolo. Existing positions continue closing naturally via SIGNAL_FLIP / SIGNAL_EXIT.

**Pre-registered handling:**
- Bankruptcy events count by (símbolo, sub-window, vol_target) reported en `bankruptcy_diagnostics.json`.
- Si > 50% de símbolos bankrupt en una sub-window dada at vol_target=30%, → halt H1 (§10.4).
- Bankruptcy ≠ FAIL automatically. Si remaining símbolos no-bankrupt aggregate beat BTC B&H, primary criterion can still PASS — pero S4 secondary criterion (target = 0 bankruptcies) flagged.

### §5.6 — Funding cost amplification (epic §8.5 SHORT bidirectional implication)

**Risk:** bidirectional rotational + funding rate cost (conservative mode = always positive) means cada hour-of-holding adds funding cost regardless of LONG/SHORT direction. Holding períodos largos (días-a-meses per epic §3.1) puede acumular significant funding drag.

**Pre-registered handling:**
- Cost attribution split (`total_funding_usd` separado de `total_slippage_usd`) permite forensic review.
- Si funding > 50% del total_cost en >50% de cells, flag como "funding-dominated outcome" — informativo, not gating.
- Sensitivity sweep parcialmente captura este factor (lower vol_target → smaller positions → less funding); si vol_target=25 PASS pero vol_target=40 FAIL específicamente por funding, eso es diagnostic insight (informativo).

### §5.7 — Basket inadequacy persistence (epic R1 risk)

Per epic §4.1 + §9.R1: research suggests 8/10 son problemáticos. Operator eligió mantener para preserve continuity con #135.

**Pre-registered handling:**
- Phase 2 NO modifica basket.
- Cross-sub-window stability §4.4 reportará per-symbol attribution. Si solo BTC + ETH contribuyen positive aggregate, eso refuerza P4 (epic §3.2) — open separate epic para basket revision, NO bajo este epic.
- Phase 3 FAIL clean podría tener basket inadequacy como root cause; documentado en derivation_audit.md pero NO auto-triggers H5 (basket revision).

### §5.8 — Daily aggregation edge: 1H → daily resampling

**Risk:** ensemble se computa sobre daily bars resampled from 1H OHLCV. Resampling convention: `df1h.resample('D').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})`. Bars con < 24 hours (e.g., first/last day del sub-window con partial coverage) pueden producir distorted indicators.

**Pre-registered handling:**
- Sub-window evaluation skips first daily bar y last daily bar si < 24 hours coverage (drop partial-day bars).
- Documented in `tools/regime_allocation_sweep.py` source comment.
- Tested via assertion en unit test before sweep ejecuta.

---

## §6 · Deliverable structure

After operator approval of this pre-reg, **Phase 3 execution** (separate PR after Phase 2 merge) lands the following on a new branch off `main`:

```
data/retune/2026-05-14-regime-allocation/
├── derivation_audit.md             # methodology recap + per-cell verdict + cross-window stability + sensitivity verdict + Bayesian update
├── manifest.json                   # cutoff, code_commit, leakage_check, sub_windows, sweep grid, coverage_by_window, baseline refs
├── sweep_primary_A.json            # 10 símbolos × 1 vol_target, sub-window A (8 in-coverage)
├── sweep_primary_B.json            # sub-window B (9 in-coverage) — ONLY IF §10 halt NOT fired
├── sweep_primary_C.json            # sub-window C (10 in-coverage) — ONLY IF §10 halt NOT fired
├── sweep_sensitivity_A.json        # 10 símbolos × 4 vol_target (32 cells in-coverage para A; 36 para B; 40 para C)
├── sweep_sensitivity_B.json        # ONLY IF §10 halt NOT fired
├── sweep_sensitivity_C.json        # ONLY IF §10 halt NOT fired
├── baseline_btc_bh_{A,B,C}.json    # BTC B&H per sub-window (3 files)
├── baseline_hubrich_{A,B,C}.json   # Hubrich 200-DMA filter on BTC per sub-window (3 files)
├── baseline_lrc_archived_{A,B,C}.json  # LRC archived strategy per sub-window con cost model v2 (3 files)
├── signal_diagnostics.json         # per-cell vote distribution, trade count, exit reason breakdown
├── cost_attribution.json           # per-cell gross_pnl, slippage_usd, funding_usd, net_pnl breakdown
├── bankruptcy_diagnostics.json     # per-cell bankruptcy events
├── halt_diagnostic.json            # ONLY IF §10 halt fires — full per-symbol breach explanation
├── verdict.json                    # formal §4 primary verdict + sensitivity map + classification per §4.6 halt-guard
└── README.md                       # summary verdict + primary table + sensitivity table + cross-window stability + Bayesian update prose
```

Plus:
- `tools/regime_allocation_sweep.py` — reproducible sweep script (NEW para Phase 3 — adapted from `tools/r3_trend_pullback_sweep.py` pattern)
- `tools/regime_allocation_verdict.py` — verdict calculator (NEW para Phase 3 — adapted from `tools/r3_verdict.py` pattern; reuses `_classify_verdict` asymmetric halt-guard scope per §4.6)
- NO patches on `backtest.py` / `strategy/core.py` — Phase 1 already shipped the implementation path; Phase 3 only adds the harness wrapper that toggles the flag and runs backtests.
- Unit tests `tests/test_regime_allocation_sweep.py` (NEW para Phase 3): sweep harness correctness, deterministic cell selection, halt-condition activation, sensitivity verdict mapping.
- Update `docs/superpowers/specs/es/2026-05-13-epic-regime-allocation-strategy-pivot.md` §12 history table with Phase 2 + Phase 3 status.
- Update this pre-reg (`2026-05-14-regime-allocation-phase2-pre-reg.md`) §14 history with execution + verdict.
- PR comment con (a) verdict per §4, (b) updated prior estimate per §12 checkpoint, (c) operator decision hooks per §4.5 (only relevant if SUCCESS-CONDITIONAL / PARTIAL / INCONCLUSIVE — PASS y FAIL clean son pre-locked), (d) IF Phase 3 PASS: Phase 4 paper trade scope draft.

**Live-path safety:** Phase 3 sweep runs with `cfg.regime_allocation.enabled = True` en harness only. Production scanner (`btc_api.py`, `btc_scanner.py`) preserva default OFF. NO changes a `config.defaults.json:regime_allocation:enabled` propuestos en Phase 3. Promotion to live es Phase 6 (post-Phase-5 holdout PASS + revisor externo).

---

## §7 · What this pre-reg does NOT cover

- **Other strategy classes** — LRC archived per A.4 closure (#321 path (a)); trend-pullback FAILED per R3 (#336). Phase 2 + Phase 3 evaluate **only** the regime-allocation strategy class. No iteration among signal frames within this epic.
- **Basket revision (H5)** — operator hard-locked NO H5 follow-up under epic §4.1. Caveat documented en §5.7. Si Phase 3 FAIL specifically points to basket inadequacy, that's input para a separate future epic, NOT bajo este epic.
- **Cost model v2 re-calibration** — Phase 0 (PR #341) calibration is locked. No re-tuning in Phase 2 / Phase 3 even if cost-dominated outcomes are observed (those are diagnostic only).
- **`config.defaults.json` promotion of `regime_allocation.enabled`** — pre-reg only commits to derivation methodology + sweep. Promotion is post-Phase-3 PASS + Phase 4 paper trade + separate operator decision. NO en Phase 3 PR.
- **Holdout work** — issue #246 hard block remains. Phase 3 does NOT touch `data/holdout/` under any outcome. Phase 5 is the only authorized touch point.
- **Phase 4 paper trade design** — out of scope. Phase 4 spec drafted separately if Phase 3 PASS.
- **Phase 5 holdout evaluation criteria** — out of scope. Documented in epic §7 Phase 5 placeholder; locked separately when Phase 4 completes.
- **Regime detector revival** — `detect_regime()` (F&G + funding + price composite) deprecated per epic §4.6. NO uso bajo `cfg.regime_allocation.enabled = True`. NO test de regime-aware long-only en este epic.
- **5m entry trigger** — eliminated per epic §4.2 (entries on close of daily bar 23:00 UTC). No 5m trigger logic en regime-allocation path. NO opt-out.
- **Aggregation method alternatives** — equal-weight vote locked per epic §8.1. Phase 3 does NOT sweep over signal-strength weighted or regime-conditional weights.
- **Lookback list alternatives** — Zarattini exact 9 locked per epic §8.4. Phase 3 does NOT sweep over subset 4 or alternative lookback sets.
- **Position update frequency alternatives** — daily locked per epic §8.2. Phase 3 does NOT test per-1H-bar with threshold variants.
- **Iteration on Phase 2 itself** — if Phase 3 produces ambiguous results, operator decides per §4.5 (default hard-lock to verdict; override path documented). NO "Phase 2.5" or "Phase 2 v2" — pre-reg is locked at single iteration.

---

## §8 · Pre-registered decision branches

Resumen de branch points donde la metodología tiene rule explícita. Cada branch tiene rule pre-registered ANTES de ver el data. Eliminates rationalización post-hoc.

| Branch point | Rule | Reference |
|---|---|---|
| Strategy class | Regime-allocation (LRC + trend-pullback archived; no iteration) | Epic §0, §1 |
| Aggregation method | Equal-weight vote (locked epic §8.1) | §2.1 |
| Lookbacks | Zarattini exact 9 (5/10/20/30/60/90/150/250/360) (locked epic §8.4) | §2.1 |
| Position update frequency | Daily 23:00 UTC close (locked epic §8.2) | §2.1 |
| Portfolio vol target (primary) | 30% annualized (locked epic §8.3) | §2.2, §2.5 |
| Sensitivity sweep vol_target | {25, 30, 35, 40}% (locked epic §8.7) | §2.5, §4.2 |
| SHORT enabled | Bidirectional rotational (locked epic §8.5) | §2.1 |
| Leverage cap | 2x (locked epic §8.6) | §2.2 |
| Max position per symbol | 20% of capital (locked epic §4.3) | §2.2 |
| Min position USD | $50 (Binance min) | §2.2 |
| Sizing | Vol-targeting (R-multiple eliminated) | §2.2 |
| Exits | Signal-based (SL/TP/TIME_LIMIT structurally disabled) | §2.3 |
| Cost model | v2 sqrt-participation + funding (Phase 0 PR #341 locked) | §2.4 |
| Sub-windows | A 2022-04-01→07-01, B 2023-04-01→07-01, C 2025-01-30→04-30 (operator locked §1.1) | §3 |
| Sub-window evaluation period | 91 daily bars post-warmup (390 daily bars warmup pre-window) | §3, §5.1 |
| Coverage exclusion rule | <390 daily bars pre-window → exclude (PENDLE from A+B; JUP from A+B+C) | §3, §5.1 |
| n_active formula (Path B) | `target_vol_per_symbol = portfolio_vol_target` (single-symbol scope per `backtest.py:601`; n_active=1 effective). Divergence from Zarattini portfolio-pooled approach documented in §2.2 | §2.2 |
| Primary criterion | Strategy total_return > BTC B&H total_return per sub-window, net of v2 costs, portfolio aggregate | §4 |
| Epic §5.1 amendment | Original anchor: 15-month single-window. Pre-reg: 3 sub-window conjunctive (operator decision §1.1). 2 of 3 sub-windows fall outside original epic §5.1 range. | §4 |
| Conjunctive holding | 3/3 sub-windows for PASS (operator locked §1.1) | §4 |
| Cell exclusion | `n_trades < 5` → INSUFFICIENT_DATA per (símbolo, sub-window, vol_target). Halt threshold operationalized in §10.4 H2 (single source of truth). | §4.1 |
| Tie-break | `(strategy_total_return, -btc_bh_total_return, alphabetical_symbol)` | §4.1 |
| Sensitivity verdict map | 4/4=STRONG, 3/4=ROBUST, 2/4=CONDITIONAL, 1/4=SWEET-SPOT FAIL, 0/4=FAIL clean (operator locked §1.1) | §4.2 |
| Cross-window stability | Informative only, not gating | §4.4 |
| PASS strong/robust | Auto-advance to Phase 4 paper trade | §4.5 |
| SUCCESS-CONDITIONAL (sensitivity 2/4) | Operator decides advance vs INCONCLUSIVE | §4.5 |
| PARTIAL (primary 2/3 windows) | Operator decides (default INCONCLUSIVE) | §4.5 |
| INCONCLUSIVE (primary FAIL vol=30 + sensitivity ≥1/4) | Default hard-lock as FAIL; operator override path with 4-element self-policing requirement (§4.5) | §4.5 |
| Operator override self-policing | Any non-PASS-strong/robust → Phase 4 advance requires (1) Bayesian update in derivation_audit.md, (2) separate sub-spec doc, (3) auditor counter-signoff, (4) verdict.json operator_override block | §4.5 |
| FAIL (clean / degenerate / sweet-spot) | Automatic. Strategy archived. Future Epic B (basket revision) considered if degenerate. | §4.5 |
| Halt H1 (universal bankruptcy) | ≥75% of in-coverage símbolos bankrupt en sub-window A AT vol_target=30 → halt B+C (≥6 in A/B; ≥7 in C) | §10.4 |
| Halt H2 (signal degenerate) | ≥75% of in-coverage símbolos con n_trades < 5 en sub-window A AT vol_target=30 → halt B+C (≥6 in A/B; ≥7 in C). Loosens epic §6.3 H2 anchor from 10→5 (rationale §10.4). | §10.4 |
| §4.6 asymmetric halt-guard | Favorable verdicts overridden under partial windows; negative preserved | §4.6 |
| Live path safety | Flag-gated; defaults to False; NO live promotion in Phase 2/3 scope | §6, §7 |
| Pre-reg iteration | Single-iteration discipline; NO Phase 2.5 / Phase 2 v2 | §1.3, §7 |

---

## §9 · Open questions for operator

La mayoría de decisiones materiales están locked en epic §8 y kickoff §1.1. Las preguntas restantes son operationalization details — operator review desired antes de Phase 3 execution.

### §9.1 — [OPTIONAL] Halt threshold concreteness — confirm or adjust

Per §10.4 + §8 table:
- **H1 (universal bankruptcy):** ≥6 of in-coverage símbolos bankrupt en sub-window A AT vol_target=30 → halt B+C.
- **H2 (signal degenerate):** ≥6 of in-coverage símbolos con `n_trades < 5` en sub-window A AT vol_target=30 → halt B+C.

Operator may adjust (thresholds revised to use uniform 75% per BLOCK 3 review fix 2026-05-14, consistent across windows given coverage A=8, B=8, C=9):
- (a) **Keep as proposed [recommended]** — ≥75% of in-coverage (≥6 in A/B, ≥7 in C). Stricter than epic §6.3 ">80% bankrupt" anchor (which would be ≥7 in A/B, ≥8 in C); rationale: regime-allocation has fewer trades than R3 sweep, so noise in bankruptcy attribution is higher; tighter halt prevents long expensive B+C compute on a degenerate signal.
- (b) Looser: ≥80% literal (≥7 in A/B, ≥8 in C — matches epic §6.3 anchor literally).
- (c) Stricter: ≥62.5% (≥5 in A/B, ≥6 in C).

### §9.2 — [OPTIONAL] Insufficient-data threshold `n_trades < 5`

Per §4.1 cell exclusion rule. **This loosens epic §6.3 H2 threshold from 10→5** (CR1 review fix 2026-05-14). Honest framing: epic §6.3 was drafted pre-Phase-1 (before §10.2 algebraic estimate). Phase 1 implementation reveals daily-frequency ensemble produces 5-25 trades/cell expected (vs LRC frame's 30-60 trades/cell at 1H granularity that R3 used). 10 trades is too high a bar for regime-allocation; 5 trades is the minimum meaningful sample for per-symbol attribution. This pre-reg operationalizes the threshold based on the post-implementation empirical evidence, not the pre-implementation estimate. Direction of change is **loosening** (less strict), unlike H1 which is stricter than epic literal — both deviations require explicit framing for diagnostic honesty.

- (a) **n_trades < 5 [recommended]** — operationalization per §10.2 algebra; loosens epic §6.3 explicitly.
- (b) n_trades < 3 — even looser; trusts very low-frequency cells.
- (c) n_trades < 10 — preserves epic §6.3 literal; would exclude more cells in regime-allocation given lower trade frequency than R3 (>50% of cells likely INSUFFICIENT_DATA under daily granularity).

### §9.3 — [OPTIONAL] Compute budget hard cap

Per §11 estimate: ~2-3h paralelizado for 150 backtests + 9 baselines. 

- (a) **No hard cap [recommended]** — wallclock unbounded since compute is local + reproducible. Default in R1/R2/R3 pattern.
- (b) Hard cap 4h — abort if exceeded; diagnose perf issue before retry.
- (c) Hard cap 6h — more generous; matches R3 estimate ceiling.

### §9.4 — [RESOLVED — consumed pre-review] Branch + PR title convention

Removed from open-questions per OBSERVATION review fix 2026-05-14: Phase 2 branch (`docs/regime-allocation-phase2-pre-reg`) and PR title (`docs(epic #338 Phase 2): pre-registration sub-spec for regime-allocation sweep (closes #347)`) were already locked when this pre-reg was first published (PR #348 created 2026-05-14 with those exact strings). Asking permission post-facto for an already-consumed decision is procedurally inconsistent. Phase 3 branch convention (`feat/regime-allocation-phase3-sweep`) decided when Phase 3 starts — not a Phase 2 pre-reg concern.

### §9.5 — [REQUIRED before Phase 3] Confirm baseline benchmark execution scope

Per §2.5 + §5 epic: 3 baseline sets to compute alongside main sweep:
- **BTC B&H** per sub-window: 3 backtests (long-only spot, one buy + one sell fee, no funding)
- **Hubrich 200-DMA filter on BTC** per sub-window: 3 backtests (long BTC when close > 200-day SMA on daily bars, else cash)
- **LRC archived strategy** per sub-window: 3 backtests (current LRC params + cost model v2, for apples-to-apples internal control per epic §5.4)

- (a) **All 3 baselines [recommended]** — full benchmark stack per epic §5; needed for primary criterion comparison (BTC B&H) + academic baseline (Hubrich) + internal control (LRC archived). +9 backtests compute (~15 min).
- (b) BTC B&H + Hubrich only — skip LRC archived (defer to post-Phase-3 if needed); -3 backtests.
- (c) BTC B&H only — minimum primary criterion comparison; -6 backtests.

---

## §10 · Pre-execution math sanity check

### §10.1 — Warmup math: 390 daily bars

Per §2.1: warmup = longest_lookback + vol_window = 360 + 30 = 390 daily bars. For each (símbolo, sub-window) pair:
- Sub-window start date: e.g., 2022-04-01 for Window A.
- Required OHLCV data: from `[sub_window_start - 391 days, sub_window_end]` (391 daily bars = 390 warmup + 1 first-evaluation-day buffer).
- Daily bars resampled from 1H OHLCV (Pandas `freq="D"`, close-of-day UTC 23:00).
- Pre-warmup period (~13 months) is consumed silently; ensemble starts emitting non-NULL signals at warmup_end + 1.

**Verification (revised post-review 2026-05-14 with empirical first-bar dates from `data.market_data` provider query):**

- **PENDLE** first 1H bar `2023-07-03 10:00 UTC` → first complete daily bar `2023-07-04`. Days to A_start (2022-04-01): -457 (PENDLE didn't exist) → excluded from A. Days to B_start (2023-04-01): -94 (PENDLE didn't exist) → excluded from B (PENDLE first bar 2023-07-03 is also AFTER B end 2023-07-01, so no in-window data either). Days to C_start (2025-01-30): 576 ≥ 390 → included in C.
- **JUP** first 1H bar `2024-01-31 16:00 UTC` → first complete daily bar `2024-02-01` (per §5.8 partial-day skip rule). Days to A_start: -730 → excluded from A. Days to B_start: -670 → excluded from B. Days to C_start (2025-01-30): **364** (from 2024-02-01 to 2025-01-29 inclusive). **364 < 390** → **excluded from C** (JUP does not satisfy 390 daily bars warmup at C_start; previous claim "JUP included in C" was a non-sequitur identified in BLOCK 2 review).

**Algebra check passes (corrected).** Coverage per §3 corrected table: **A=8, B=8, C=9**.

### §10.2 — Signal frequency plausibility (algebraic lower bound)

For ensemble to fire ≥5 trades per (símbolo, sub-window):
- Sum of 9 Donchian signals must flip sign across ≥5 daily bars over ~91 evaluation days.
- Equivalent: position direction must change at least 5 times.

**Random-walk approximation:** if each of the 9 lookbacks independently votes ±1 with 50% probability (random walk null), ensemble vote follows binomial(9, 0.5) — symmetric around 0. Probability of `|sum| > 0` (any non-flat position) ≈ 75%. Probability of sign flip from one day to next ≈ 30-40% (depends on autocorrelation; sticky signals reduce this).

**Conservative estimate:** even with sticky lookbacks (longer lookbacks rarely flip), at least 2 of the 9 lookbacks (5d, 10d) flip frequently (~5-10 times per 91 days each). Ensemble vote sum responds. **Expected trade count per (símbolo, sub-window): 8-25** under reasonable assumptions.

**Plausibility passes:** signal should fire frequently enough to produce ≥5 trades. Halt H2 catches the failure case (whole-basket non-trending epoch where all 9 lookbacks coexist at vote = 0).

**Honest caveat:** Crypto majors are NOT random walks; they exhibit autocorrelation + clustering. Actual data may deviate from null. §5.2 + §5.3 cover both directions (under-firing + over-firing).

### §10.3 — Cost impact lower bound

Per Phase 0 calibration: per-trade cost ≈ `0.05% notional` (slippage) + funding rate (~0.01% per 8h hold).

**Under Path B (§2.2 single-symbol scope; cap-binding):** typical position size ≈ `0.20 × capital = $2K` (max_position_pct cap binding most of the time under single-symbol vol-targeting with portfolio_vol_target=30% and realized_vol ≈ 50%). For a típical trade (size $2K, hold 5 days):
- Slippage: 2 × $2000 × 0.0005 = $2 (entry + exit).
- Funding: 5 days × 24h / 8h = 15 funding periods × $2000 × 0.0001 = $3 (conservative tier major).
- Total per-trade cost: ~$5 (0.25% of notional).

Si strategy genera ~15 trades por (símbolo, sub-window), total cost per cell ≈ $75. Sobre INITIAL_CAPITAL = $10K, eso es ~0.75% drag. Cost-dominated outcome flag fires if cost > 30% del gross_pnl (§5.4). Under Path B with cap-binding sizing, the cost is **lower in absolute terms** than original Zarattini-formula path (smaller positions) **but proportional to notional** so the relative drag is unchanged.

**Plausibility passes:** cost model v2 calibration leaves room for strategy edge even at 15-20% trade frequency.

### §10.4 — Halt conditions pre-registered (concrete thresholds)

**Halt H1 (universal bankruptcy):** during sub-window A execution at vol_target=30%, if **≥75% of in-coverage símbolos** bankrupt within the sub-window, halt B+C. Concrete counts (per §3 coverage A=8, B=8, C=9): **≥6 in Windows A/B; ≥7 in Window C** (though Window C only matters if A halt doesn't fire). Mechanism failed structurally — vol-targeting + leverage cap NOT preventing capital destruction.

**Halt H2 (signal degenerate):** during sub-window A execution at vol_target=30%, if **≥75% of in-coverage símbolos** have `n_trades < 5`, halt B+C. Concrete counts: **≥6 in Windows A/B; ≥7 in Window C**. Ensemble fails to fire — signal calibration issue OR basket non-trending in Window A specifically. **This loosens epic §6.3 H2 anchor (originally `n_trades < 10`) → `n_trades < 5` per CR1 review fix 2026-05-14** (rationale: §10.2 algebraic estimate shows daily-frequency ensemble produces 5-25 trades/cell; 10 trades/cell would exclude >50% of cells even under healthy strategy; the epic threshold was drafted pre-Phase-1 before empirical algebra was available). Direction of change: loosening (less strict); operator may revert to epic literal per §9.2 option (c).

**Either halt → write halt diagnostic.** `data/retune/2026-05-14-regime-allocation/halt_diagnostic.json` con full per-symbol breach explanation + sensitivity sweep also halted (no need to run vol_target ∈ {25, 35, 40} si primary already fails decisively).

**Why ≥75% (uniform across windows), not literal epic §6.3 ">80%":**

Epic §6.3 says ">80% de símbolos bancarrotan en >50% de cells". For regime-allocation:
- "Cells" interpretation: in primary pass there's 1 cell per (símbolo, sub-window). So "in >50% of cells" reduces to "in the primary cell".
- ">80%" of 8 in-coverage = 6.4, rounded up to 7. Literal interpretation: ≥7 of 8 (Window A/B) bankrupt OR ≥8 of 9 (Window C) bankrupt.
- This pre-reg adjusts to **≥75% uniform** = ≥6 in A/B, ≥7 in C as **stricter than literal**. Rationale: regime-allocation has lower trade frequency than LRC; noise in per-symbol bankruptcy attribution is higher; tighter halt threshold prevents long expensive B+C compute on a degenerate signal. Direction of change: stricter (more sensitive). Operator can adjust per §9.1.

**Direction-of-change framing transparency (per CR1 review fix 2026-05-14):** H1 deviates from epic §6.3 in the **strict** direction (75% < 80% literal anchor); H2 deviates in the **loose** direction (5 < 10 epic anchor). Both directions are honest deviations driven by post-Phase-1 empirical evidence; the framing is explicit to enable diagnostic review.

**§4.6 asymmetric halt-guard:** under halt, favorable verdicts (PASS, SUCCESS-CONDITIONAL, PARTIAL) are overridden to `PHASE_3_INSUFFICIENT_DATA`. Negative verdicts (FAIL clean, FAIL degenerate, FAIL sweet-spot) on partial windows are preserved. Per §4.6 + R3 §4.6 mirror. **Override paths in §4.5 are NOT covered by this guard** (see §4.5 self-policing requirement added per CR3).

---

## §11 · Compute estimate

| Stage | Estimate | Notes |
|---|---|---|
| Code patch (`tools/regime_allocation_sweep.py`) | 2-3 h | Adapted from `tools/r3_trend_pullback_sweep.py` pattern + regime-allocation-specific cell shape (no grid, 1 primary cell + 4 sensitivity cells per (símbolo, sub-window)) |
| Code patch (`tools/regime_allocation_verdict.py`) | 1-2 h | Adapted from `tools/r3_verdict.py` + §4.6 halt-guard scope mirror + sensitivity verdict mapping §4.2 |
| Unit tests (`tests/test_regime_allocation_sweep.py`) | 2-3 h | ~15-20 tests: sweep harness correctness, deterministic cell selection, halt-condition activation, sensitivity verdict mapping, baseline benchmark execution |
| Baseline backtests (9 = 3 baselines × 3 sub-windows) | 15-20 min | Sequential, single config each |
| Sweep execution primary (25 running of 30 submitted, parallelized 8 workers) | **25-40 min wall-clock** | Per-backtest avg ~60s (daily granularity is faster than 1H R3 sweep); 5 NO_DATA cells return early per §3 coverage |
| Sweep execution sensitivity (100 running of 120 submitted, parallelized 8 workers) | **1.3-1.7 h wall-clock** | Per-backtest avg ~60s; 4× primary compute minus same coverage exclusions |
| Verdict tool execution + JSON outputs + README | 30-45 min | Includes Bayesian update prose + decision hooks |
| Derivation audit (md prose, math, interpretation tree) | 1-2 h | Math/data interpretation + sensitivity verdict + per-symbol attribution + cross-window stability |
| PR comment + Phase 4 scope draft (if PASS) | 30 min | Operator-facing summary |
| **Total Phase 3 compute time** | **~10-13 h** | Single contiguous session OR split across 2 sessions |

**Halt-conditional savings:** if §10.4 halt fires (H1 or H2) after sub-window A primary pass, total drops to **~4-5 h** (no B+C primary, no sensitivity sweep across B+C, simpler derivation_audit + FAIL path).

**Phase 2 (this PR) compute:** doc-only. Estimated **2-3 h** for redaction + operator review + commit + CI wait. NO code, NO compute beyond CI lint + holdout isolation regression test.

---

## §12 · Auditor prior on Phase 3 outcome

**Auditor (Claude Opus 4.7) prior before Phase 3 execution:**

| Outcome | Probability | Reasoning |
|---|---:|---|
| **PASS strong (3/3 windows AT vol=30 + sensitivity 4/4)** | ~8-12% | Requires regime-allocation to beat BTC B&H portfolio aggregate across 3 distinct regimes AND across 4 vol_target values. High bar given basket caveat (epic §4.1: 8/10 problematic per research) + cost model v2 funding drag + bidirectional rotational adds complexity. |
| **PASS robust (3/3 windows + sensitivity 3/4)** | ~10-15% | More likely than strong — typical sensitivity sweep shows edge concentrated in 2-3 of 4 vol_target values; 3/4 PASS is the "edge present but acceptable sensitivity" tier. |
| **SUCCESS-CONDITIONAL (3/3 windows + sensitivity 2/4)** | ~8-12% | Plausible — edge may concentrate in a "narrow" vol_target window; operator decision needed if 2/4 robust. |
| **SWEET-SPOT ARTIFACT (3/3 windows + sensitivity 1/4)** | ~5-8% | Suggests calibration overfit to specific vol_target — defensive bias against false positive. |
| **PARTIAL SUCCESS (2/3 windows AT vol=30)** | ~10-15% | Bear 2022 (Window A) is most adversarial; strategy may PASS recovery + recent but FAIL bear. Regime-conditional edge plausible per epic §9.R7 (2024 was "Bitcoin year"). |
| **PHASE 3 FAIL clean (mechanism engaged, no edge)** | ~30-35% | Most likely outcome. Ensemble fires, vol-targeting prevents catastrophic losses, but aggregate edge insufficient to beat BTC B&H net of v2 costs (especially funding drag on bidirectional rotational). Mirrors R1/R2/R3 framing: "mechanism engaged, profitability absent". |
| **PHASE 3 FAIL signal degenerate (ensemble doesn't fire)** | ~5-8% | Sustained sideways periods where all 9 lookbacks vote 0. §5.2 + §10.4 H2 designed to catch this. |
| **PHASE 3 INSUFFICIENT_DATA (§4.6 halt-guard fires)** | ~3-5% | Halt H1 (universal bankruptcy) or H2 (signal degenerate) fires in Window A primary → halt B+C. Asymmetric guard preserves negative verdict for non-favorable cases. |

**Joint prior:** ~26-39% PASS-or-CONDITIONAL (across strong/robust/CONDITIONAL/PARTIAL outcomes that don't auto-FAIL). ~50-65% FAIL clean. ~5-8% FAIL degenerate. ~3-5% INSUFFICIENT.

**Comparison to literature:**
- Zarattini 2025 paper: Sharpe 1.58 over 2015-2024. If we matched it on 91-day sub-windows, primary criterion would PASS easily.
- BUT: Zarattini's universe is top-20 rotational + 9-year window + survivorship-bias-free careful construction. Our basket is 10 fixed + 3-month windows + curated under contaminated simulator (epic §A.2 caveat). Realistic discount factor ~50-70% on Zarattini Sharpe → expected Sharpe ~0.5-0.8 in our setup.
- That Sharpe range is **secondary criterion** territory (S1: target ≥ 0.8 adequate). Primary criterion (beat BTC B&H) is more lenient: even Sharpe 0.4 strategy can beat BTC B&H in 3 sub-windows if regime characterization is favorable.
- 2022-2025 includes a major BTC bull run; primary criterion is hardest in Window C (recent 2025) if BTC alone outperforms diversified portfolio.

**Operator's prior (per kickoff §1.1):** not explicitly stated, but kickoff signaled "moderate expectations" — aligned with ~25-35% PASS auditor prior.

**Bayesian update plan post-Phase-3:**
- **PASS strong/robust:** P(strategy viable for live) jumps to ~40-60%; advance to Phase 4 paper trade. Validate live cost vs modeled cost; check execution drift.
- **SUCCESS-CONDITIONAL / PARTIAL:** P(viable) ~20-30%; operator decides per §4.5. Default INCONCLUSIVE path activates.
- **SWEET-SPOT / FAIL clean / FAIL degenerate:** P(viable) drops to ~5-10%; strategy class archived under current basket. Open question about whether different basket (separate epic) would change result.
- **INSUFFICIENT_DATA (halt fired):** P(viable) preserved at pre-Phase-3 prior; no inferential weight from partial windows. Operator decides next step (re-run with adjusted halt thresholds OR archive).

**§A.4 prior re-evaluation checkpoint:** post-Phase-3 PR comment must include explicit Bayesian update with magnitude shift documented in 2-3 sentences. Same pattern as R1/R2/R3 per audit §A.4 + R3 §12 mirror.

---

## §13 · Methodology limitations carried forward

Per epic §9 (risk register) + R3 §13 + audit §A.2 + §A.7 + §A.8, Phase 2 + Phase 3 inherit these caveats:

1. **Cost model v2 calibration uncertainty.** Anchored to academic references (Almgren-Chriss 2001, Donier-Bonart 2015) but NOT data-fit on our specific instruments. Real-world slippage may diverge from sqrt-participation prediction, especially in extreme stress events (e.g., Terra/Luna May 2022). §5.4 covers cost-dominated outcome flagging.

2. **Basket curated under contaminated simulator (epic §A.2 H5 caveat).** The 10 símbolos were selected by epic #135 using pre-#223 simulator (phantom-profit bug). Selection criteria may have favored coins with phantom-LRC-edge that does NOT generalize to trend-following frame either. Phase 3 FAIL clean is a likely outcome partially because of this; documented per §5.7.

3. **3-month sub-windows are short for days-to-months hold period.** Regime-allocation strategy holds positions days-to-months per epic §3.1. A 91-day evaluation window can include only 1-2 full holding cycles. Sample size per cell is limited; per-symbol per-window verdicts have high variance. Cross-window stability (§4.4) partially mitigates.

4. **Sensitivity sweep is conservative (4 vol_target points).** Only 4 discrete vol_target values evaluated. Edge may exist at vol_target=0.32 or 0.28 — not detectable. Phase 3 PASS robust (3/4) is the strongest defensible verdict; sensitivity 4/4 is rare in practice unless edge is truly insensitive (which is itself an unusual property).

5. **Bidirectional rotational requires perp markets + cross-margin.** Live execution (Phase 6 conditional) depends on Binance Futures USDT-M with cross-margin enabled. Funding rate cost charged on all positions regardless of direction (conservative mode). If actual funding regime differs from calibration tier-anchor (especially during extreme funding events), live costs may diverge from modeled. §5.6 documents the open exposure.

6. **Vol-targeting depends on realized vol estimate.** `std(daily_log_returns[-30:]) × sqrt(365)` is a backward-looking estimate. Regime shifts can produce stale estimates (vol explodes but estimator is delayed). Position sizing in early stages of vol regime change can be inappropriate. §5.3 (over-active flipping) partially captures this; bankruptcy halt is the safety net.

7. **No regime detector means no early-warning mechanism.** Deprecated per epic §4.6. The strategy "discovers" regime via ensemble vote distribution but does NOT explicitly gate on macro regime. In a sustained whipsaw + funding-extreme environment, strategy can underperform without an explicit halt mechanism beyond per-symbol bankruptcy.

8. **5m entry trigger eliminated.** Per epic §4.2 entries on close of daily bar UTC 23:00. Cannot leverage intra-day price action for entry refinement. Trade-off accepted by design (reduces signal latency + slippage from over-trading).

9. **Single-iteration discipline.** If Phase 3 produces ambiguous results, no "Phase 2.5" or "Phase 3 v2" — operator decides per §4.5 (default hard-lock to verdict) or escalates per epic §6.3 + §9. Mirror R3 §13 #10.

10. **Sub-window choice may not generalize.** A/B/C cover bear-2022/recovery-2023/recent-2025. Other regimes not tested (bull-2021, bear-2024, etc.). Phase 5 holdout (12 months 2025-04-30 → 2026-04-30) covers a different time slice; if Phase 3 PASS but Phase 5 FAIL, that's the regime-generalization gap evidence.

11. **Independent-stream architecture vs Zarattini portfolio approach (BLOCK 5 review fix 2026-05-14).** The pre-reg formula in §2.2 uses single-symbol scope (`target_vol_per_symbol = portfolio_vol_target`; n_active=1 effective) to match `backtest.py:_simulate_strategy_regime_allocation` shipped in Phase 1C (PR #345). The Zarattini paper uses portfolio-pooled capital with `n_active_symbols` dynamic across active positions. Our architecture's 10 independent $10K streams cannot natively implement portfolio-pooled vol-targeting without re-architecting the simulator. Operator decision §1.1 (Path B) accepts this divergence: position sizes are larger than literal Zarattini formula would produce, but bounded by `max_position_pct = 0.20` cap which is **more frequently binding** under single-symbol scope. Cross-symbol leverage cap 2x effectively becomes per-symbol (each stream is independent). If future epic adds portfolio-level orchestration, the n_active formula can be revisited; under this pre-reg, the divergence is locked.

12. **Epic §5.1 anchor amendment (CR2 review fix 2026-05-14).** The primary criterion in §4 compares regime-allocation strategy vs BTC B&H **per sub-window**, conjunctive 3/3. Epic §5.1 originally anchored the comparison to a **single 15-month pre-holdout window** `[2024-01-30, 2025-04-29]`. The kickoff operator decision §1.1 changed this to 3-sub-window conjunctive (R3-exact dates). 2 of the 3 sub-windows (A: 2022-04-01→07-01; B: 2023-04-01→07-01) fall **outside** the original epic §5.1 anchor window. This amendment is operator-approved and explicitly framed for downstream readers to avoid confusion about historical anchoring.

---

## §14 · Historial

| Fecha | Cambio | Autor |
|---|---|---|
| 2026-05-14 | Pre-reg sub-spec inicial — drafted from kickoff prompt + epic #338 spec + R3 pre-reg pattern + 3 operator decisions locked via AskUserQuestion (PR #348 opened) | Claude Opus 4.7 (sesión kickoff post-Phase-1D) + sssamuelll |
| 2026-05-14 | **PR #348 review fixes applied** — 5 BLOCKs + 3 CHANGES_REQUESTED + 1 OBSERVATION addressed (single revision pass): BLOCK 1+2 coverage table corrected to A=8/B=8/C=9 (PENDLE first-bar 2023-07-03 verified empirically, JUP first-bar 2024-01-31 verified empirically); BLOCK 3 H2 threshold de-duplicated (single source of truth in §10.4); BLOCK 4 INCONCLUSIVE row added explicitly to §4.3 verdict table; BLOCK 5 Path B locked (n_active=1 single-symbol scope matching `backtest.py:601/815`; Zarattini divergence documented in §2.2 + §13 #11); CR1 H2 loosening framing added (§9.2 + §10.4); CR2 epic §5.1 amendment acknowledged (§4 + §13 #12); CR3 asymmetric-guard scope caveat + 4-element self-policing requirement added to §4.5; OBSERVATION §9.4 procedural-question converted to resolved-decision note | Claude Opus 4.7 + sssamuelll (Path B operator-locked via AskUserQuestion 2026-05-14) |
| TBD | Operator re-review + final approval | sssamuelll |
| TBD | Phase 2 PR merged via gh pr merge --squash | sssamuelll |
| TBD | Phase 3 execution (separate PR after Phase 2 merge) | sssamuelll + Claude Opus 4.7 |

Reservar líneas para iteración post-operator-re-review y verdict registration en Phase 3 closure.



