# Celda 4 — stat-arb: falsificación pre-registrada (pares cointegrados en perps)

**Fecha:** 2026-06-05 · **REV 3** (REV 2 = roast Adrian, 6 BLOCKERS + 5 HIGH;
REV 3 = crítica ontológica Voronov, 4 hallazgos; ver §10) · **Verbo:** F ·
**Coordenada al verdict:** E1/F/n_F=3
**Survey previo:** ejecutado 2026-06-05 ANTES de este spec (anti-post-hoc,
requisito Richter) — hallazgos resumidos en §1. Este spec se commitea ANTES de
escribir el falsificador y MUCHO antes de correrlo. Los parámetros de §4 son
IRREVOCABLES una vez commiteados: cambiarlos = experimento nuevo con namespace
nuevo.

## §1 · Qué dice la literatura (anclas del diseño)

- **Frecuencia (keystone):** Fil & Kristoufek (IEEE Access 2020; 26 cryptos,
  5min/1h/diario, 2018-2019): el edge de pares NO existe en diario
  (−0.07%/mes) y aparece intradía (5min: +11.61%/mes bruto). 1h es la
  frontera. Tadi & Kortchemski (arXiv 2305.06961, 2023; 20 coins Binance,
  HORARIO, 2021-2023, fees 2/4bps modelados): positivo neto. → la pregunta en
  1h/2021-2026 está abierta: falsificable de verdad.
- **Parámetros canónicos:** formación 6-12 meses; Engle-Granger con ADF
  p<0.05 en residuos; entrada |z| 1.5-2.5; salida z=0; stop |z|>3; cap top-N
  (Gatev top-20). Las elecciones de §4 caen TODAS dentro de estos rangos —
  ninguna se eligió mirando nuestra data.
- **Modos de fallo cripto:** ruptura de cointegración en shocks (2022);
  sensibilidad a régimen (muere en tendencia); funding drag que puede
  invertir signo; delisting de la pierna corta (sin cuantificación académica
  — gap declarado).
- **Decay:** entrada institucional 2021-2023 (Jump sep-2021, Jane Street,
  Tower) → muestras pre-2021 sobreestiman. Nuestra ventana ES el mundo
  post-institucionalización.

## §2 · Hipótesis falsificable

**H:** En Binance USDT-M perps (1h, 2021-01→2026-05), una estrategia de pares
cointegrados con parámetros canónicos de literatura, ejecutada taker y cargada
con cost-model **v3w** (§3-bis) + funding neto, produce P&L pooled
$-denominado > 0 (bootstrap CI95 lo > 0) en walk-forward **Y sigue vivo en la
segunda mitad de la ventana** (Gate B, §5 — la hipótesis nombra un proceso, no
un promedio; el verdict debe responder ambas preguntas: ¿hubo edge? ¿sigue?).

El pagador estructural hipotético: flujo de ruido que desalinea
transitoriamente pares co-movidos. La incertidumbre que caduca: si la
reversión del spread sobrevive costos reales en el mundo
post-institucionalización.

## §3 · Mundo y data

- **Data:** `data/program_ohlcv.db` → `perp_klines` (1h trade klines) +
  `perp_funding` (settlements), sub-universo panel∩perps (point-in-time: el
  listing de Vision retiene delistados; el conteo REALIZADO se registra en el
  manifest, no se estima en prosa).
- **Fingerprint del input (F14):** el manifest del verdict congela: membresía
  exacta del panel∩perps, row-counts por símbolo en `perp_klines` y
  `perp_funding`, y min/max `open_time` por símbolo. La corrida es auditable
  contra ese fingerprint; el db es regenerable desde el bulk inmutable.
- **Cero holdout:** ningún módulo lee `data/holdout/` ni llama
  `simulate_strategy`/`open_holdout`. #322 intacto.
- **Denominación:** $ por posición con `NOTIONAL_PER_LEG = 10_000` fijo
  (escala-invariante en %; evita el mirage sharpe↔net_pnl documentado
  2026-06-02).
- **Mark-price (F1, aproximación DECLARADA):** `perp_funding` no carga mark.
  El funding P&L se computa como `rate × close_trade_kline(settlement_hour) ×
  units`. Error de la aproximación: `rate × |mark − close|` — con |rate|
  típico 1e-4 y divergencia mark-close < 0.1% en líquidos, el error es
  O(1e-7 × notional) por settlement: segundo orden, declarado, no afecta el
  signo de ningún gate.

### §3-bis · v3w: la moneda de costos de ESTA celda (Voronov V1)

v3 está **cerrado sobre 10 símbolos curados** (`tier_for_symbol` lanza
`UnknownSymbolError` fuera de dominio — el código se niega, no aproxima).
Aplicar "v3" al universo ancho sería una extrapolación renombrada como
calibración, y los PASSes de carry (net-of-v3 en su dominio) y de esta celda
serían inconmensurables con el mismo nombre.

**v3w** se define como extensión DECLARADA con procedencia propia:

- **Estructura:** idéntica a v3 (floor spread+fee por fill + tail de basis
  diario sqrt con Y=1.5) — se reusan los parámetros POR TIER de
  `costs_calibration.json` sin modificarlos.
- **Asignación de tier (lo nuevo, regla declarada):** por mediana del
  dollar-volume diario del símbolo en la VENTANA DE FORMACIÓN (la misma
  cantidad de elegibilidad de §4, función pura de formación): ≥$100M → tier
  large; ≥$10M → mid; <$10M → small. (El piso de elegibilidad de $1M ya
  excluye lo no-tierable.)
- **El tipo carga su dominio en el nombre:** el verdict de esta celda es
  **net-of-v3w**, no net-of-v3. La comparación cardinal con el PASS de carry
  queda **prohibida a nivel de tipo** salvo re-pricing explícito a moneda
  común (operación que, si algún día se hace para la regla de activación del
  marco §4, es un acto declarado con su propio artefacto).
- **Implicación para el marco (registrada como pregunta abierta del INDEX,
  no se legisla aquí):** toda celda F de universo ancho (3, 9) tiene el mismo
  problema de moneda; el mandato "net-of-v3" de la constitución es cobrable
  solo en el dominio curado.

## §4 · Parámetros IRREVOCABLES de la config primaria

| Parámetro | Valor | Ancla |
|---|---|---|
| Ventana de formación | 180 días (4,320 barras 1h esperadas) | rango 6-12mo de la lit. |
| Ventana de trading | 30 días, walk-forward rolling sin solape; la formación de cada window son los 180d inmediatamente anteriores (re-estimación cada 30d) (F7) | ciclos rolling de la lit. |
| Test de formación | Engle-Granger sobre log-precios CON intercepto; ADF sobre residuos con `regression='c'`, `autolag='AIC'`, p<0.05 (F4) | EG = el más común en cripto |
| Orientación EG (F4) | determinista: Y = símbolo lexicográficamente posterior regresado sobre X = anterior | arbitraria pero fija — dos builds fieles producen el mismo par |
| Guard de degeneración (F4) | σ del spread de formación < 1e-6 (en log) → par excluido en formación | |
| Elegibilidad de símbolo (point-in-time, F2/F3) | (a) cobertura ≥95% de las 4,320 barras de formación; (b) mediana sobre los 180 días de formación del dollar-volume diario ≥ $1M, donde `dollar_volume_diario = Σ_barras(volume_base × close)`. **Toda cantidad de elegibilidad es función pura de barras ≤ fin de formación. PROHIBIDO todo filtro que referencie disponibilidad o data del trading window** — un símbolo que delista 3 días después de formar es elegible y su pérdida de cierre forzoso entra al P&L (así se mecaniza el anti-survivorship en la capa de selección, no solo en la de data). | liquidez pre-declarada sin mirar retornos |
| Gaps en formación (F7) | la cobertura ≥95% admite huecos; el spread se computa sobre barras donde AMBOS símbolos tienen close; μ/σ/β sobre esa intersección | |
| Cap de pares (F5) | top-20 por menor p-value ADF; **un símbolo aparece en MÁXIMO 1 par por trading window** (el de menor p-value) — elimina interacción de piernas compartidas: exposición, capital y unidades del bootstrap quedan bien definidas | Gatev top-20 |
| Hedge ratio | β del EG de formación, congelado durante el trading window | estándar |
| Señal | z-score del spread con μ/σ de la formación (congelados) | estándar |
| Entrada | \|z\| ≥ 2.0 (long spread si z≤−2, short si z≥+2); máx. 1 posición por par simultánea; sin re-entrada tras stop dentro del mismo window | centro del rango 1.5-2.5 |
| Salida | cruce de z=0 | canónico |
| Stop | \|z\| ≥ 3.0 → cierre; cierre forzoso al fin del trading window (sin re-apertura en el window siguiente salvo señal nueva de su propia formación); delisting de una pierna → cierre forzoso en la última barra disponible (F3) | Park 2026 |
| Ejecución | taker en el close de la barra siguiente a la señal (lag 1 barra) | anti look-ahead |
| Costos (F6, V1) | **v3w** (§3-bis) por pierna por fill (4 fills/round-trip); fills de cierre forzoso por delisting se cargan al tier small de v3w (el peor) independiente del tier de formación; la reutilización de los parámetros por-tier de v3 fuera de su universo de calibración se declara como limitación en findings | |
| Funding (F13) | NETO del par, acumulado en todo settlement con `entry_fill_time < funding_time ≤ exit_fill_time`, al close de la hora del settlement (§3 aproximación) | decisión Samuel 2026-06-05 |
| Posiciones | dollar-neutral, $10k por pierna | |

## §5 · Gates del verdict (pre-registrados)

**Orden de evaluación FORZADO (F10): kill criteria → gate de poder → Gate A →
LOO. Ningún paso lee el resultado de un paso posterior.**

- **Kill criteria (matan el estudio SIN emitir PASS/FAIL — el verdict es
  N-INSUFICIENTE o ARTEFACTO, la celda NO cierra):**
  1. < 30 posiciones cerradas en total.
  2. Concentración (F11): entre las posiciones con P&L neto > 0, la mayor
     contribución de un par-window > 50% de la suma de contribuciones
     positivas. (Si el P&L total ≤ 0 este criterio es inerte — el CI de Gate
     A resuelve.)
  3. LOO inviable (F9): algún subset LOO (por símbolo o por año) queda con
     < 30 posiciones.
- **Gate de poder (F10/V2, ANTES de mirar el signo de nada):** `power.py`
  computa el efecto mínimo detectable (MDE ≈ semi-ancho esperado del CI95
  sobre el estimador pooled, desde N de posiciones y σ empírica de P&L por
  window) y lo congela en el artefacto. **Si MDE > 10%/año sobre el notional
  desplegado, el estudio muere como N-INSUFICIENTE sin verdict.** El umbral
  está anclado en LITERATURA, no en otra celda (Voronov V2: usar el 6.33% de
  carry era un cardinal cross-mundo dentro del gate — atlas violado): 10%/año
  es el orden del efecto neto que reporta Tadi & Kortchemski 2023 (~10.6%),
  el setup publicado más cercano a este mundo (Binance, horario, 2021-2023,
  costos modelados). La mecánica fuerza el orden (power.py corre y escribe
  ANTES de evaluate.py).
- **Gate A (¿hubo edge?):** P&L pooled neto, **bootstrap por TRADING-WINDOW**
  (F8): se resamplean con reemplazo los windows (cada uno con la suma de P&L
  de sus posiciones), `BOOTSTRAP_N=10_000`, `SEED=20260605`. **CI95 lo > 0.**
  El bootstrap per-posición se reporta como descriptivo (sesgado a estrecho
  bajo clustering temporal — declarado).
- **Gate B — vigencia (¿sigue vivo?, Voronov V4):** mismo bootstrap por
  window restringido a los windows cuyo inicio ≥ **2023-09-01** (la segunda
  mitad de la ventana, ~33 windows). **CI95 lo > 0.** Sin esto, el pooled
  integra sobre la no-estacionariedad que la propia literatura del §1 declara
  como EL fenómeno: un edge que vivió en 2021-22 y murió en 2025 pasaría Gate
  A y LOO-por-año. Si el subset de Gate B queda con < 30 posiciones → kill
  criterio 3 (N-INSUFICIENTE, sin verdict).
- **PASS ⟺ Gate A ∧ Gate B ∧ LOO.** Gate A sin Gate B = **FAIL por edge
  muerto** (cierra la celda: el promedio histórico existió, el mundo presente
  no lo paga). El findings reporta la curva por-año como descriptivo.
- **Robustez LOO:** CI95 lo > 0 (mismo bootstrap por window, sobre el
  pooled) al excluir (a) cualquier símbolo individual, (b) cualquier año
  calendario. Gate A+B pasan + LOO falla → **FAIL por fragilidad** (lección
  Brazo A/PENDLE).
- **Qué significa FAIL:** con el gate de poder superado, FAIL cierra la celda
  — "no hay edge de pares cointegrados extraíble con diseño canónico en este
  mundo, O lo hubo y murió" (el findings distingue cuál vía Gate A/B).
  Reapertura solo con hipótesis distinta tipada (p.ej. intradía 5min costos
  maker — estudio nuevo).
- **Qué significa PASS:** la celda cierra PASS con coordenada E1/F/n_F=3.
  Gate A afirma el promedio histórico; Gate B es lo único que afirma
  vigencia. NO autoriza deploy (dossier del marco §5 + decisión explícita).
  El PASS está denominado **net-of-v3w** (§3-bis): NO es comparable
  cardinalmente con el PASS de carry sin re-pricing explícito a moneda común
  — si algún día compite por promoción, ese re-pricing es un acto declarado
  previo a la deflación de la regla de activación (marco §4).

## §6 · Poder declarado

Mecanizado como gate en §5 (orden forzado, umbral 6.33%/año pre-registrado).
El findings reporta además la zona ciega residual: "un edge menor que MDE no
era visible con este N", cualquiera sea el verdict.

## §7 · Sweep descriptivo (NO gatea, SÍ se registra)

Sensibilidad post-verdict, claramente rotulada DESCRIPTIVE: entrada
z ∈ {1.5, 2.5}, formación ∈ {120d}, top-N ∈ {10}. Cada combinación corrida se
registra en el trial registry con `source = "celda4-stat-arb/sensitivity"`;
la config primaria con `source = "celda4-stat-arb/primary"`. **La deflación-N
de la primaria se congela en SU corrida (F12): N=1 config del namespace al
momento del verdict; el sweep posterior crece su propio N y no re-interpreta
el verdict.** Si la primaria falla y una variante pasa, el verdict es FAIL y
la variante es candidata declarada a estudio nuevo (arrastrando este N).

**Honestidad sobre la frontera (Voronov V3):** la separación
primaria/sweep vive en el campo `source` y en la disciplina del operador —
es una CONVENCIÓN, no una restricción mecánica (el mismo `evaluate.py`
computa ambas). El enforcement real: el orden está pre-registrado aquí, el
sweep no se implementa hasta después del verdict de la primaria (§9), y el
backstop es la revisión de PR — mismo contrato que el candado del programa:
defensa contra el humano distraído, no contra el atacante motivado.

## §8 · Unidad de estudio (Cassian, 5 piezas)

1. Este spec (pre-registrado, commiteado antes del código).
2. `tools/celda4_stat_arb/` — clonando la forma de `tools/funding_carry/`:
   `constants.py` (parámetros §4 congelados) / `costs.py` (**v3w**, §3-bis) /
   `pairs.py` (formación EG) / `simulate.py` (señal+ejecución+costos) /
   `power.py` / `evaluate.py` (gates+bootstrap) / `run.py`. Determinista,
   seed fijo.
3. `data/retune/programa-celdas/celda4-stat-arb/verdict.json` (+ coordenada +
   fingerprint del input §3).
4. Datos del verdict: `positions.json` (por posición: par, window,
   entrada/salida, P&L bruto, costos v3, funding neto), `pairs_formed.json`,
   `power.json` (escrito ANTES que el verdict).
5. `findings.md` — veredicto línea 1, gates con números, poder declarado,
   qué-significa-PASS/FAIL, scope, limitación de calibración v3 (F6).

## §9 · Negative space

- NO se toca `tools/funding_carry/` ni sus constantes.
- NO se corre el sweep antes del verdict de la primaria.
- NO regime-gating, NO copulas, NO Johansen, NO optimización de umbrales —
  diseño canónico congelado; las mejoras son estudios futuros tipados.
- NO se reusa `walk_book`/infra de ejecución del sistema vivo (mide
  taker-cross del sistema de señales, otro mundo).
- NO cardinales de la celda en la columna Veredicto del INDEX (regla del
  atlas); los números viven en findings/verdict.json.
- El fingerprint/selection-world se declara en el artefacto (cost-model v3 +
  deflación intra-celda); el contrato del `selection_fingerprint` NO se
  modifica (marco §4).

## §10 · REV log

- **REV 1 (2026-06-05, commit 2720e9f):** versión inicial post-survey.
- **REV 2 (2026-06-05):** roast de Adrian (6 BLOCKERS, 5 HIGH) aplicado
  íntegro: F1 mark-price como aproximación declarada con cota (§3); F2
  fórmula de dollar-volume + cláusula de pureza de formación (§4); F3
  anti-survivorship mecanizado en la capa de selección (§4); F4 EG
  determinista (orientación lexicográfica, intercepto, ADF c/AIC, guard σ)
  (§4); F5 cap 2→1 par por símbolo (§4); F6 peor tier v3 en cierres forzosos
  + limitación declarada (§4); F7 cadencia rolling y gaps definidos (§4); F8
  bootstrap por trading-window (§5); F9 LOO min-N como kill (§5); F10 gate de
  poder con orden forzado y umbral pre-registrado (§5); F11 concentración
  sobre neto positivo (§5); F12 deflación de la primaria congelada (§7); F13
  boundary de funding `(entry, exit]` (§4); F14 fingerprint del input en el
  manifest (§3); F15 conteos realizados, no prosa (§3).
- **REV 3 (2026-06-05):** crítica ontológica de Voronov aplicada: **V1** v3
  está cerrado sobre 10 símbolos (`UnknownSymbolError` fuera de dominio) →
  la celda define **v3w** con asignación de tier por dollar-volume declarado
  y el verdict se denomina net-of-v3w, incomparable con carry sin re-pricing
  (§3-bis, §5); implicación constitucional registrada como pregunta abierta
  del INDEX. **V2** el umbral del gate de poder era el cardinal de carry
  (cross-mundo, atlas violado) → reemplazado por ancla de literatura: 10%/año
  (Tadi 2023, setup más cercano) (§5). **V3** la frontera primaria/sweep es
  convención, no mecanismo → declarada honesta con su contrato de enforcement
  (§7). **V4** el pooled bootstrap asume intercambiabilidad de windows y
  borra el decay (el fenómeno del §1) → **Gate B de vigencia** (segunda mitad
  de la ventana, ≥2023-09, CI95 lo > 0) requerido para PASS; Gate A sin Gate
  B = FAIL por edge muerto (§2, §5).
