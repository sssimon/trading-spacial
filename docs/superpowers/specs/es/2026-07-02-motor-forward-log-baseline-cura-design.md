# Motor forward-log del baseline cured-random — Diseño

**Fecha:** 2026-07-02
**Estado:** diseño aprobado (brainstorming + junta del roster, unánime scope A). Pendiente: review del spec → plan de implementación.
**Rama sugerida:** `feat/baseline-forward-log`

## Origen

Una sesión larga de búsqueda de edge estableció, con la evidencia más fuerte que la data permite
(OOS + adversarial + day-matched + anti-survivorship):

- **La SELECCIÓN de entrada es nula** — no se puede escoger la moneda ganadora (ver
  [[edge-mecanico-agotado-day-matched]], commit `db246f2`).
- **La GESTIÓN sí da valor** — "curar el azar": entrada RANDOM diversificada + exit asimétrico
  (escalera de targets +15/30/50/90% + runner + piso −50%) + circuit breaker re-armante
  (`kill_switch_v2`) + diversificación M=20. Validado walk-forward (25/25 folds) con protección de
  bear OOS (cura 1.48x / 9% maxDD vs naive 0.54x / 71% maxDD sobre el bear 2022 ciego). Ver
  `data/retune/2026-06-23-calibracion-gate-regimen/curar_azar_findings.md` (commit `69bdf59`, `8d34629`).

La única medición que queda es forward: correr la cura random en vivo como **baseline** y, con el
tiempo, comparar contra el operador humano (el papá, que opera Valles discrecionalmente). Este spec
diseña ese baseline. La junta del roster votó **unánime** por el scope mínimo (baseline solo; la
comparación operador-vs-baseline se difiere porque su input aún no existe).

## Qué construimos

Un **motor forward-log** en producción: un **baseline vivo** de la cura random-curada. NO es un bot
que tradea. Es un instrumento de MEDICIÓN descriptivo — el "cero" contra el cual, más adelante, se
mide si la mano humana agrega valor. Corre en paper, día a día, hacia adelante desde hoy.

**Insight que define el diseño (Serrano):** el baseline NO es un solo camino random. La cura se validó
como *distribución* (25/25 folds, 5 semillas → 1.47-1.59x). Un log que escoge UNA rotación registra UNA
muestra — que pudo salir afortunada o no. Por eso el baseline es un **ENSEMBLE de N semillas** y emite
la **distribución** (mediana + banda p10-p90), no un número. El operador se medirá contra la nube.

## No-negociables (el motor los respeta por construcción)

- **#8 freshness owner + `LiveSnapshot`:** el motor es estado vivo que cruza el borde de proceso, así
  que DEBE tener un dueño de frescura nombrado en prod (un lifespan thread en `scanner/runtime.py`,
  nunca un CLI manual) y emitir su frescura vía `freshness.LiveSnapshot` (fresco/rancio/muerto). Sin
  esto no mergea.
- **Doctrina anti-veredicto:** el read describe un yardstick ("el baseline va en X"), nunca ordena
  ("compra X"). Los picks individuales del día NO se surfacean (se leerían como señal).
- **#4 RISK_PER_TRADE=0.01 fijo:** el baseline es PAPER; su sizing es equal-weight `cap/M` (el
  parámetro del estudio), y NO toca ni referencia el `RISK_PER_TRADE` de producción ni agrega scalers.
- **#3 holdout bloqueado:** el motor vive en ventana forward (hoy → adelante); es estructuralmente
  imposible que roce el holdout (2025-04-30..2026-04-30). No usa `open_holdout`/`simulate_strategy`.

## Arquitectura — componentes

### 1. Núcleo de cómputo del ensemble (`scanner/baseline/ensemble.py`)

**Qué hace:** mantiene N portafolios paper independientes (uno por semilla) y los avanza un día.
**Interfaz:**
- `BaselineEnsemble(n_seeds=30, M=20)` — estado: por semilla, `cap`, historia de equity, posiciones
  abiertas `[(entry_date, symbol, entry_price, ladder_state)]`.
- `advance_day(date, universe_bars) -> None` — para cada semilla: (a) marcar cada posición abierta con
  la barra diaria (avanzar la escalera viva, ver abajo); (b) cerrar las que llegaron a t+30; (c) computar
  `portfolio_dd` con **pico rodante 180d** → `evaluate_portfolio_tier(dd, 0, cfg)` (kill_switch_v2 real,
  `aggressiveness` alto); (d) si hay slots libres y `tier != FROZEN`, abrir picks random. **Idempotente
  por fecha** (si `last_date >= date`, no hace nada — clave para reinicios).
- `snapshot() -> dict` — mediana + p10/p90 de equity, distribución de tiers, `n_seeds`, `last_date`.
- Pick reproducible: `seed_pick(date, seed)` deriva el offset de `sha256(f"{date}|{seed}")` sobre el
  universo alive ordenado → auditable, mismo (fecha, semilla) → mismo pick.

**Escalera VIVA (diferencia con el backtest):** el backtest computó `ladder_return` de un tiro sobre
30 días conocidos. En vivo se **acumula**: `ladder_state` guarda `{frac_restante, realizado, next_tp_idx}`.
Cada día, si el `high` de la barra tocó el próximo target → vende esa fracción (acumula en `realizado`);
si el `low` tocó el piso −50% → cierra todo; en t+30 → cierra el remanente al `close`.
**Depende de:** `strategy.kill_switch_v2`, la definición congelada de la escalera (TPS/FRACS/DISASTER/HOR).

### 2. Freshness owner — lifespan thread (`scanner/runtime.py`)

**Qué hace:** es el dueño de frescura nombrado. Loop diario con `stop_event`, registrado en
`_managed_threads` para teardown limpio (mismo patrón que `scanner_loop`/`sync_loop`).
**Cada tick:** si hay una barra diaria nueva no procesada → fetch de las barras diarias del universo
trackeado, `ensemble.advance_day(...)`, `store.persist(...)`, refrescar el `LiveSnapshot`.
**Depende de:** el ensemble, la persistencia, y `_fetch_daily_bars` (ya existe en `api/levels.py`,
`api/plan.py`, `api/valleys.py`) como fuente de barra diaria del universo trackeado — sin abrir
superficie nueva de fetch/rate-limit (atajo consciente).

### 3. Persistencia (`scanner/baseline/store.py`)

**Qué hace:** serializa el estado del ensemble (portafolios por semilla + historia de equity + `last_date`)
para sobrevivir reinicios y hacer replay determinista. **Interfaz:** `load() -> state | None`,
`persist(state)`. **Depende de:** un store simple (tabla sqlite o JSON versionado en `data/`).

### 4. LiveSnapshot + read (`scanner/baseline/snapshot.py` + endpoint en `btc_api.py`)

**Qué hace:** envuelve `snapshot()` en `freshness.LiveSnapshot` (umbral ~26h → fresco/rancio/muerto) y
lo sirve read-only. **Interfaz:** `GET /baseline` → `{estado_frescura, mediana, banda_p10, banda_p90,
n_seeds, tier_actual, last_date}` + copy anti-veredicto. **Depende de:** `freshness.LiveSnapshot`
(mismo patrón que `api/scanner_liveness.py` y `api/valleys.py`; `tests/test_valles_freshness.py` es el
modelo de test de frescura).

## Flujo de datos (tick diario)

```
lifespan thread (scanner/runtime.py)
  └─ ¿barra diaria nueva? ──sí──> fetch barras universo (fuente existente)
         └─ ensemble.advance_day(date, bars)   [marca escaleras, kill-switch, picks random por semilla]
              └─ store.persist(state)
                   └─ snapshot.refresh()  ──> freshness.LiveSnapshot (fresco)
  (si el thread muere → LiveSnapshot pasa a rancio → muerto; el read lo expone, nunca empty mudo)
GET /baseline ──> LiveSnapshot.to_response()  [descriptivo: "el baseline va en X (banda Y-Z)"]
```

## Manejo de errores / modos de falla (Halberg)

- **Barra faltante de un símbolo tenido** (delisted/gap): si no hay barra, no se resuelve ese día; si el
  símbolo desaparece del universo N días → se cierra la posición al último precio conocido (trata la
  muerte, no la ignora).
- **Owner muerto:** el `LiveSnapshot` transita fresco→rancio→muerto por antigüedad; el read lo expone.
- **Reinicio a mitad de día:** `load()` recupera el estado; `advance_day` es idempotente por fecha → no
  hay doble avance.
- **Rate-limit en el fetch del universo:** reusa barras cacheadas; el umbral de frescura atrapa el stale.

## Testing

- **#8:** un test que arranca el owner → `LiveSnapshot` reporta `fresco`; sin owner (o tras el umbral)
  reporta `muerto`. Cubre el no-negociable.
- **Reproducibilidad:** `assert seed_pick(d, s) == seed_pick(d, s)` y que dos `advance_day` de la misma
  fecha no cambian el estado (idempotencia).
- **Escalera viva:** un test de que la escalera acumulada sobre una serie sintética de barras da el mismo
  resultado que `ladder_return` de un tiro (equivalencia backtest↔vivo).

## Diferido (post-MVP, no en este spec)

- **Comparación operador-vs-baseline:** captura/normalización de las decisiones reales del operador (su
  input ya lo registra el lifecycle de posiciones vía `PositionClosure`) + scorecard. Se construye cuando
  el papá haya operado N semanas y el baseline tenga historia — su input no existe hoy.

## Caveat abierto declarado (Lyra)

Es un **yardstick descriptivo/direccional, no un test de hipótesis.** A cadencia de operador humano
(pocos trades/mes) contra un baseline diversificado M=20, la comparación puede tardar **años** en tener
poder estadístico para rechazar el nulo. Se construye igual porque el log es convexo y barato y es
irreversible (no se reconstruye hacia atrás), pero el read declara esta limitación — no promete un
veredicto que su N no puede sostener.

## Salida

`scanner/baseline/` (ensemble, store, snapshot) + lifespan thread en `scanner/runtime.py` + endpoint en
`btc_api.py` + tests. Reusa `curar_azar.py` (lógica validada) y `strategy.kill_switch_v2`. Entra a CI
(es código de producción, a diferencia de los estudios en `data/retune/`).
