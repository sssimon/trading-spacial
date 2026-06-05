# Celda 4 — stat-arb: falsificación pre-registrada (pares cointegrados en perps)

**Fecha:** 2026-06-05 · **Verbo:** F · **Coordenada al verdict:** E1/F/n_F=3
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
con cost-model v3 + funding neto, produce P&L pooled $-denominado > 0
(bootstrap CI95 lo > 0) en walk-forward.

El pagador estructural hipotético: flujo de ruido que desalinea
transitoriamente pares co-movidos. La incertidumbre que caduca: si la
reversión del spread sobrevive costos reales en el mundo
post-institucionalización.

## §3 · Mundo y data

- **Data:** `data/program_ohlcv.db` → `perp_klines` (1h trade klines) +
  `perp_funding` (settlements), sub-universo panel∩perps (~148 símbolos,
  point-in-time: el listing de Vision retiene delistados). Cero spot proxy:
  las piernas viven y se evalúan en perp.
- **Cero holdout:** ningún módulo lee `data/holdout/` ni llama
  `simulate_strategy`/`open_holdout`. #322 intacto.
- **Denominación:** $ por posición con `NOTIONAL_PER_LEG = 10_000` fijo
  (escala-invariante en %; evita el mirage sharpe↔net_pnl documentado
  2026-06-02).

## §4 · Parámetros IRREVOCABLES de la config primaria

| Parámetro | Valor | Ancla |
|---|---|---|
| Ventana de formación | 180 días (4,320 barras 1h) | rango 6-12mo de la lit. |
| Ventana de trading | 30 días, walk-forward rolling sin solape | ciclos rolling de la lit. |
| Test de formación | Engle-Granger sobre log-precios, ADF p<0.05 en residuos | EG = el más común en cripto |
| Elegibilidad de símbolo (point-in-time) | cobertura ≥95% de barras en la ventana de formación Y mediana de quote-volume diario en formación ≥ $1M | liquidez mínima pre-declarada, sin mirar retornos |
| Cap de pares | top-20 por menor p-value ADF; un símbolo puede aparecer máx. en 2 pares | Gatev top-20; el cap por símbolo evita concentración |
| Hedge ratio | β del EG de formación, congelado durante el trading window | estándar |
| Señal | z-score del spread con μ/σ de la formación (congelados) | estándar |
| Entrada | \|z\| ≥ 2.0 (long spread si z≤−2, short si z≥+2) | centro del rango 1.5-2.5 |
| Salida | cruce de z=0 | canónico |
| Stop | \|z\| ≥ 3.0 → cierre; cierre forzoso al fin del trading window; delisting de una pierna → cierre forzoso en la última barra disponible (costo cargado) | Park 2026; gap de delisting manejado explícito |
| Ejecución | taker en el close de la barra siguiente a la señal (lag 1 barra) | anti look-ahead |
| Costos | cost-model v3 por pierna por fill (4 fills/round-trip) + funding NETO del par por settlement retenido (de `perp_funding`) | decisión Samuel 2026-06-05 |
| Posiciones | dollar-neutral, $10k por pierna, máx. 1 posición por par simultánea | |

## §5 · Gates del verdict (pre-registrados)

- **Gate A (el verdict):** P&L pooled neto (todas las posiciones de todos los
  trading windows) — bootstrap por posición, `BOOTSTRAP_N=10_000`,
  `SEED=20260605`. **PASS ⟺ CI95 lo > 0.** Todo lo demás es descriptivo.
- **Robustez LOO (gate de fragilidad):** PASS requiere además que el CI95 lo
  pooled se mantenga > 0 al excluir (a) cualquier símbolo individual y
  (b) cualquier año calendario individual. Si Gate A pasa pero LOO falla →
  **FAIL por fragilidad** (la lección del Brazo A/PENDLE).
- **Kill criteria (cualquiera mata antes del bootstrap):** (1) < 30 posiciones
  cerradas en total (N insuficiente — ver §6); (2) > 50% del P&L bruto
  concentrado en un solo par-window (artefacto, no edge).
- **Qué significa FAIL:** con el poder de §6 declarado, FAIL cierra la celda —
  "no hay edge de pares cointegrados extraíble con diseño canónico en este
  mundo". Reapertura solo con hipótesis distinta tipada (p.ej. intradía 5min
  con costos maker — sería estudio nuevo, no re-corrida).
- **Qué significa PASS:** la celda cierra PASS con coordenada E1/F/n_F=3. NO
  autoriza deploy (dossier spec §5 del marco + decisión explícita). Si llega a
  competir con el PASS de carry por promoción → se activa la deflación
  cross-celda con N = celdas F corridas (regla de activación, marco §4).

## §6 · Poder declarado (pre-verdict, obligatorio)

Antes de la corrida del verdict, `power.py` computa y CONGELA en el artefacto:
el efecto mínimo detectable aproximado ($ por posición y % anualizado sobre el
notional desplegado) dado el N real de posiciones generadas y la σ empírica de
P&L por posición (ancho esperado del CI95 bootstrap). Si el efecto mínimo
detectable resulta mayor que el carry de referencia ya medido (6.33%/año,
celda 2), el verdict se emite igual pero el findings DEBE declarar la zona
ciega: "un edge menor que X no era visible con este N". Sin poder declarado el
FAIL no cierra la celda (marco §3-F).

## §7 · Sweep descriptivo (NO gatea, SÍ se registra)

Sensibilidad post-verdict, claramente rotulada DESCRIPTIVE: entrada
z ∈ {1.5, 2.5}, formación ∈ {120d}, top-N ∈ {10}. Cada combinación corrida se
registra en el trial registry con `source = "celda4-stat-arb/sensitivity"`;
la config primaria con `source = "celda4-stat-arb/primary"`. La deflación-N
intra-celda cuenta DISTINCT configs del namespace `celda4-stat-arb/*`. El
verdict NO se mueve por el sweep — si la primaria falla y una variante pasa,
el verdict es FAIL y la variante es una candidata declarada a estudio nuevo
(con su deflación arrastrando este N).

## §8 · Unidad de estudio (Cassian, 5 piezas)

1. Este spec (pre-registrado, commiteado antes del código).
2. `tools/celda4_stat_arb/` — clonando la forma de `tools/funding_carry/`:
   `constants.py` (parámetros §4 congelados) / `pairs.py` (formación EG) /
   `simulate.py` (señal+ejecución+costos) / `power.py` / `evaluate.py`
   (gates+bootstrap) / `run.py`. Determinista, seed fijo.
3. `data/retune/programa-celdas/celda4-stat-arb/verdict.json` (+ coordenada).
4. Datos del verdict: `positions.json` (por posición: par, window, entradas/
   salidas, P&L bruto, costos v3, funding neto), `pairs_formed.json`.
5. `findings.md` — veredicto línea 1, gates con números, poder declarado,
   qué-significa-PASS/FAIL, scope.

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
