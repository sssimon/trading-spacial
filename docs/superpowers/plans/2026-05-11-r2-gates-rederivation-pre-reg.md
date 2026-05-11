# R2 — Pre-registration sub-spec: theoretical re-derivation of per-symbol gates

**Fecha:** 2026-05-11
**Status:** DRAFT — pre-registration ANTES de cualquier execution. Operator review desired before sweep runs.
**Autor:** Claude Opus 4.7 (sesión audit-execution) en colaboración con sssamuelll
**Tipo:** pre-registration sub-spec — fija metodología antes de derivación + sweep
**Trigger:** Audit spec §6 R2 + §A.1/§A.3 + operator R2 methodology review (2026-05-11)
**Cierre objetivo:** issue #317 (gates calibration deferred) cuando R2 execution complete + new gates promoted

---

## §1 · Contexto y alcance

Este sub-spec pre-registra la metodología EXACTA con que R2 va a re-derivar `time_limit_hours`, `max_participation_rate`, y `cooldown_hours` para cada uno de los 10 símbolos del basket curado, en cumplimiento de:

- **Audit spec §6 R2** — "re-derivar gates per-symbol desde teoría, no desde #281 contaminado"
- **Audit spec §A.1** — post-R2 TP-sensitivity re-check antes de declarar success
- **Audit spec §A.3** — validation sobre ≥3 sub-windows non-overlapping pre-holdout
- **Issue #317** — gates calibration on post-fix simulator (deferred → activated bajo Phase 2 R2)

**Hace:**
- Fija WHICH theoretical anchors se usan (ATR-time-to-1-ATR for TL; Almgren-Chriss inverse for PoV).
- Pre-registra las 3 sub-windows EXACTAS sobre las que se validará.
- Fija aggregation rule conjuntiva (≥6 de 8 en EACH sub-window).
- Pre-registra failure / edge cases (pathological values, tightening risk, etc.).

**No hace:**
- No ejecuta la derivación todavía. Una vez que este pre-reg sea operator-approved, la derivación y el sweep son commits subsecuentes.
- No modifica `config.defaults.json` ni código. Pre-reg only.

**Iteración:** Esta es la **segunda iteración** del methodology — la primera (texto en PR #323 conversation) recibió 1 fix crítico (sub-windows leakage) + 4 importantes + 3 menores. Todos incorporados acá.

---

## §2 · Methodology

### §2.1 — TL re-derivation: ATR-based time-to-±1-ATR-move

**Anchor:** volatility-normalized horizon. Tiempo típico que el price tarda en moverse ±1 ATR es una medida intrínseca del símbolo, independiente del simulator output.

**Pasos por símbolo (sobre pre-holdout window [earliest, 2025-04-29]):**
1. Compute ATR(14) en 1H bars (matchea signal timeframe).
2. Para cada bar `i`: encontrar el siguiente bar `j > i` tal que `|close_j − close_i| ≥ ATR(14)_i`. Time delta `Δt = (timestamp_j − timestamp_i) en horas`.
3. Si no hay tal bar `j` dentro de `lookahead_max = 72h`, registrar `Δt = censored` (right-censored observation).
4. `tl_anchor_raw` = mediana de los `Δt` no-censored sobre todos los bars del window.
5. **Round** `tl_anchor_raw` a la nearest-integer-hour.
6. **Clamp** a `[4, 48]` horas. If clamped, log warning + flag para review.

**Por qué cada decisión:**
- **1H bars**: matchea signal timeframe (la estrategia evalúa 1H signals). Time-to-move medido en otra timeframe induce mismatch.
- **Median (no mean)**: robusto a tails. Symbols con distribuciones long-tailed (PENDLE, JUP — high-vol low-liq) tienen mean ≫ median.
- **lookahead_max = 72h**: bars sin move ≥1 ATR en 3 días son rare pero existen en regímenes ultra-quiet. Censura a 72h evita aceptar valores absurdos (e.g., bar único quiet en BTC durante un fin de semana de 2024).
- **Round-to-nearest-integer-hour**: simpleza + eliminates la quantización rara del rounding ladder `{4, 6, 8, 12, 16, 24, 48}` original. Match config.json hour-precision.
- **Clamp [4, 48]**: floor 4h porque <4h no es una time-horizon coherente para una estrategia 1H-signal (operador no opera estilo HFT); ceiling 48h porque holdout = 12 months / TL = 48h implica solo 180 entries possible per symbol (TL × cooldown ≥ TL → frequency cap). Symbols clamped flagged para review.

**Pathological value handling:**
- Si `tl_anchor_raw < 4`: clamp a 4, log `WARN: symbol X has anomalously short time-to-1-ATR; clamped to 4h. Investigate.`. Cont. con el clamp value.
- Si `tl_anchor_raw > 48`: clamp a 48, log similar. Cont.
- Si una distribución de `Δt` tiene `<100 samples` (símbolo con muy pocos bars), abort para ese símbolo. JUP (start 2024-01-31) puede caer cerca de este threshold según el sub-window.

### §2.2 — PoV re-derivation: Almgren-Chriss inverse for 30 bps slippage target

**Anchor:** target slippage anchor del cost model. `costs_calibration.json:sources.size_factor` documenta el calibration target: *"an order of 0.1% of avg_volume_per_minute incurs ~30 bps total slippage on majors"*. Operating point donde el linear cost model es most trustworthy.

**Pasos por tier (tier mapping definido en §2.4):**
1. Per tier, leer `base_bps` y `size_factor` de `costs_calibration.json`.
2. Resolver `participation_ratio_per_min` tal que `base_bps + size_factor × participation_ratio_per_min = 30`. Es decir: `participation_ratio_per_min = (30 − base_bps) / size_factor`.
3. Convert `participation_ratio_per_min` (notional / liquidity_per_min) a `max_participation_rate` (notional / 24h_median_bar_volume) — conversión que depende de la definition exacta de `liquidity_per_min` en `backtest_costs.py`. La derivation debe documentar la conversión paso-a-paso.
4. Per-symbol value = tier value (i.e., no per-symbol customization within a tier — A-C theory anchors per-tier, no per-symbol).

**Caveat documentado (per operator pedido):** El target 30 bps es el cost model v1's calibration anchor. Esto hereda toda la incertidumbre del cost model v1 (linear, conocidamente flawed en thin liquidity per H8 del audit). Una alternativa sería **15 bps** (más conservador, menos slippage tolerated) — pero también significaría PoV más conservador → menos trades → potencialmente bajo el threshold de "≥30 trades" del success criterion. Si R2 falla con 30 bps, considerar 15 bps como ablación en una iteración separada.

**Pre-registered alternatives if 30 bps fails:**
- Si R2 falla con 30 bps, NO iterar a 15 bps sin operator approval. Reportar resultado de 30 bps primero. Ablación es un decision branch, no automatic.

### §2.3 — Cooldown derivation: maintain transitive rule + floor

**Rule (pre-existing, mantenida):** `cooldown_hours_new = max(time_limit_hours_new, NW=4, floor=6)`.

**Interpretación de `NW=4`:** literal del codebase actual (`backtest.py:_validated_cooldown_hours`). NW probablemente refiere a un "neighbor wait" o "non-overlap window" — la value `4` es un floor adicional al floor general de 6. **Acción derivation_audit.md:** investigar y documentar la procedencia de NW=4 (1 línea de grep + cita).

**Comportamiento bajo new_TL diferentes:**
| new_TL | NW | floor | cooldown_hours_new | Notas |
|---|---|---|---|---|
| 2h | 4 | 6 | 6 | Floor-dominated. Flag para review. |
| 4h | 4 | 6 | 6 | Floor-dominated. OK. |
| 8h | 4 | 6 | 8 | TL-dominated. OK. |
| 14h | 4 | 6 | 14 | TL-dominated. OK. |
| 24h | 4 | 6 | 24 | TL-dominated. OK. |
| 48h | 4 | 6 | 48 | TL-dominated. OK. Pero implica trade frequency cap muy bajo (1 every 48h ≈ 180 entries/yr max). |

**Pre-registered alert:** Si new_TL < 6 para cualquier símbolo, cooldown se vuelve floor-dominated → TL-cooldown decoupling parcial. Flag para review en derivation_audit.md. Operator decide si:
- (a) aceptar floor-dominated cooldown (sub-window validation arbitrará si gates funcionan así).
- (b) considerar new_TL anómalamente corto como señal de "este symbol no es compatible con la strategy time horizon" — opt para excluir del Phase 2-R2 tests.
- (c) re-derivar cooldown independientemente con su propio anchor (e.g., ATR-time-to-mean-revert).

Mi recomendación: ir con (a) por simplicidad. Si floor-domination produce R2-fail para esos símbolos, escalar entonces.

### §2.4 — Tier mapping: maintain with external Binance volume verification

**Mapping actual (pre-existing, mantener):**
- `major`: BTCUSDT, ETHUSDT
- `mid`: ADAUSDT, AVAXUSDT, DOGEUSDT, UNIUSDT, XLMUSDT
- `small`: PENDLEUSDT, JUPUSDT, RUNEUSDT

**Verificación externa requerida en derivation_audit.md:**
- Cross-check contra ranking público de 24h volume en Binance Spot (no usar #281 cost-spectrum).
- Source: `binance.com/en/markets/spot` o snapshot histórico equivalente cercano al pre-holdout end (2025-04-29).
- Acceptance criterion: BTC/ETH en top-2 majors ✓; los 5 mid-cap dentro de top-30 by 24h volume ✓; PENDLE/JUP/RUNE fuera del top-30 pero presentes ✓.
- Si la verificación external NO matchea el mapping actual (e.g., RUNE saltó a top-30 → debería ser mid; o ADA bajó → debería ser small), abrir issue separado y NO promover los nuevos gates hasta resolver el mismatch. Mapping inconsistency es un structural blocker, no minor adjustment.

**Por qué no re-derivar mapping completamente:** la asignación símbolo→tier es ordinal (ranking), no cardinal. Si Binance volume data soporta el mapping actual, no hay razón para re-shuffle. Re-derivation completa sería tackling un problema que no se evidenció en el grid_topology data.

### §2.5 — Resumen ejecutable

```
For each symbol in DEFAULT_SYMBOLS:
    new_TL_raw = median(time_to_1_ATR over pre_holdout 1H bars, censored at 72h)
    new_TL = clamp(round(new_TL_raw), 4, 48)
    if outside [4,48]: log WARN

For each tier in {major, mid, small}:
    new_PoV = (30 - tier.base_bps) / tier.size_factor    # participation_ratio_per_min
    new_max_PoV = convert_to_24h_median_bar_volume_ratio(new_PoV)    # see derivation_audit.md

For each symbol:
    new_cooldown = max(new_TL, NW=4, floor=6)
    if new_TL < 6: log WARN (floor-dominated)

# tier mapping verification
verify_against_binance_public_volume_ranking()
if mismatch: open issue + halt R2 promotion
```

---

## §3 · Sub-windows specification (Option B, operator-approved)

| ID | Window | Regime characterization | Notable coverage |
|---|---|---|---|
| A | 2022-04-01 → 2022-07-01 | Bear market 2022 (Terra/Luna collapse, May 2022) | All 10 symbols except PENDLE (start 2023-07) and JUP (start 2024-01) — 8/10 cover |
| B | 2023-04-01 → 2023-07-01 | Recovery period 2023 (post-FTX low) | All 10 except JUP — 9/10 cover |
| C | 2025-01-30 → 2025-04-30 | Recent pre-holdout 3 months (post-A.4-1 train end, pre-holdout start) | All 10 cover |

**Properties:**
- Non-overlapping ✓
- All BEFORE holdout_start = 2025-04-30 ✓ (Window C ends exactly at holdout_start − 1 día; safe)
- Genuinely OUTSIDE A.4-1 train window [2024-01-30, 2025-01-30] ✓ (operator's critical fix incorporated)
- Cover ≥3 distinct regime characterizations (bear / recovery / recent)

**Per-symbol coverage table (compute durante R2 execution):**
- For each (symbol, sub_window) pair: report `bars_available`, `bars_required_for_indicators` (LRC needs 100 bars warmup), and `usable_bars`.
- If `usable_bars < threshold_for_indicator_stability`, exclude that (symbol, sub_window) pair from the test for that symbol.
- Specifically: JUP excluded from Windows A + B (no data). PENDLE excluded from Window A (no data).
- **Decision rule:** un símbolo participa en el conjuntivo aggregation (§4) sólo en los sub-windows donde tiene `usable_bars ≥ 500` (a conservative threshold ~3 weeks of 1H bars, comfortably past warmup).

### §3.1 · Pre-registered failure-coverage scenarios

| Scenario | Acción pre-registrada |
|---|---|
| Symbol pasa en Window C pero no en A o B | Reportar; NO promover gates para ese símbolo. Operator decide si lo trata como "regime-conditional success" o "fail". |
| Symbol pasa en A + B + C | Promote new gates for that symbol. |
| Symbol no participa en A o B (JUP, PENDLE) → solo evaluado en sub-windows disponibles | Aggregation rule §4 considera "≥6 of 8 ELIGIBLE symbols in each available sub-window". I.e., aggregation respeta cobertura. |

---

## §4 · Success criterion (aggregation rule)

**Per audit §6 R2:** ≥6 de los 8 símbolos actualmente bancarrotando (ADA, AVAX, DOGE, UNI, XLM, PENDLE, JUP, RUNE) muestran trade counts ≥30 en el window completo.

**Per audit §A.3 + operator review §4 (conjuntivo):**

> **R2 success = ≥6 of 8 currently-bankrupt symbols show trade counts ≥30 IN EACH of the 3 sub-windows.**

Detallado:
- Sub-window A: ≥6 of 8 (con JUP/PENDLE adjusted per §3.1).
- Sub-window B: ≥6 of 8 (con JUP adjusted per §3.1).
- Sub-window C: ≥6 of 8 (todos los 8 elegibles).
- Conjuntivo: las 3 conditions arriba TODAS verdaderas.

**Failure modes pre-registered:**

| Outcome | Interpretation | Acción |
|---|---|---|
| 3 of 3 sub-windows ≥6/8 with new gates | R2 success | Advance to R1 (with §A.1 TP-sensitivity re-check first) |
| 2 of 3 sub-windows ≥6/8 | R2 failure: regime-dependent | NO promote. Document which sub-window failed + why (regime characterization). Operator decides: re-derive with different anchors, or escalate to H5 |
| ≤1 of 3 sub-windows ≥6/8 | R2 strong failure | Gates re-derivation didn't rescue trade frequency. Strong signal that under-trading is NOT gate-induced but signal-induced. **Per §A.4 prior recalibration:** drop estimate <10%; consider escalating to H5 (basket re-validation) BEFORE investing in R1+R3. |
| 0 of 3 sub-windows ≥6/8 | R2 catastrophic failure | Same as ≤1, but stronger. Probably H5 escalation. |

**Audit §A.4 sub-prior re-evaluation checkpoint:** Después de R2 (regardless of outcome), update `P(R1+R2+R3 → viable strategy)` based on R2 result. Document in R2 PR description.

---

## §5 · Tightening risk: pre-registered rule

**Pre-registered:** For each symbol, define:
- `new_TL ≥ current_TL_in_config_defaults_json` → gates relaxed for this symbol. Eligible for R2 success criterion.
- `new_TL < current_TL_in_config_defaults_json` → gates **tightened** for this symbol. **Flag as "R2 inconclusive for this symbol"** because the hypothesis of "under-trading by over-restrictive gates" is not testable here (the new gates aren't more permissive).

**Implication:** A tightened symbol cannot count toward the "≥6 of 8" success criterion in §4 unless explicitly justified. Operator option:
- (a) Replace the tightened symbol's contribution with "neutral" (i.e., neither success nor fail toward aggregation).
- (b) Treat tightened as a positive signal (gates are now closer to A-C/ATR theory, even if more restrictive) and include in success criterion.

**My recommendation: (a) for conservatism.** A tightened symbol's failure to reach ≥30 trades cannot disambiguate "signal sin edge" from "gates too tight even after R2". Excluding it from the conjuntivo preserves R2's discriminative power.

### §5.1 · Degenerate case guard (operator-locked 2026-05-11)

**Pre-registered safeguard:** If `tightened_count ≥ 5` (i.e., ≥5 of 8 currently-bankrupt symbols have `new_TL < current_TL` and are excluded from the conjuntive per §5), R2 is declared **INVALID as test of the hypothesis**, NOT passed-by-default.

**Rationale:** the conjuntive aggregation "≥6 of 8 in EACH sub-window" cannot apply vacuously over <3 remaining eligible symbols. Without this guard, universal tightening would silently produce a false positive (e.g., "1 of 1 remaining symbols passes = 100% success").

**Branches on degenerate case:**
- (a) Re-do R2 with modified methodology — e.g., constrain `new_TL_constrained = max(new_TL_raw, current_TL)` so the ATR derivation only relaxes, never tightens. Operator-approval required for re-run.
- (b) Advance to R1/R3 with explicit acknowledgment that the gates question remains unresolved within Phase 2. H7 from the audit stays unresolved; #317 stays open.

Operator decides between (a) and (b) when the guard fires.

**Enforcement in `tools/r2_gates_rederivation.py`:** before invoking ANY sub-window sweep, the script checks `tightened_count`. If ≥5, the script:
1. Emits `data/retune/2026-05-11-r2-gates/degenerate_guard_fired.txt` with the per-symbol comparison `(current_TL, new_TL_raw, tightening_flag)`.
2. Aborts the sweep with non-zero exit code.
3. Logs `R2 ABORTED — degenerate case (tightened_count={N}/8) — see degenerate_guard_fired.txt`.

No "salvar" the sweep with post-hoc adjustments (per operator instruction).

---

## §6 · Deliverable structure

After operator approval of this pre-reg:

```
data/retune/2026-05-11-r2-gates/
├── derivation_audit.md       # math + anchors + per-symbol per-tier outputs + tier mapping verification
├── per_symbol_gates.json     # drop-in for config.defaults.json:symbol_overrides (NOT yet promoted)
├── manifest.json             # cutoff, code_commit, leakage_check, sub_windows, ...
├── tl_distributions.json     # per-symbol full ATR-time-to-1-ATR distribution (for forensics)
├── q2_grid_topology_A.json   # grid topology run, sub-window A, gates = new
├── q2_grid_topology_B.json   # sub-window B
├── q2_grid_topology_C.json   # sub-window C
└── README.md                 # summary + R2 success/fail verdict + sub-window-by-sub-window table
```

Plus:
- `tools/r2_gates_rederivation.py` — reproducible script that emits all the above artifacts.
- Update `docs/superpowers/specs/es/2026-05-11-strategy-structural-audit.md` §10 history table con R2 outcome.
- PR comment con (a) verdict table per §4, (b) updated prior estimate per §A.4 checkpoint, (c) next-step recommendation (R1 advance / H5 escalate / etc.).

---

## §7 · What this pre-reg does NOT cover

- **`config.defaults.json` promotion** — pre-reg only commits to derivation methodology. Whether new gates get promoted depends on R2 success criterion + operator decision.
- **R1 (dynamic exit) implementation** — Phase 2 R1 is a separate workstream, depends on R2 outcome per §A.1.
- **A.4-1 ATR re-sweep with new gates** — happens AFTER R2 success + operator approval, in step 4 of Phase 2 success path (audit spec §A.5). Not in R2's scope.
- **Cost model v2 migration** — H8 of audit spec. Out of R2 scope (different epic).

---

## §8 · Pre-registered decision branches

Resumen de las 4 branch points donde la metodología tiene rule explícita:

| Branch point | Rule | Reference |
|---|---|---|
| Tightening (new_TL < current) | Exclude symbol from §4 conjuntivo (mark "inconclusive") | §5 recommendation (a) |
| **Degenerate tightening (≥5 of 8 tightened)** | **R2 declared INVALID; abort sweep; emit `degenerate_guard_fired.txt`; operator decides re-do (a) or advance (b)** | **§5.1 (operator-locked safeguard)** |
| Pathological TL value (< 4h o > 48h pre-clamp) | Clamp + WARN log; symbol still eligible for §4 | §2.1 |
| Tier mapping verification fails | Halt R2 promotion; open separate issue | §2.4 |
| R2 failure (≤2 of 3 sub-windows) | Document + escalate per §4 table | §4 + §A.4 prior re-eval checkpoint |
| Floor-dominated cooldown (new_TL < 6h) | WARN log; aceptar floor-dominated for now | §2.3 |
| Symbol con `usable_bars < 500` en un sub-window | Excluir ese símbolo de ese sub-window en aggregation | §3.1 |

Cada branch point tiene rule pre-registered ANTES de ver el data, eliminando rationalización post-hoc.

---

## §9 · Operator confirmations (2026-05-11) — open questions resolved

Las 3 open questions de la iteración anterior fueron resueltas por operator review (2026-05-11) + 1 safeguard adicional añadido:

1. **Cooldown floor-dominated cases: OK as-is.** Transitive rule `max(new_TL, NW=4, floor=6)` preserva design intent original. Re-derivación independent es follow-up post-R2 si necesario, no scope de este PR.

2. **NW=4 provenance: OK investigar durante execution.** Se documenta resultado en `derivation_audit.md`. Si hay anchor teórico (paper/convención) → documentar; si es operator-chosen sin trazabilidad → flag como deferred follow-up, no bloquea R2.

3. **Tightening exclude "inconclusive": APPROVED + new safeguard §5.1.** Regla locked: tightened symbols (`new_TL < current_TL`) excluded del conjuntive. **Plus** degenerate guard: si ≥5 of 8 tightened, R2 abort (no false-positive vacuous pass). Ver §5.1 + §8 decision branches table.

**Status:** ready for R2 execution. Next commits sobre #324:
- `tools/r2_gates_rederivation.py` (deriva TL+PoV+cooldown; enforces §5.1 guard ANTES del sweep)
- Actual derivación per symbol
- Sweep over 3 sub-windows (A, B, C)
- `derivation_audit.md` con math + NW=4 provenance + tier verification
- `per_symbol_gates.json` (drop-in para `config.defaults.json:symbol_overrides`, NOT yet promoted)
- README con verdict + interpretación
- PR comment con conjuntive table + degenerate guard status + prior re-eval per §A.4

---

## §10 · Historial

| Fecha | Cambio | Autor |
|---|---|---|
| 2026-05-11 | Pre-reg sub-spec inicial (post-operator-review methodology v2) | Claude Opus 4.7 + sssamuelll |

Reservar líneas para iteración del pre-reg si necesario antes de R2 execution.
