# Kickoff — Revisión externa A.4 hallazgo de inflexión metodológica

**De:** sssamuelll (operador)
**Fecha:** 2026-05-11
**Tipo:** solicitud de revisión meta-metodológica (no code review)
**Estado del proyecto:** PAUSADO en este nodo — no se mergea código ni se ejecuta nada irreversible hasta tener tu feedback.

---

## TL;DR (60 segundos)

Llevo semanas haciendo correcciones estructurales al simulador de backtest de trading-spacial para que refleje condiciones live-equivalent. Hoy corrí los dos sweeps pre-registrados sobre el pre-holdout window con todas las correcciones activas. Ambos retornan, en lectura literal, **"no hay edge demostrable en el grid disponible para ningún símbolo del basket curado de 10 monedas"**. Antes de quemar la bala única del A.4-3 holdout o tomar cualquier otra acción, necesito que valides la cadena meta-metodológica que me trajo hasta acá.

---

## Tu trabajo

**Objetivo:** validar (o invalidar) tres decisiones meta-metodológicas, en orden de prioridad.

**Deliverable:** respuestas escritas a las 3 preguntas críticas (§2). Formato libre — markdown, doc, email, audio, lo que te resulte. Cualquier extensión es válida, desde "estoy de acuerdo con X, no con Y porque Z" hasta análisis multipágina.

**Tiempo estimado:**
- **30-45 min** si solo respondés las 3 preguntas críticas con tu mejor judgement.
- **2-3 horas** si querés engagement completo con el spec central + las 7 preguntas en §7 de ese spec.
- **Más** si querés inspeccionar código o re-correr alguna parte del análisis.

Cualquier nivel ayuda. **No necesito perfección; necesito un segundo par de ojos independiente.**

---

## Background — el proyecto (1 párrafo)

trading-spacial es un sistema automatizado de señales de trading BTC/USDT con análisis multi-timeframe sobre un basket curado de 10 monedas. Backend Python (FastAPI), frontend React/TypeScript. La estrategia genera señales, las puntúa, y emite alertas — la entrada/salida es operator-gated (humano aprueba via CLI/frontend, Telegram solo outbound). La meta del epic A (paraguas) es validar honestamente la estrategia sobre un dataset holdout locked antes de invitar usuarios adicionales a la plataforma.

## Background — el epic A.4 (1 párrafo)

A.4 es la fase de re-tune pre-holdout. El holdout es bala única — se corre **una sola vez** sobre 12 meses de datos OHLCV que fueron locked en `data/holdout/` el 2026-04-30. Cualquier ajuste post-holdout sobre asunciones del análisis invalida la prueba como validación primaria. A.4-1 (ATR) y A.4-1.5 (regime thresholds) son sub-fases que re-tunean parámetros sobre `[earliest, holdout_start − 1 bar]` para evitar el leakage de los valores actuales (que fueron tuneados sobre history que incluye el holdout window). A.4-2 es walk-forward sobre el pre-holdout. A.4-3 es la corrida final contra el holdout.

## Background — qué cambió antes de hoy (3 bullets)

1. **#223 / #224 — phantom-profit fix.** Antes, un bug de signo en `_close_position` convertía losing trades en `pnl_usd` positivos cuando el SL estaba mal estructurado. Los backtest numbers en `2026-04-17-formula-ganadora-resultados-finales.md` (citados como "real strategy contribution") están todos pre-fix y están inflados.
2. **#309 — K=10 overshoot cap.** Cap sobre `|pnl_pct / sl_pct_actual|` en `_close_position` para que un trade individual no pueda producir pérdida mayor a 10× el `risk_amount`. Antes, single-trade overshoots vía TIME_LIMIT exits con SL tight producían pérdidas tipo PENDLE −$1.7M en una sola trade.
3. **#313 (#280) — per-symbol bankruptcy halt.** Una vez que la equity simulada de un símbolo cae bajo `0.1 × INITIAL_CAPITAL` ($1000), el simulador emite un único `BANKRUPT` trade record y deja de procesar entries para ese símbolo. Antes, post-bancarrota el simulador seguía generando trades con `risk_amount = 0` por el floor `effective_capital = max(0, capital)` — esos zero-PnL trades inflaban aggregate trade counts y win rates.

Cada uno de estos fixes fue motivado por evidencia concreta de un sweep que halt-eó. No son fixes especulativos.

## Hoy — qué encontré (con números)

Corrí los dos sweeps pre-registrados con cutoff `2025-04-30T00:00:00 UTC`.

**A.4-1.5 regime threshold sweep** (`tools/regime_retune_pre_holdout.py`, ~10 min):
- Exit code 3 (sanity halt: `no_detector` gana).
- Per-config aggregate `sum(net_pnl)`: `60_40: −$99,550 | 70_30: −$99,536 | 80_20: −$99,553 | no_detector: −$97,408`. Margin winner-runnerup = 2.18%.
- Per-symbol: cada uno de los 10 símbolos bottoms out en ~$−9K (initial $10K − bankruptcy floor $1K). PENDLE satura en $−15K, JUP en $−12K vía K=10-capped overshoots inmediatamente antes del bankruptcy halt.
- Trade count total: **1,840 (vs 21,193 en el mismo sweep el 2026-05-06 cuando #280 no estaba mergeado)**. Drop del 92% — los ~19K trades eliminados son exactamente los post-bankruptcy fictional zero-PnL trades que #280 ahora detiene. Esa es la prueba empírica directa de que #280 funciona.

**A.4-1 ATR re-tune sweep** (`tools/retune_pre_holdout.py`, ~36 min con `cpu_count()` workers):
- Exit code 0, pero `recommendation: NO_DATA` para los 10 símbolos.
- `NO_DATA` significa: `top_candidates[0]["pnl"] ≤ 0` — la **mejor** de las 105 combinaciones del grid `(sl, tp, be)` produce P&L no-positivo de train para ese símbolo.
- Grid: `sl ∈ {0.5, 0.7, 1.0, 1.2, 1.5, 2.0, 2.5}`, `tp ∈ {2.0, 3.0, 4.0, 5.0, 6.0}`, `be ∈ {1.5, 2.0, 2.5}` — 7 × 5 × 3 = 105 combos.
- Train window: `[2024-01-30, 2025-01-30]` (12 meses). Validate: `[2025-01-30, 2025-04-30]` (3 meses).

**Lectura combinada:** bajo simulación live-equivalent (gates time-limit + participation-cap + bankruptcy halt activos), **ninguna** dimensión sola (ATR dentro del grid, regime dentro de {60_40, 70_30, 80_20, no_detector}) produce P&L positivo en el pre-holdout window para ninguno de los 10 símbolos.

## Comparativa contra el sweep del 2026-05-01 (que mostraba edge)

El sweep ATR del 2026-05-01 (corrido por Gemini durante un período donde yo no tenía acceso a Claude por límite de uso) reportó CHANGE/KEEP recommendations con improvements positivos (BTC +$1,296, ETH +$562, UNI +$1,893, etc.). La diferencia con hoy es exclusivamente el path del simulador:

| Aspect | 2026-05-01 (Gemini) | 2026-05-11 (hoy) |
|---|---|---|
| Path simulador | legacy `atr_*` kwargs | `cfg` + `symbol_overrides` |
| Time-limit barrier | **bypassed** | **active** |
| Participation cap | **bypassed** | **active** |
| Bankruptcy halt (#280) | not in main yet | **active** |

El path legacy bypassea tres gates live-relevantes — CLAUDE.md flagea esto como "decisión de diseño legacy para callers que no opt-in a `symbol_overrides`". PR #287 (el harness) mergeó con el path legacy; el post-merge review (yo, mismo día) flageó esto como bug de comparabilidad vs el harness regime (`tools/regime_retune_pre_holdout.py`, #306) que usa el path standard. El fix shipped en commits `06fcd02` + `7fef45c` forza el path standard cuando `cutoff` y `app_config` están seteados. **El sweep de hoy es el primero que mide la estrategia bajo condiciones live-equivalent.**

---

## §1 — Por qué necesito revisión externa

Por dos razones:

1. **A.4-3 es bala única.** Si la decisión "correr A.4-3 vs pausar" está mal tomada, no se puede rebobinar. Quiero un segundo par de ojos en esa decisión específica.

2. **Mi cadena de pre-registrations ha sido auto-revisada.** Cada uno de los fixes estructurales (#223/#224, #309, #313) fue revisado por mí + ocasionalmente por un agente de code-review automatizado. Ninguno por un revisor humano independiente. Es plausible que haya un sesgo sistemático en mi forma de razonar sobre el sistema que esté contaminando todas las decisiones. Si lo hay, ahora es el momento de detectarlo — antes de quemar la bala única.

---

## §2 — Las 3 preguntas críticas

Si solo tenés 30 minutos, contestá estas. (Las 7 completas están en el spec central, §7.)

### Pregunta 1: ¿`cfg + symbol_overrides` es el path correcto para el ATR re-tune?

**Setup:** Mi fix forzó al harness ATR a usar el path standard (`cfg + symbol_overrides`) que activa time-limit + participation-cap gates. El path legacy `atr_*` kwargs bypassea esos gates por diseño.

**Argumento pro path standard (lo que hice):**
- Sin gates activos, tuneamos sobre un mundo que no existe en prod. Los params propuestos no transferirían.
- Comparabilidad con el harness regime exige misma realismo.
- La estrategia VIVE con esos gates en prod, así que tunear sin ellos es self-deception.

**Argumento pro path legacy (lo que NO hice):**
- ATR multipliers son una propiedad intrínseca de la estrategia (controlan SL/TP/BE distance en términos de ATR). Otros gates (time-limit, participation-cap) son layer encima.
- Quizás el orden correcto es: tunear ATR primero AISLADO (sin gates), después tunear gates con ATR fijo, después re-tunear ATR con gates activos. Coordinate descent.
- Si tuneás todo simultáneamente bajo gates, el tuner puede no encontrar el óptimo conjunto porque los gates restringen demasiado del search space.

**Lo que necesito de vos:** ¿Cuál de estos dos argumentos te parece más sólido? Si el legacy path es defendible para ATR tuning specifically, mi finding de hoy se invalida y necesito re-correr (y eso es buenísimo saberlo antes de A.4-3).

### Pregunta 2: ¿El grid actual es suficiente, o necesitamos expandir antes de aceptar NO_DATA?

**Setup:** Grid actual: 7 × 5 × 3 = 105 puntos. `sl ∈ [0.5, 2.5]`, `tp ∈ [2, 6]`, `be ∈ [1.5, 2.5]`. NO_DATA significa "ninguno produce P&L positivo".

**Argumento pro expandir:**
- Si el óptimo real está en `sl ≥ 3` (super wide SL) o `tp ≤ 1` (super tight TP) o ambos, no lo veríamos.
- Una sweep con grid 5-10x más amplio costaría ~24h de compute paralelizado — barato comparado con quemar la bala única.

**Argumento contra expandir:**
- 105 puntos es bastante denso. Si NINGUNO se acerca a positivo, expandir probablemente no rescate.
- Risk de overfitting si encontramos un solo punto positivo en un grid grande.
- ¿Cuándo paramos? Si grid 5x falla, ¿probamos 10x? ¿Bayesian optimization? El espacio puede expandirse indefinidamente.

**Lo que necesito de vos:** ¿Expandimos primero o aceptamos NO_DATA como suficiente? Si expandimos, ¿cuál es tu sugerencia de rangos? Y crucialmente: ¿qué constituiría "evidencia suficiente de no-edge" para vos?

### Pregunta 3: ¿Vale la pena correr A.4-3 (holdout, bala única) ahora?

**Setup:** A.4-3 corre la estrategia con params actuales sobre el holdout window `[2025-04-30, 2026-04-30]`. Bala única.

**Argumento pro correrlo ya (Opción D del spec):**
- Pre-registrar el finding como predicción: "el holdout mostrará el mismo patrón (universal bankruptcy / NO_DATA)".
- Si confirma, tenemos el ground truth pre-registered, no post-hoc rationalización.
- Si NO confirma, descubrimos algo importante (¿por qué difiere holdout de pre-holdout?) — pregunta nueva que merece investigación.

**Argumento contra correrlo ya (Opción A del spec):**
- Si ya predecimos resultado con alta confianza, quemamos la bala por nada.
- Mejor primero resolver §2.1 (¿gates correctos?) y §2.2 (¿grid suficiente?). Si esas resoluciones cambian el predictor, A.4-3 se vuelve más informativo.

**Argumento intermedio (Opción B del spec, mi sesgo actual):**
- Expandir grid primero (~24h compute). Si NO_DATA persiste, refuerza el predictor.
- Después correr A.4-3 con pre-registration explícito de la predicción "NO_DATA persiste en holdout".

**Lo que necesito de vos:** ¿Cuál de estos tres caminos es más sólido? Si elegís D directamente o cambias el orden propuesto en B, ¿por qué?

---

## §3 — Material adicional si querés profundizar

**Spec central** (lectura completa ~45 min):
`docs/superpowers/specs/es/2026-05-11-a4-hallazgo-inflexion-metodologica.md`
9 secciones. Las 7 preguntas en §7 incluyen las 3 de arriba más:
4. ¿Qué constituye formalmente "edge demostrable" para esta estrategia? (Sharpe? PF? DSR? Win rate?)
5. ¿Es defendible NO promover ningún cambio de params dado este finding, o el operador debe tomar la decisión de mantener los valores actuales pre-leakage como "best guess available"?
6. Si el finding se confirma, ¿qué implicaciones tiene sobre el guardrail #271 (sin nuevos usuarios hasta epic A pase validación)?
7. ¿Hay algún experimento simple que pudiera reabrir el espacio antes de cerrar?

**Pre-registrations existentes** (lectura para contexto, ~30 min):
- `CLAUDE.md` — secciones "Validation Methodology" y "Caveats heredados — A.4 (#250)" #1, #4
- `docs/superpowers/specs/es/2026-05-03-asunciones-tecnicas-pre-holdout.md` (Decisión 9) — los pre-registers que estábamos siguiendo

**Evidencia cruda** (~15 min):
- `data/retune/2026-05-11-pre-holdout-regime-evidence/README.md` + `halted_summary.json` — regime sweep
- `data/retune/2026-05-11-pre-holdout-atr-evidence/README.md` + `report.md` + `manifest.json` — ATR sweep

**Código** (profundización opcional):
- `backtest.py` — buscar `_close_position`, `BANKRUPTCY_THRESHOLD`, `MAX_OVERSHOOT_RATIO`
- `tools/retune_pre_holdout.py`, `tools/regime_retune_pre_holdout.py` — harnesses
- `auto_tune.py:177` (`run_backtest_with_params`) — donde está el path-switching

**Pull requests relevantes:**
- #309 — K=10 cap
- #313 — bankruptcy handler (con mi plan ejecutable en `docs/superpowers/plans/`)
- #287 — A.4-1 ATR harness (con post-merge fix `06fcd02` + `7fef45c`)
- #315 — regime sweep evidence
- #316 — A.4 inflection-point spec (este package)

---

## §4 — Lo que NO necesito de vos

- No necesito que valides los fixes estructurales individualmente. Si ya participaste en review de #309, #313 o #287, asumimos que esas decisiones eran sólidas en su momento. Lo que necesito es revisión **meta-metodológica** sobre el orden de operaciones y la interpretación de hoy.
- No necesito que ejecutes nada en mi infra. Si querés re-correr algo, dame guía y lo hago yo (o lo dejamos pendiente).
- No necesito que tu respuesta sea formal o pulida — pseudo-código, bullet points, voz/audio funciona.

---

## §5 — Cómo entregar feedback

Cualquiera de estos formatos:
- **Comentario en el PR #316** (`docs/methodology-inflection-2026-05-11`) — preferido si querés que quede traceable en el repo
- **Email a samueldarioballesteros@gmail.com** — si preferís texto plano y reply-chain
- **Slack / DM directo** — si tenemos canal compartido y querés algo más rápido
- **Audio (voice memo)** — si te resulta más ágil que escribir; te transcribo yo

Si dejás comentario en el PR, podés usar review comments anclados a líneas específicas del spec — eso me ayuda a saber a qué te referís sin ambigüedad.

---

## §6 — Timeline expectation

**No urgente.** El proyecto está pausado en este nodo. No hay deadline forzoso, no hay nadie esperando código.

Idealmente respuesta dentro de **1-2 semanas** para no perder momentum, pero si necesitás más tiempo decímelo y esperamos. Mejor revisión lenta que decisión apresurada.

---

## §7 — Mi sesgo declarado (transparency)

Para que tengas cuidado al leer mi argumentación:

1. **Tengo mucho tiempo invertido en este proyecto** — varias semanas de iteración, muchas correcciones estructurales. Sesgo natural a defender que las correcciones valieron la pena (e.g., interpretar el finding como "metodológicamente clean" en vez de "el proyecto no tiene edge"). Cuestioná esto.

2. **Mi recomendación preliminar es B → D** (expandir grid después correr A.4-3 con predicción). Esto es lo que YO haría sin tu input. Es perfectamente válido que vos digas "no, opción A, parar ahora" o "no, opción D directo sin expandir grid" — y eso me dará información que mi cadena de razonamiento auto-cerrada no me da.

3. **Estoy operando con la asistencia de Claude** (modelo de Anthropic) como reviewer agent. Los specs, evidencia y planes están escritos en colaboración con él. Cualquier sesgo del modelo está embedded en cómo te estoy presentando esto. Si percibís un sesgo sistemático en la forma en que está framed, decímelo — eso es exactamente el tipo de meta-observación que necesito.

4. **El #271 guardrail** (no invitar usuarios hasta validar A) significa que un finding de "no edge" tiene consecuencias materiales sobre el negocio. No quiero que eso me empuje a sub-interpretar el finding. Cuestioná si lo estoy haciendo.

---

Gracias por aceptar (o considerar).

— sssamuelll
