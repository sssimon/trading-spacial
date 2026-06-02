# Cost-model v3 — diseño (two-body upper bound)

**Fecha:** 2026-06-02
**Estado:** diseño aprobado, listo para plan de implementación.
**Capa:** Capa 0 de la descomposición de "encontrar edge" (precondición para edge-search y para cualquier validación del kill-switch). El holdout falsification gate (Capa 2, PR #553) ya aterrizó; este es el instrumento que ese gate eventualmente disparará.
**Archivo gobernado:** `costs_calibration.json` es production-governing (CLAUDE.md Non-Negotiable #6).

**Verificado contra el repo** (no asumido): `backtest_costs.py`, `costs_calibration.json`,
`backtest.py:495-540 / :737 / :1003`, `tools/cost_diagnosis/recompute.py:13/34/39`,
`tests/test_backtest_costs_v2.py:404-526`, `.mex/context/architecture.md:84-90`,
`data/retune/2026-06-01-base-edge-diag/FINDINGS.md`.

---

## 0. Marco ontológico (REFRAMES — no se relitigan)

Dos reencuadres gobiernan el diseño. No son opiniones a debatir en implementación; son los tipos de los objetos involucrados.

- **R1 — El modelo de costos es una COTA SUPERIOR intencional, no un estimador.**
  No se "valida" contra el P&L realizado en vivo; solo se puede *falsificar como cota*.
  Una cota por encima de lo observado es el spec **cumpliéndose**, no fallando. El fallo
  real de v2 no es "el número está 30-40× alto" — es que la cota se aflojó tanto que
  **invierte conclusiones de signo** per-símbolo (AVAX-short: backtest −$7,345 bancarrota
  vs live +$35 mejor performer). Un instrumento de seguridad empezó a producir afirmaciones
  de edge: **violación de capa.** El P&L live es un **techo de sanidad (sanity ceiling),
  nunca un fit target.** Como `net = gross_edge − cost`, los 27 trades en vivo NO aíslan
  slippage; costo y edge están **mutuamente identificados** con data de un solo régimen, así
  que ajustar el costo a live equivale a asumir la respuesta a "¿hay edge?", que es la
  pregunta que el costo debía ayudar a responder. Un estimador empírico insesgado (de fills
  reales) es un objeto **FUTURO y SEPARADO** que vive AL LADO de la cota, nunca la reemplaza
  (epic distinto, NO es v3).

- **R2 — La ley de raíz cuadrada es de respuesta del libro a FLUJO SOSTENIDO.**
  Tóth (2011 PRX 1:021006), Donier-Bonart (2015 MM&L 1(02)), Almgren-Chriss (2001) derivan
  `impact ≈ Y · σ_daily · sqrt(Q / V_daily)` para metaorders ejecutados gradualmente a lo
  largo de horas; la raíz emerge del **tiempo** (integral de reposición-vs-consumo del libro),
  no del tamaño. La estrategia dispara una orden de mercado casi-instantánea de ~$644. Su
  dominio operativo de participación es ~`1e-7..1e-4`; el dominio donde la ley fue validada es
  `1e-3..1e-1`. La raíz va reservada al **tail**, donde de hecho aplica; el cuerpo dominante
  del costo en el régimen operativo es **spread + fee**, dos términos que la ley sqrt no
  contiene.

**Tie-break explícito en todo el diseño: conservadurismo (cota) sobre acuerdo (estimación).**

---

## 1. Forma — cota de dos cuerpos

```
cost_bps = FLOOR(spread + fee + funding)  +  TAIL(impact)   ; luego TOTAL-cost cap
```

- **FLOOR = el CUERPO.** La cota dominante en el régimen operativo. Size-**independiente**
  (spread + fee + funding). Calibrado de forma **independiente** y conservadora a partir de
  hechos públicos de Binance USDT-M perp. **No se ajusta a live.** Hoy está disfrazado de
  `base_bps` constante.
- **TAIL = el GUARDARRAÍL.** Size-**dependiente**; física sqrt en base **diaria**; ancla
  **desacoplada** del floor. Inerte (dominado por el floor) en el trade operativo promedio;
  guardarraíl real en el régimen validado para fills grandes en alts finos. El defecto raíz
  de v2 era amarrar floor y tail en un solo punto de calibración (anchor-parity-con-v1) — el
  único punto donde la mezcla es indistinguible. v3 los desacopla estructuralmente.

**La garantía de "esto es una cota" recae en TRES guardianes, NO en la dominancia floor>tail:**
(1) no-negatividad de cada término, (2) el TOTAL-cost cap, (3) `stress_mult`. La dominancia
floor>tail es narrativa descriptiva del trade promedio, **no** una prueba de seguridad y no
se cita como tal en ninguna parte de la spec ni del código.

---

## 2. FLOOR spec (el cuerpo)

```
FLOOR_bps[tier] = stress_mult[tier] * (2*half_spread_bps[tier] + 2*fee_bps_per_side[tier])
                + funding_bps(holding_hours, funding_rate_bps_per_8h[tier])
```

| tier  | half_spread (bps) | fee/side (bps) | RT floor spread+fee | funding (bps/8h) |
|-------|-------------------|----------------|---------------------|------------------|
| major | 1.5               | 5.0            | 13.0                | 1.0              |
| mid   | 4.0               | 5.0            | 18.0                | 2.0              |
| small | 10.0              | 5.0            | 30.0                | 5.0              |

**`half_spread` major = 1.5 (no 1.0):** con 1.0 el RT floor sería 12.0, **dentro** de la banda
live observada 5-15 bps — el floor del tier más operado podía quedar *por debajo* de un costo
real major de 13-15 bps (violación de cota). 1.5 → RT 13.0 lo deja en el borde superior.
Honestamente: el floor major sigue siendo "comparable al techo", no holgadamente por encima;
mid (18) y small (30) sí acotan limpio. Esto se documenta, no se esconde.

**Fuentes (por parámetro, sin lavado):**
- **half_spread** = cuartil conservador del spread top-of-book público de Binance USDT-M perp,
  redondeado hacia arriba sobre el típico (BTC/ETH ~0.1bps típico → 1.5; mid ~1-4 → 4.0; small
  ~5-10 → 10.0). **major queda IGUAL que v2 (1.5 → 1.5)** — ya estaba en el cuartil perp, no había
  colchón spot que quitar. **Solo mid (7.5→4.0) y small (15→10) bajaron** — v2 les cargaba un
  colchón spot no físico para perps. Cada uno queda por encima del spread perp típico, así que
  sigue siendo cota. (NO afirmar "todos menores que v2": major no cambió.)
- **fee_bps_per_side = 5.0 = taker ESTÁNDAR publicado de Binance USDT-M (0.05%), sin VIP/BNB/maker.**
  Re-citado honestamente: el 0.04% del JSON v2 es la tasa con descuento BNB; el taker estándar
  no descontado **es** 0.05% = 5.0 bps. Es el costo determinístico exacto, **sin colchón**. v2
  usaba 10.0 (la inflación no física más floja); v3 la quita. La spec NO afirma "5.0 está 1bp
  por encima del real 4".
- **funding** = **valor** sin cambios vs v2 (1.0/2.0/5.0 bps/8h, estimado absoluto conservador,
  semántica floor por intervalo de 8h). Es **estructuralmente cero en el régimen operativo** (hold
  medio 5.3h → `floor(5.3/8)=0`); no es un componente load-bearing del cuerpo dominante, es un
  add-on que solo dispara en holds ≥8h. **Pero el EFECTO en backtests LRC sí cambia:** hoy el path
  LRC excluye funding (`_costs_active` sin `enable_funding`, `:1003`); v3 lo activa en ambos paths
  (§8), así que los números LRC v2-priced no son comparables en el eje funding. "Sin cambios"
  aplica al valor del parámetro, no al efecto en LRC.
- **stress_mult** = dial de pesimismo del operador para corridas de stress-replay (LUNA/FTX).
  **Default 1.0.** Es el ÚNICO lever que ensancha la cota bajo un evento de volatilidad/liquidez,
  porque ningún cuerpo responde a σ por sí solo. **No se ajusta a datos.**

**Disciplina (load-bearing):** el floor es **por-tier** y el harness DEBE indexarlo por el tier
real de cada trade. Aplicar el floor small (30) uniforme a todos los trades barre la inversión
bajo el supuesto más pesimista y miente sobre el tier major. El floor **NO se baja** hacia el
live 5-15bps para "arreglar el overcharge" — eso lo convierte en estimador puntual del único
régimen muestreado (la muerte más probable identificada por el failure-mode analyst).

---

## 3. TAIL spec (el guardarraíl)

```
tail_bps_per_fill = Y * sigma_daily_bps[tier] * sqrt( max(order_usd / V_daily_usd, 0) )
V_daily_usd       = liquidity_usd_per_min * 1440     # eleva el proxy per-min (backtest.py:1019) a base DIARIA
tail_round_trip   = tail(entry) + tail(exit)
```

Single sqrt continuo, monótono, `tail(0)=0`. Sin blend suavizado (puede hundirse y subcobrar),
sin switch duro (su única forma segura es un salto hacia ARRIBA).

**Veredicto físico (honesto):** la estrategia dispara market orders one-shot, **no** metaorders,
así que sqrt es la ley física *equivocada* para el mecanismo de fill. Pero el requisito es una
**cota no-subestimante**, no una descripción fiel del fill. Se **elimina** la afirmación vieja
"sqrt(p) > p ⇒ sqrt over-bounds el barrido lineal" (era un truco de prefactor: comparaba la
función con sí-misma-por-p, no con el costo de un book-sweep lineal `k_linear·p`, cuya condición
`Y·σ·sqrt(p) ≥ k_linear·p` no está demostrada). Justificación de sqrt: es la ley validada del
dominio, extrapolada conservadoramente hacia abajo (por debajo del dominio validado el tail es
despreciable frente al floor de todos modos); la garantía de cota recae en los tres guardianes
(§1), no en la forma funcional.

**Parámetros:**

- **Y = 1.5** (tope del band empírico O(1) = 0.3-1.5; Tóth 2011, Donier-Bonart 2015 — los MISMOS
  papers que v2 citaba, ahora correctamente asignados SOLO al tail). **Y se fija por coherencia
  de tipo, no por preferencia:** un objeto de tipo cota se define por su modo de falla (se
  equivoca hacia arriba); de un band de incertidumbre, solo el **borde superior** preserva el
  tipo cota — el resto del band son valores que un *estimador* tomaría. Un Y central (1.0) hace
  del tail un estimado central con etiqueta de cota = la inversión de signo de R1 reencarnada en
  el tail. La "inertness" del tail (tail << floor en operativo) **no tiene autoridad sobre Y**:
  ya fue jubilada como argumento de seguridad (§1), así que no restringe el valor. Consecuencia
  aceptada explícitamente: a $644 el tail small es ~60% del floor — **eso no es un costo, es el
  guardarraíl reportando que el order ya no es pequeño respecto al libro de un alt fino.**
- **sigma_daily_bps = {major 300, mid 500, small 800}** como fallback estático conservador para
  el lanzamiento de v3. **Fast-follow:** reemplazar por stdev rolling 30d de retornos diarios
  por símbolo (auto-escala con el régimen de vol de cada símbolo). El supuesto estático se
  documenta explícitamente para que nadie lea 300/500/800 como verdad medida.

**La reducción del tail vs v2 vive en DOS cambios — y la spec debe decir ambos:**
`sqrt(1440) = 37.95×` (corrección de base per-minute → diaria) × `size_factor/(Y·σ)` (cambio de
**fuente** de la pendiente: de un `size_factor` anchor-parity arbitrario a `Y·σ` fuenteado de la
literatura). **A Y=1.5** (el valor de lanzamiento) el factor de pendiente es major
`885.44/(1.5·300) = 1.97×`, mid `1423.02/(1.5·500) = 1.90×`, small `2055.59/(1.5·800) = 1.71×`,
dando reducción total **major 74.7× / mid 72.0× / small 65.0×**. (A Y=1.0 el factor de pendiente
sería ~2.7-2.9× → 112×/108×/97.5×; se menciona solo para mostrar que el grueso del cambio es la
base `sqrt(1440)`, no la pendiente. Y=1.0 fue rechazado, NO se usa.) El test pinned "reducción
tail vs v2" assertea los valores **Y=1.5: 74.7/72.0/65.0**. No vender "el fix de sqrt(1440)" como
la historia completa.

**Inertness (re-definida, honesta):** NO es "<1bp absoluto en todas partes" (matemáticamente
incompatible con Y=1.5 para small). Inert = **dominado por el floor en el trade promedio ~$644**.
**A Y=1.5 (el valor de lanzamiento):** tail/floor = major **3.5%** / mid **19.7%** / small
**59.8%**. (A Y=1.0 sería 2.3% / 13.1% / 39.9%; NO se usa — Y=1.0 fue rechazado.) El test pinned
"dominancia floor del trade promedio" assertea los valores **Y=1.5**. La dominancia es propiedad
del trade operativo promedio, **no** un invariante del
modelo: a participación diaria ~`1e-2` (régimen validado) el tail mid llega a ~150 bps RT, muy
por encima del floor — ahí el tail es un guardarraíl real, como debe ser. El tamaño de orden
escala con `INITIAL_CAPITAL`, no está fijo en $644; a notional de backtest mayor el tail small
es co-dominante. Se documenta; no se cita la dominancia como prueba de seguridad.

**Desacople de ancla (verificado):** slope = `Y · σ`, producto de dos cantidades
independientemente fuenteadas, nunca resuelto para pegar un bps target, nunca atado a spread/fee.
El defecto v2 de ancla única (`size_factor = (target − base)/sqrt(0.001)`) queda roto
estructuralmente. No se introduce acople nuevo: como la dominancia floor>tail ya no es argumento
de seguridad, no crea dependencia oculta.

---

## 4. Fórmula combinada (rama v3, aditiva a v1/v2)

```python
# FLOOR (cuerpo) — size-independiente
floor_bps = stress_mult[tier] * (2*half_spread_bps[tier] + 2*fee_bps_per_side[tier])
floor_bps += compute_funding_cost_bps(                      # keyword-only (líder * en :176)
    holding_hours=holding_hours,
    funding_rate_bps_per_8h=funding_rate_bps_per_8h[tier],
)

# TAIL (guardarraíl) — size-dependiente, base DIARIA, ancla desacoplada, single sqrt continuo
V_min_per_day = calib.global.v_daily_minutes_per_day         # 1440, leido del JSON, NO hardcoded
V_daily_entry = entry_liquidity_usd_per_min * V_min_per_day
V_daily_exit  = exit_liquidity_usd_per_min  * V_min_per_day
tail_entry = Y * sigma_daily_bps[tier] * sqrt(max(entry_notional_usd / V_daily_entry, 0))
tail_exit  = Y * sigma_daily_bps[tier] * sqrt(max(exit_notional_usd  / V_daily_exit,  0))
tail_bps   = tail_entry + tail_exit

total_cost_bps = floor_bps + tail_bps

# TOTAL-cost cap (RE-SPEC: aplica al TOTAL round-trip, NO por leg como v2)
cap_hit = (total_cost_bps >= TOTAL_COST_CAP_BPS)             # antes del recorte; robusto a float (no ==)
total_cost_bps = min(total_cost_bps, TOTAL_COST_CAP_BPS)     # 1000.0

total_cost_usd = total_cost_bps * 0.5*(entry_notional_usd + exit_notional_usd) / 10_000
```

**Cap re-spec (verificado):** en v2 el cap 500 se aplica POR LEG de slippage
(`backtest_costs.py:171`) y luego spread+fee se SUMAN encima (`:349-351`) — nunca fue un techo
total. v3 lo re-especifica como **cap del TOTAL round-trip** = `TOTAL_COST_CAP_BPS = 1000.0`, por
encima del slippage de evento peor-caso plausible (LUNA/FTX small-cap se ejecutan a 8-15%+ adverso,
así que un cap total de 500 SUB-acotaría justo en el evento para el que existe el cap).
Explícitamente un **backstop de pesimismo de backtest**, NO una afirmación física de "5% adverso".
El ensanchamiento de stress vive en `stress_mult` (lever calibrado), no en el cap. El
`EXTREME_PARTICIPATION_CAP_BPS = 500.0` módulo-nivel se queda intacto para la rama v2.

**Fallback de liquidez (re-definido para que componga):** el escalar standalone 100bps de v2 NO
compone (un bar muerto podía salir más barato que un bar fino vivo una vez que el tail puede
exceder el fallback). El fallback es **por-leg**. Cuando la liquidez de un leg es
no-positiva/no-finita, ese leg ENTERO (su spread-leg + su tail-leg) se reemplaza por
`leg_cost = max(stress_mult*(half_spread_bps + fee_bps_per_side), liquidity_fallback_floor_bps)`
(default 100); el otro leg se computa normal. **Fórmula RT explícita** (exactamente un leg en
fallback): `total = leg_cost_fallback + (floor_leg_normal + tail_leg_normal) + funding_RT`, luego
total-cap. Con AMBOS legs en fallback: `total = 2*leg_cost_fallback + funding_RT`, capped. El
funding (RT, no por-leg) **siempre se suma una vez** encima, independiente del fallback.
Anclado-al-floor, nunca por debajo; mantiene monótono "bar muerto nunca más barato que bar fino
vivo".

**Contrato de salida (add-only, no se remueve nada):** mismas keys que v2 —
`entry_slippage_bps`, `exit_slippage_bps` (cargan `tail_entry`/`tail_exit`), `entry_spread_bps`,
`exit_spread_bps` (cargan los legs de spread del floor), `fee_bps`, `funding_cost_bps`,
`total_cost_bps`, `total_cost_usd`. **Agrega** `floor_bps`, `tail_bps`, `cap_hit` (bool),
`fallback_hit` (bool — algún leg cayó en el fallback de liquidez). Un `TierParams` con campos
v3 NaN (p.ej. uno de v2/envenenado) metido al branch v3 **lanza `ValueError`** (simétrico al
poison v3→v2): el NaN se vuelve ruidoso, no un fallback de liquidez disfrazado.

---

## 5. JSON de calibración + cargador (commit atómico)

El `load_calibration` actual hace acceso por subscript MANDATORIO a `t['base_bps']`,
`t['size_factor']`, y raíz `version`/`model`/`sensitivity_note`; la `Calibration` dataclass no
tiene campo `global`. Un JSON v3 que dropee esos campos rompe en **import-time** para TODOS los
consumers. Por tanto **el cambio de loader + dataclass + JSON es UN solo commit atómico.**

**Números v2 congelados → archivo físico sibling `costs_calibration.v2.json`** con el shape FLAT
v2 exacto de hoy. **DEBE conservar `"version": 2` como campo** — es lo que activa el parser flat;
quitarlo rompe el dispatch silenciosamente. Los tests v2 pinned cargan vía
`load_calibration(path="costs_calibration.v2.json")`. **No se embebe un bloque `v2_legacy`** (sería
peso muerto). `load_calibration` crece un parse version-aware: `version==2` → parser flat actual
(byte-idéntico); `version==3` → parser nested floor/tail + bloque `global`.

```jsonc
// costs_calibration.json  (v3, production-governing — NN#6)
{
  "version": 3,
  "model": "two-body bound: floor(spread+fee+funding) + decoupled daily-basis sqrt impact-tail",
  "active_model": "v3",
  "v3_planned": "Reemplaza el acople de ancla-unica v2 por dos cuerpos desacoplados. Futuro: sigma_daily rolling por simbolo; ESTIMADOR empirico de costo separado (epic distinto, vive AL LADO de esta cota, nunca la reemplaza).",
  "global": {
    "Y_impact_constant": 1.5,
    "total_cost_cap_bps": 1000.0,
    "liquidity_fallback_floor_bps": 100.0,
    "v_daily_minutes_per_day": 1440
  },
  "tiers": {
    "major": { "symbols": ["BTCUSDT","ETHUSDT"],
      "floor": { "half_spread_bps": 1.5, "fee_bps_per_side": 5.0, "funding_rate_bps_per_8h": 1.0, "stress_mult": 1.0 },
      "impact_tail": { "sigma_daily_bps": 300.0 } },
    "mid":   { "symbols": ["ADAUSDT","AVAXUSDT","DOGEUSDT","UNIUSDT","XLMUSDT"],
      "floor": { "half_spread_bps": 4.0, "fee_bps_per_side": 5.0, "funding_rate_bps_per_8h": 2.0, "stress_mult": 1.0 },
      "impact_tail": { "sigma_daily_bps": 500.0 } },
    "small": { "symbols": ["PENDLEUSDT","JUPUSDT","RUNEUSDT"],
      "floor": { "half_spread_bps": 10.0, "fee_bps_per_side": 5.0, "funding_rate_bps_per_8h": 5.0, "stress_mult": 1.0 },
      "impact_tail": { "sigma_daily_bps": 800.0 } }
  },
  "sources": { /* re-citado honestamente, ver §2-§3; incluye nota fee=taker estandar SIN colchon, y tail ~100x = 38x base x 2.7x slope */ },
  "sensitivity_note": "Floor = cota del regimen operativo (size-indep). Tail inert (dominado por floor) SOLO en el trade promedio ~$644; guardarrail real en 1e-3..1e-1 para fills grandes en alts finos; co-dominante para small a notional de backtest. stress_mult + sigma por simbolo son los levers de volatilidad."
}
```

**Cambios de dataclass (enumerados):**
- `Calibration` gana campo `global: GlobalParams` (parse de `Y_impact_constant`,
  `total_cost_cap_bps`, `liquidity_fallback_floor_bps`, `v_daily_minutes_per_day`).
- `TierParams`: **shape FLAT con campos duales** (mantiene `base_bps`/`size_factor` para v2 +
  agrega `floor_*`/`tail_*` para v3). Razón: el shape nested rompe TODA construcción directa
  `TierParams(...)` en tests (verificado: `test_backtest_costs_v2.py:404-411`,
  `test_backtest_costs.py:229-234`). Flat dual preserva esas construcciones sin reescribir y deja
  al loader version-aware decidir qué campos llenar.
  - **Estado de los campos cruzados (poison, NO 0.0):** cuando el parser v3 construye un
    `TierParams`, llena `base_bps`/`size_factor` con `float('nan')` — **NO 0.0**. Razón: un
    consumo v2 residual (RA hardcoded `:737`, recompute, `TestAnchorParity` antes de re-apuntar,
    `regime_allocation_sweep` antes del fix) que reciba un objeto v3 con `size_factor=0.0`
    calcularía slippage **0 silenciosamente** (split-brain). Con `NaN` ese consumo produce un
    costo `NaN` ruidoso y detectable. Simétrico: un `TierParams` cargado de v2 lleva
    `floor_*`/`tail_*` = `NaN`. **Construcción vía classmethod factory**
    (`TierParams.from_v3_tier(floor, impact_tail)` / `.from_v2_flat(...)`) para evitar el problema
    de orden-de-campos en el `@dataclass(frozen=True)` (no se pueden añadir defaults a
    `base_bps`/`size_factor` sin forzar defaults en todos los campos posteriores; el factory lo
    sortea sin tocar la firma posicional que usan los tests).
- `recompute.py`: **se fija al v2 congelado** (`load_calibration(path="costs_calibration.v2.json")`
  en `:13`; `replace(tp, size_factor=...)` en `:34` sigue válido). Es un diagnóstico v2 cuyo
  `sf_div` sobre `size_factor` no tiene semántica contra un tail v3 — NO se re-apunta al tail, se
  congela.

---

## 6. Test de falsificación

Dos partes: techo de sanidad live (refuta / expone-inversión, **nunca valida**) + aserción
machine-checkable de no-inversión-de-signo.

**Harness:** `tools/ks_stress_replay/falsify_cost_bound.py` — read-only, `mode=ro` contra el prod
`signals.db`. Columnas verificadas en `db/positions_schema.py`: symbol, direction,
entry/exit_price, size_usd, qty, pnl_usd, pnl_pct, entry/exit_ts, exit_reason. **No existe
columna de costo;** el `pnl_usd` live es movimiento de precio crudo a precios de fill del operador
(`db/positions.py::_calc_pnl`) → la fricción realizada ya está horneada y es
**fee/funding-EXCLUSIVE.**

**Frontera NN#3 (explícita):** la ventana de reconciliación live es 2026-05-21 → 06-01, POSTERIOR
al cutoff de holdout (≤2025-04-29). El harness lee prod `signals.db` + OHLCV SOLO de esa ventana
2026. **NUNCA lee frames pre-2025-04-29 y NUNCA llama `open_holdout`.** NN#3-clean.

Por posición cerrada *i*:
- `MODEL_COST_BPS_i = compute_trade_costs(model='v3', tier=tier_for_symbol(symbol), ...)['total_cost_bps']`
- `REALIZED_CEILING_BPS_i = MIN(C1, C2)`, techo **edge-AGNÓSTICO**:
  - **C1 (techo de ganador):** para trades net-positivos en precio (`pnl_usd > 0`), la fricción no
    puede exceder el movimiento favorable bruto → techo = `|pnl_pct|*100`. Solo válido en
    ganadores; los perdedores NO dan cota superior de costo (reintroduciría la trampa de
    identificación mutua).
  - **C2 (techo spread-physics):** banda FIJA de spread perp publicada por tier (RT) =
    `{major 3.0, mid 8.0, small 20.0}` bps (= 2× el half_spread floor de §2). Se elige la banda
    fija publicada, **NO** reconstrucción OHLCV — un estimador de spread (Corwin-Schultz vs Roll
    vs proxy high-low) daría C2 distintos y no reproducibles, y C2 solo alimenta el diagnóstico
    `R_i`, no un gate.

**Resolución de los dos blockers del harness (del workflow de diseño):**
- **La condición de holgura no excluye, FALLA o se reporta.** No se filtra silenciosamente un
  símbolo demasiado-cargado del check de signo (ese es EXACTAMENTE el caso de inversión: AVAX-short
  v2 ~159bps habría pasado por exclusión). La holgura se reporta como DIAGNÓSTICO, nunca como gate
  de membresía.
- **El criterio de PASS no exige cercanía a live.** Requerir que v3 quede dentro de 3× de un techo
  live = fitting al único régimen muestreado (violación de R1). La cota se **falsifica SOLO** cuando
  `MODEL_COST < cota inferior demostrable del costo obligatorio` para algún fill.

**Aserciones (se vuelven checks pytest/harness):**

```python
# PRECONDICION dura: si n < 20 el harness ABORTA con error ruidoso (NO pasa, NO unscored, NO veredicto).
# 20 es el piso minimo para que el check de signo per-simbolo sea significativo; FINDINGS observo 27.
if n_closed_shorts < EXPECTED_MIN:               # EXPECTED_MIN = 20
    raise RuntimeError(f"falsification harness needs >={EXPECTED_MIN} closed shorts, got {n_closed_shorts}")
assert all(liquidity_proxy_i finite and non-fallback for i scored)   # else excluir + REPORTAR (no mezclar fallback en el veredicto)

# TRIPWIRE secundario de no-subestimacion (NO el test primario; ese es la no-inversion-de-signo abajo).
# mandatory_lower_bound NO es el floor del modelo — eso haria el assert VACUO (el total SIEMPRE incluye
# el floor, asi que total >= floor_propio es trivialmente cierto). Es la cota inferior INDISCUTIBLE e
# INDEPENDIENTE del modelo: el fee taker PUBLICADO por el exchange (2*5.0 = 10.0 bps RT), que ninguna
# ejecucion real puede evitar. El assert es NO-vacuo porque compara contra una constante EXTERNA: falla
# si la calibracion v3 carga fee < publicado o deshabilita fees (mis-config), capturando ese unico modo
# en que el modelo podria sentarse por debajo de un costo que el exchange cobra con certeza.
MANDATORY_LOWER_BOUND_BPS = 2 * PUBLISHED_TAKER_FEE_BPS   # 10.0 RT, del exchange, NO de costs_calibration.json
for i in scored:
    assert MODEL_COST_BPS_i >= MANDATORY_LOWER_BOUND_BPS  # RT vs RT

# TEST PRIMARIO de falsificacion (R1) — no-inversion-de-signo: ningun simbolo que gano en precio se
# vuelve "perdedor" de backtest por costo v3.
for S in symbols_with_closed_shorts:
    if abs(sum_i pnl_usd_i) > NOISE_BAND_USD:     # skip simbolos ~cero-net
        assert sign(sum_i (pnl_usd_i - v3_cost_usd_i)) == sign(sum_i pnl_usd_i)

# DIAGNOSTICO (NO gate): looseness ratio reportado, nunca falla la cota
R_i = MODEL_COST_BPS_i / REALIZED_CEILING_BPS_i   # v2 hoy ~30-40x; v3 esperado acotado, solo se imprime
```

`MODEL_COST_BPS` incluye fee+funding; el techo C1 es fee/funding-EXCLUSIVE. Como `R_i` ya no decide
membresía, la asimetría no cambia el conjunto asertado; para el reporte diagnóstico se compara
like-for-like (restar fee+funding del floor de `MODEL_COST` antes de dividir, o sumar la banda de
fee publicada al techo).

**SCOPE CAVEAT impreso IN-BAND con el veredicto (no solo en docs):** dominio = SOLO SHORT, ~$644
notional, régimen NORMAL mayo-2026, baja participación (~1e-6). NO licencia: costo long, regímenes
de crisis/spread-ancho, fills de alta participación, ninguna afirmación de edge, ninguna afirmación
de "validado". N=27 soporta el check de signo per-símbolo; NO soporta ninguna afirmación de
tightness/cobertura. Símbolos all-fallback o all-noise se reportan como "unscored", nunca como pase
silencioso.

**Lectura plain:** ningún símbolo que hizo dinero en precio en la cinta live se convierte en
"perdedor" de backtest por costo v3 solo. v2 FALLA en AVAX-short (live +$35 → net negativo vía
~159bps). v3 PASA sii el costo floor-dominado deja intactos los signos de los ganadores live.

---

## 7. Decisiones tomadas

| Decisión | Valor | Razón |
|---|---|---|
| Forma | Cota de dos cuerpos + total-cap | R1 + R2 (§1) |
| **Y** | **1.5** (tope del band) | Coherencia de tipo: solo el borde superior preserva el tipo cota (Voronov). Inertness es criterio jubilado, sin autoridad sobre Y |
| Default model | **Flipea a v3** | v2 invierte signos; dejar la cota defectuosa como default perpetúa la violación de capa |
| stress_mult | Default 1.0; **mandatorio en stress-replay** | Ningún cuerpo responde a σ; un stress-replay a costo de régimen normal es la cota volviéndose silenciosamente optimista en el régimen que debe sobrevivir |
| Total cap | 1000 bps (techo total) | El 500 sub-acotaría en eventos LUNA/FTX (8-15% adverso) |
| σ_daily | Estática 300/500/800; rolling fast-follow | Mantiene el PR acotado; stress_mult es el lever de vol primario |
| Falsificación | **Server DB antes de mergear** | Archivo production-governing (NN#6); el harness no corre en CI |

---

## 8. Plan de migración

**Versioning:** extender `Literal['v1','v2']` → `['v1','v2','v3']` en `compute_slippage_bps`
(def `:114`, param `model` `:121`) y `compute_trade_costs` (def `:273`, param `model` `:285`).
Ramas v1/v2 quedan **byte-idénticas** (load-bearing para
parity/forensic + recompute). La versión activa la dirige el objeto de calibración (`active_model`),
no literales dispersos. **El `else: raise ValueError` typo-guard existe SOLO en
`compute_slippage_bps` (`:172`)**; `compute_trade_costs` reenvía sin guard propio — el dispatch v3
debe rutear por ese guard o agregar uno explícito.

**Callers a tocar (verificados):**
- `_apply_costs_to_trade` (def `backtest.py:495`; su llamada interna a `compute_trade_costs` en
  `:518` NO pasa `model`/`enable_funding`/`holding_hours`). **Dos callsites reales del path LRC:
  `backtest.py:1169` y `:1474`** — `:1474` es el cierre tail-close de barra final; NO olvidarlo o
  hay split-brain entre cierre normal y cierre final. Threadear los tres params nuevos por la firma
  de `_apply_costs_to_trade` Y actualizar AMBOS callsites. Al flipear el default a v3, re-precia.
- `backtest.py:737` (`_close_position_ra`, path RA): `model="v2"` **HARDCODED** — el único literal
  hardcoded en prod. Debe hacerse calibration-driven (no editar a mano) o habrá split-brain.
- `tools/cost_diagnosis/recompute.py:13/34/39`: `load_calibration()` módulo-nivel, `replace(tp,
  size_factor=...)`, `model="v2"`. **Fijar al sibling v2 congelado.**
- `auto_tune.py:289/303`, `walk_forward.py`, `tools/regime_allocation_sweep.py:357/699`: heredan el
  default (LRC) o pegan el hardcode RA; re-run obligatorio. `regime_allocation_sweep` es path RA
  (setea `cfg.regime_allocation.enabled=True`) → SIEMPRE pega `:737`; hacerlo calibration-driven
  para que no haya flip sin test que lo guarde.

**BLOCKER funding LRC vs RA (resuelto):** el path LRC NO preciaba funding (no threadeaba
`holding_hours` ni `model` por `_apply_costs_to_trade`). **Resolución (refinada en implementación):**
el fix REAL es **threadear `holding_hours` + `enable_funding` + `model` + `global_params` por
`_apply_costs_to_trade`** y por sus dos callsites (`:1169`, `:1474`). Eso hace que LRC precie funding
v3 en prod, donde `_costs_active` ya es True vía los otros flags. **NO se agrega `enable_funding` al
guard `_costs_active` del LRC:** se intentó y se revirtió — como `enable_funding` defaultea a True,
incluirlo en el guard activa el path de costos en runs "costs-off" que solo apagan los otros tres
flags, rompiendo el idiom de tres flags de toda la suite (`test_backtest_with_costs`,
`test_backtest_bankruptcy`, parity). El bound en prod (todos los costos on) es idéntico con o sin esa
inclusión. Queda una asimetría residual LRC vs RA SOLO en el config degenerado "solo-funding"
(slippage/spread/fees=False, funding=True), que ningún run real usa; se acepta como edge irrelevante.

**Tests a actualizar (refs verificadas):**
- `test_backtest_costs_v2.py:516-526` `test_unknown_model_raises` usa `model="v3"` como sentinel
  INVÁLIDO → **reescribir a `'v9'`/`'bogus'` ANTES** de agregar v3 al Literal (si no, deja
  silenciosamente de lanzar).
- `:528` `test_v1_and_v2_both_accepted` → extender a `'v3'`.
- `:413` `test_v2_is_the_default_model` (tripwire del default) → actualizar en el MISMO commit al
  flipear el default a v3.
- `TestAnchorParity` (esp. `:92`, lee `size_factor` del JSON live) → apuntar al sibling
  `costs_calibration.v2.json`. v3 dropea anchor parity a propósito.
- `test_backtest_costs.py`: `test_calibration_records_v2_model_marker` (asserta version==2 +
  'sqrt-participation') → v3 marker o version-tolerant; `test_loads_committed_calibration` /
  `test_calibration_documents_source_per_param` (asserta root `base_bps`/`size_factor` + source
  keys) → schema nested floor/tail + source keys nuevos.
- **NUEVOS tests pinned v3:** dominancia floor del trade promedio; monotonicidad en order a
  liquidez fija; total-cap a 1000; fallback compone ≥ floor; tripwire `fee >= taker estándar
  publicado`; no-negatividad de cada término; `tail(0)==0`.

**Backtests a re-correr bajo v3 (todos los números v2-priced invalidados):** veredicto −90% de
FINDINGS + decomposición + medianas per-fill + tabla de inversión; #272 re-baselining; docs "formula
ganadora" 2026-04-17/04-18 (ya NN#5-flagged, ahora también stale en el eje costo); KS stress-replay
DD/cost (gate de cualquier shadow→active sobre v3); artifacts de `regime_allocation_sweep`; recompute
de `over_charge_ratio`.

**Constraints hard de back-compat:**
- Ramas v1/v2 callable; keys del output dict sin cambios (add-only); `load_calibration` mantiene
  `FileNotFoundError`-no-silent-default.
- **`.mex/context/architecture.md:84-90`** (hardcodea la fórmula sqrt v2 + anchor parity +
  Almgren-Chriss/Tóth/Donier-Bonart **al nivel del floor** — v3 los reframe como **tail-only**) debe
  editarse en el MISMO PR para evitar drift real de `mex check`.
- `RISK_PER_TRADE = 0.01` + taxonomía de tiers intactos (NN#4). `stress_mult` es un dial de pesimismo
  del COSTO dentro de `backtest_costs`, **NO** un risk-scaler multiplicativo de sizing — no confundir
  con el `size_factor` del kill-switch en `strategy/sizing.py`/`health.py` (concepto ortogonal).
- NO validar v3 contra frames de la ventana de holdout (NN#3, #322 bloqueado) — solo sanity ceiling
  live post-cutoff.

---

## 9. Precondición de merge (BLOCKER operacional)

El harness de falsificación **no puede correr en este repo/CI** — el `signals.db` local tiene 0
rows; necesita el DB del server (misma clase de blocker que "config.json vive en el server"). Un run
verde LOCAL es pase vacuo (el `n_closed_shorts >= 20` lo guarda). v3 cambia un archivo
production-governing (NN#6) y su única gate de falsificación no ejecuta en CI.

**Decisión tomada:** correr el harness contra el `signals.db` del server con `n_closed_shorts >= 20`
**ANTES** de mergear el cambio de calibración. Samuel proporciona acceso al DB del server (o corre el
harness y pasa el output). El resto del PR se implementa y revisa normalmente; la falsificación es la
última gate antes del merge, y su resultado se `mex log`-ea.

**Umbral y comportamiento (sin ambigüedad):** `EXPECTED_MIN = 20` es el piso (FINDINGS observó 27; 20
es el mínimo para que el check de signo per-símbolo sea significativo). Si el server devuelve
`n_closed_shorts < 20`, el harness **ABORTA con error ruidoso** — NO pasa, NO reporta unscored, NO
emite veredicto. Un run verde requiere n ≥ 20 reales; cualquier cosa por debajo es un fallo de
precondición, no un pase débil.

---

## 10. Scope OUT (parkeado, no olvidado)

- **Estimador empírico de costo** (objeto insesgado de fills reales): epic SEPARADO. Vive AL LADO de
  esta cota, nunca la reemplaza (R1). Requiere recolección sistemática de slippage post-trade de
  Binance — el "v3 plan" que el JSON v2 ya anticipaba, ahora correctamente tipado como objeto
  distinto.
- **σ_daily rolling por símbolo:** fast-follow del lanzamiento estático.
- **Migrar `walk_forward.evaluate_winner_on_holdout`** al holdout gate: PR de cierre de #322,
  ortogonal a v3.

---

## 11. Riesgos residuales (honestos)

1. **Floor major en el borde, no holgado.** RT 13.0 vs banda live 5-15. Mid/small acotan limpio; el
   major es "comparable al techo". Documentado, no escondido.
2. **El sqrt es la física equivocada para fills instantáneos.** Se queda como forma conservadora
   extrapolada, con la garantía de cota en los tres guardianes — no en la fidelidad física.
3. **Cifras de cobertura/tightness proyectadas, no medidas.** No se ejecutó el harness ni los
   backtests v3 en el entorno de diseño (y medirlas no validaría la cota, por R1).
4. **Vol-blindness fuera de stress-replay + `stress_mult` ships INERTE.** Un backtest normal de un
   régimen de alta vol no infla la cota salvo que el operador suba `stress_mult`. El default 1.0 es
   para régimen normal por diseño — **pero NADA en este PR pone `stress_mult` > 1** (el acople con
   el harness de stress-replay es un epic aparte, §10). Consecuencia honesta: `stress_mult` es uno
   de los tres guardianes nombrados en §1, y embarca como **no-op**. La cota **NO está demostrada
   crisis-survivable** (LUNA/FTX) por nada de este PR; esa afirmación espera a que el stress-replay
   jale el lever. No vender supervivencia a crisis hasta entonces.
5. **El cap puede quedar por debajo del floor bajo `stress_mult` alto.** Con `stress_mult` suficientemente
   grande, `floor_bps` solo puede exceder el `total_cost_cap_bps` (1000), y el `min(uncapped, cap)`
   recorta el total por debajo de su propio floor. Es **intencional** (el cap es un backstop de
   pesimismo, §4): en stress-replay el cap, no el floor, se vuelve la cota vinculante — justo el régimen
   para el que el cap existe. A `stress_mult=1.0` (default) NO puede ocurrir para ningún tier
   (floor máximo = small 30 << 1000). `enable_slippage=False` (modo gross-probe) omite el tail y el
   exceso de fallback de liquidez por diseño, consistente con v2; la garantía de cota aplica a la
   configuración por defecto con todos los costos activos.
