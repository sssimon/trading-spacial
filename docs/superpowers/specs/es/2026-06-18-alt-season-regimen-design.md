# Pieza de régimen "¿es alt-season?" — diseño

**Fecha:** 2026-06-18
**Subproyecto 1 de la reorientación de Valles.** (Subproyectos 2 y 3 — reframe del detector
per-coin y rediseño profundo de la UI — son specs aparte; ver §10 Fuera de alcance.)
**Estado:** revisado por el roster (6 lentes, 2026-06-18); BLOCKERS+HIGH aplicados.

## Goal

Una pieza de **estado vivo** que computa y expone el régimen del mercado ("¿es alt-season?")
como un **hecho de mercado** — no un veredicto per-coin — y lo exhibe como la cabecera honesta
de Valles. Es donde la evidencia dice que vive la única señal con edge.

## Por qué (evidencia)

El estudio multi-régimen 2020–2025 (`data/retune/2026-06-18-setup-edge-multiregimen/`, panel de
455k filas, n=12.220 hits de la regla-conjunta) midió que **la selección per-coin no tiene edge
en ningún régimen** (Δmediana max_fwd_14d ≤ 0 en todas las celdas, p≈1; el universo vivo
cualquiera —B2— rinde más que el "setup"). El único eje con señal es el **régimen**. Veredicto
del roster (2026-06-18): Valles **exhibe estado, no elige ganadores**; el régimen es "el único
eje donde se puede exhibir dirección sin firmar sobre ningún símbolo" (Axiom-0). Ver
`docs/superpowers/specs/es/2026-06-18-musikito-firma-estadistica-evidencia.md` (cierra su P2).

## Arquitectura

Enfoque elegido: **piggyback en la pasada del screener + núcleo de cálculo puro.** El screener
ya itera todo el universo vivo USDT cada 6h y baja klines diarias por símbolo; breadth y
outperformance se computan en esa **misma pasada** (cero red extra). Única red nueva: 1 llamada a
CoinGecko por pasada.

Cuatro unidades con fronteras limpias:

1. **`regime/alt_season.py`** — núcleo PURO (sin red, sin DB, testeable), espejo de
   `screener/valley_filter.py`.
2. **`tools/run_valley_screener.py`** (ya existe) — capa de I/O; se extiende para acumular las
   contribuciones de régimen en su loop y escribir un segundo snapshot.
3. **`api/alt_season.py`** — router con `GET /alt-season`; sirve el snapshot con
   `freshness.LiveSnapshot`. **Se registra en `btc_api.py`** (ver §Lector).
4. **Cabecera de mercado en el frontend** — consume `/alt-season` (tajada vertical mínima para
   que la pieza esté operativamente viva).

### Owner de frescura
El **`screener_loop`** de `scanner/runtime.py` (cadencia configurable `screener_interval_sec`,
arranque 6h; ya en `_managed_threads`, ya migrado para `/valley-candidates`). **No se crea thread
nuevo.** El punto de extensión real es **`scanner/runtime.py::_regenerate_screener`** (el wrapper
que el loop invoca), no `regenerate` directo; su log debe reflejar también el estado del régimen
(no solo el conteo de candidatas). Se añade una fila a `docs/superpowers/inventario-estado-vivo.md`
como `migrado`.

## Núcleo puro: `regime/alt_season.py`

Constantes de arranque. **PROVISIONALES, sin calibrar contra el panel 2020–2025** — son puntos de
partida, no umbrales derivados (calibración = POST-SHIP, ver §10):

```python
SMA_FAST = 50
RET_WINDOW_DAYS = 30
MIN_HISTORY_DAYS = 50         # cuello de botella = SMA50 (50 cierres). ret_30d sólo necesita 31.
# Umbrales de lean por componente (provisionales, sin calibrar):
BREADTH_ALT = 0.60           # breadth ≥ 0.60 → alts ; ≤ BREADTH_BEAR → btc ; entremedio → neutral
BREADTH_BEAR = 0.40
OUTPERF_ALT = 0.05           # +5% mediana alt sobre BTC ≥ 0.05 → alts ; ≤ -0.05 → btc
OUTPERF_BEAR = -0.05
DOM_ALT = 0.50               # dominancia ≤ 0.50 → alts ; ≥ DOM_BTC → btc
DOM_BTC = 0.58
# Gobierno de evidencia:
COVERAGE_MIN = 0.70          # breadth vota sólo si evaluated/universe ≥ esto
MIN_LIVE_VOTERS = 2          # < 2 votantes vivos → estado "mixto"
```

> Nota de honestidad: breadth (0.60/0.40, banda 20pp) y dominancia (0.50/0.58, banda 8pp) no
> comparten criterio; son provisionales hasta calibrarse. No se finge rigor.

### `symbol_contribution(symbol, bars) -> dict | None`
`bars`: `list[dict]` diarias ascendentes, mismo contrato que `valley_filter`. Devuelve `None` si
`len(bars) < MIN_HISTORY_DAYS`. Si no:

```python
{
  "above_sma50": bool,                  # close_t > media(close, 50)
  "ret_30d": float,                     # (close_t - close_{t-30}) / close_{t-30}
}
```
Sin red, sin estado. Determinista. (`above_sma200`/breadth200 se difiere a v1.1 — ver §10.)

### `compose_regime(alt_contribs, btc_ret_30d, btc_dominance, coverage_ratio) -> dict`
- `alt_contribs`: contribuciones de TODOS los símbolos vivos **excepto BTCUSDT** (breadth de alts).
- `btc_ret_30d`: `ret_30d` de BTCUSDT. `None` si BTC no evaluable hoy (ver invariante abajo).
- `btc_dominance`: fracción 0–1 de CoinGecko, o `None` si la llamada falló/inválida.
- `coverage_ratio`: `evaluated / universe` de la pasada (para el piso de cobertura).

**Tres votantes**, cada uno emite un *lean* ∈ {`alts`, `neutral`, `btc`} y un estado de vida:

1. **breadth50** = `mean(c["above_sma50"] for c in alt_contribs)`. Lean por `BREADTH_*`.
   **Vota sólo si `coverage_ratio ≥ COVERAGE_MIN`**; si no, su `valor` se muestra igual (hecho
   sobre lo que se bajó) pero queda `muerto`/no-votante con razón `cobertura_baja` — para que un
   ban de Binance (429/418) NO se lea como giro de mercado.
2. **outperf_30d** = `median(c["ret_30d"] - btc_ret_30d for c in alt_contribs)`. Lean por
   `OUTPERF_*`. `muerto`/no-votante si `btc_ret_30d is None`.
3. **dominancia_btc** = `btc_dominance`. Lean por `DOM_*`. `muerto`/no-votante si `None`.

**Regla de estado (determinista):** sea `n_alts`, `n_btc`, `n_neutral` el conteo de leans entre
los **votantes vivos**, y `n_live` su total.
- `n_live < MIN_LIVE_VOTERS` → `"mixto"` (régimen indeterminable por falta de evidencia).
- `n_alts > n_btc` **y** `n_alts > n_neutral` → `"alts"`.
- `n_btc > n_alts` **y** `n_btc > n_neutral` → `"btc"`.
- en cualquier otro caso (empate, neutral domina) → `"mixto"`.

El enum `estado ∈ {"alts", "mixto", "btc"}` son **etiquetas de inclinación sin valencia** (como
"invierno"/"primavera"), NO verbos de mando. Prohibido `fuertes`/`manda`/`débil` en el contrato.

Salida:
```python
{
  "estado": "alts" | "mixto" | "btc",
  "componentes": {
    "breadth50":      {"valor": 0.62,  "lean": "alts", "estado": "fresco", "n": 418},
    "outperf_30d":    {"valor": 0.071, "lean": "alts", "estado": "fresco"},
    "dominancia_btc": {"valor": 0.539, "lean": "alts", "estado": "fresco"},
  },
  "votos": {"alts": 2, "neutral": 0, "btc": 0, "vivos": 3},
  "n_alts_evaluadas": 418,
}
```
Cuando un votante está `muerto`, su `valor` es `None` (o el dato parcial en el caso de breadth con
razón `cobertura_baja`), `estado` es `muerto`, NO cuenta en `votos`, y el estado se decide con los
vivos. **Nunca se arrastra un valor viejo como si fuera actual.** `votos.vivos` permite al lector
distinguir un giro de mercado de un giro por pérdida de evidencia.

**Disciplina léxica (doctrina):** el payload no contiene NINGÚN campo per-símbolo ni lenguaje de
consejo/predicción/valencia.

## Capa de I/O: extensión de `tools/run_valley_screener.py`

`build_snapshot` ya recorre `list_live_usdt_spot()` y baja klines por símbolo. Se extiende:

1. Por cada símbolo (misma iteración que `evaluate_symbol`), llamar `symbol_contribution`.
   Acumular en `alt_contribs` si el símbolo ≠ `BTCUSDT`; si es `BTCUSDT`, guardar su `ret_30d`
   como `btc_ret_30d`. Un símbolo omitido por fallo de red NO contribuye (coverage honesto).
   **Invariante:** BTCUSDT está en el universo vivo; el contrato distingue "BTC falló red hoy"
   (`btc_ret_30d=None`, outperf muerto) de "BTC ausente del universo" (no debería pasar; se loguea).
2. Tras el loop, la dominancia vive tras una función **aislada y mockeable**
   `_fetch_dominance() -> float | None` (espejo de `_fetch_daily_klines`):
   `GET https://api.coingecko.com/api/v3/global`, `timeout=(3.05, 10)` (connect, read).
   Éxito → `data["data"]["market_cap_percentage"]["btc"] / 100`, **validado `0 < dom < 1`**.
   Cualquier fallo (`requests.RequestException, KeyError, TypeError, ValueError`) o fuera de rango
   → `None` (degradación elegante; sin excepción que tumbe la pasada). Nota: el `read-timeout` (10s)
   acota el teardown del thread (`stop_managed_threads` da 2s de gracia).
3. `compose_regime(...)` → escribir `data/alt_season.json` **atómicamente**: `tempfile` en el
   MISMO directorio + `os.replace()`. **NO se hereda** el patrón no-atómico `open("w")+json.dump`
   de `valley_candidates.json` (evita que un `GET` concurrente lea truncado y degrade a "muerto").
   Forma:
   ```json
   {
     "generated_at": "<ISO-8601 UTC, cierre de la pasada>",
     "coverage": {"universe": 430, "evaluated": 419, "complete": false},
     "dominancia_fetch": {"ok": true, "fetched_at": "<ISO>", "source": "coingecko/global"},
     "regime": { ...salida de compose_regime... }
   }
   ```
   **Orden de escritura:** `alt_season.json` se escribe DESPUÉS de `valley_candidates.json`, y
   ambos `generated_at` provienen del MISMO cierre de pasada (se pasa un timestamp único). El
   breadth es "as-of la pasada" (computado a lo largo de la pasada serial), no instantáneo.

`regenerate()` escribe ambos snapshots en la misma pasada.

**Infra:** CoinGecko es una dependencia externa NUEVA. Sin API key (tier gratuito). 1 llamada cada
6h (muy por debajo del rate limit). Su salud se refleja en `dominancia_fetch.ok` y en el `estado`
del componente — nunca se finge frescura.

## Lector: `GET /alt-season` (`api/alt_season.py`)

Router nuevo. **Se registra en `btc_api.py`** (`import` + `app.include_router(alt_season_router)`
junto a `valleys_router`, ~btc_api.py:317); sin esa línea el endpoint vive solo en `TestClient` y
está muerto en prod. Lee `data/alt_season.json` y lo envuelve:

```python
LiveSnapshot(payload, generated_at=snap["generated_at"],
             umbral_seg=api.valleys.FRESCURA_VALLES_SEG).to_response()
```

- **Umbral reconciliado:** usar `api.valleys.FRESCURA_VALLES_SEG` (12h = 43200), el MISMO que el
  lector hermano `/valley-candidates`, porque comparten writer y loop — dos cabeceras del mismo
  snapshot no pueden reportar frescura discordante. Si se toca `screener_interval_sec`, el umbral
  (anclado a ~2× interval) se re-deriva en un solo lugar.
- Archivo ausente / `generated_at` faltante ⟹ `LiveSnapshot` lo clasifica `muerto`. **Nunca un
  empty mudo.** Con la escritura atómica, el lector no ve truncamiento (no hay falso "muerto").
- **Doble nivel de frescura** (honra #8): la EXTERNA (cadencia del screener, vía `LiveSnapshot`,
  UNA `frescura` inyectada por `to_response`) y la INTERNA de la dominancia. `LiveSnapshot` NO
  cubre la dominancia: su frescura vive como campo de payload (`dominancia_fetch` + el `estado` del
  componente) **por contrato**, y el frontend lo trata como autoridad para ese eje.
- **`fresco` significa "el cálculo es reciente", NO "la afirmación de mercado sigue vigente".** Un
  snapshot de 3h puede decir `alts` mientras el régimen real ya giró; eso se aclara en el contrato.

Fila a añadir en `docs/superpowers/inventario-estado-vivo.md`:

| Reader | Writer | Owner de frescura en prod | Frescura en contrato | Estado |
|---|---|---|---|---|
| `GET /alt-season` | `tools.run_valley_screener.regenerate` (vía `_regenerate_screener`) | `screener_loop` (**trading-scanner.service**, 6h) | `LiveSnapshot` (+ frescura interna de dominancia en payload) | **migrado** |

## Frontend: cabecera de mercado

- **Dónde:** cabecera arriba de Valles (sobre Pick / lista / idea de moneda). El régimen es UN
  hecho para todo el mercado → vive una sola vez, arriba. Componente nuevo en
  `frontend/src/components/valles/`, montado en el contenedor de Valles (`ValleysFlow`).
- **Archivos:** reusar el tipo `Frescura` de `frontend/src/types.ts`; función de fetch en
  `frontend/src/api.ts` espejo de `fetchValleyCandidates`; estilo cálido consistente
  (cross-ref `docs/superpowers/specs/es/2026-06-14-valles-rediseno-calido-design.md`). Lector
  mayor: ≥18px, contraste AA.
- **Qué muestra:** el estado como **descripción sin valencia** (`Inclinación del mercado: hacia
  alts / mixta / hacia BTC` — copy calibrable, sin verbo de mando) + los 3 componentes como hechos
  (breadth % con su `n`, outperf %, dominancia %) + la frescura
  (`fresco/rancio/muerto`, y la frescura interna de la dominancia si está `muerta`). Exhibir el `n`
  de breadth (no es un porcentaje comparable a otros ejes). **El frontend muestra el VALOR (y `n`)
  de cada componente pero NO su lean por etiqueta** — simplificación intencional a favor de la
  doctrina: el estado compuesto (`alts`/`mixto`/`btc`) ya resume la inclinación; pintar además
  el lean per-componente añadiría valencia redundante en pantalla.
- **La frase honesta**, dicha una vez, sin regañar (p.ej. *"Lo que más mueve el resultado es el
  régimen del mercado, no la moneda que elijas."*). Su **presencia es requerida** (AC); la
  redacción exacta es calibrable vía `solace-wren`.
- **Relación con per-coin:** la vista rica queda subordinada a la cabecera; sigue siendo hechos
  descriptivos, sin claim de selección. **Sin** modulación per-coin por régimen: el estado del
  régimen NO altera color/orden/énfasis de la lista de coins. Costura textual entre cabecera y
  lista.

## Doctrina anti-veredicto

- **Permitido:** un estado de régimen es un hecho de **mercado** (clima), no per-símbolo. La línea
  roja de la doctrina es la **singularidad** (firmar sobre UN símbolo), no la **dirección** de
  mercado — el único eje donde se exhibe dirección sin firmar sobre ninguna moneda (Axiom-0).
- **Prohibido:** (a) modular el per-coin con el régimen (color/orden/énfasis/copy); (b) un score
  0-100 que lea como medidor de compra (por eso es voto con componentes visibles, no un número);
  (c) lenguaje de consejo/predicción; (d) **valencia en el enum/copy** (`fuertes`/`manda`/`débil`);
  (e) ranking oculto.
- Nota: breadth50 y outperf_30d comparten dataset (no son ejes independientes); se exhiben por
  separado para que el lector lo vea, no se afirma independencia.
- El payload pasa la misma disciplina léxica que los 5 candados server-side existentes. La frase
  honesta ES la doctrina dicha fuerte: entrega la verdad incómoda (régimen > coin) sin esconderla.

## Testing (TDD)

**Núcleo puro (`tests/test_alt_season.py`):**
- `symbol_contribution`: `above_sma50` y `ret_30d` correctos; `< MIN_HISTORY_DAYS` → `None`;
  frontera exacta a 50 barras.
- `compose_regime` — batería determinista de la regla de estado:
  - 3 votos alts → `"alts"`; 2 alts 1 btc → `"alts"`; 1 alts 1 btc 1 neutral → `"mixto"` (empate);
    2 neutral 1 alts → `"mixto"` (neutral domina); 2 alts (dominancia muerta) → `"alts"`;
    1 votante vivo → `"mixto"` (`MIN_LIVE_VOTERS`); 0 vivos → `"mixto"`.
  - Fronteras de umbral exactas (breadth 0.60 y 0.40; outperf ±0.05; dominancia 0.50 y 0.58).
  - `coverage_ratio < COVERAGE_MIN` → breadth `muerto`/no-vota con razón `cobertura_baja`, valor
    aún presente; `btc_ret_30d=None` → outperf no vota; `btc_dominance=None` → dominancia no vota.
  - `votos.vivos` refleja el conteo correcto.

**Extensión del screener (`tests/test_run_valley_screener.py` — se ACTUALIZA):**
- La pasada acumula contribuciones y escribe `alt_season.json` junto a `valley_candidates.json`
  (orden: candidatas primero), ambos con el mismo `generated_at`.
- Un símbolo omitido por fallo no corrompe el régimen (coverage honesto, `complete=False`).
- BTCUSDT se excluye de `alt_contribs` y su `ret_30d` se usa como referencia.
- **Los 2 tests vigentes se actualizan para mockear también `_fetch_dominance`** (hoy solo mockean
  `_fetch_daily_klines`/`list_live_usdt_spot`). Ningún test nuevo lleva el marker `network`: todos
  mockean la red (el gate corre `-m "not network"`).
- Escritura atómica: verificar que se usa `os.replace` (no `open("w")` directo sobre el destino).

**Degradación CoinGecko:** mock de `_fetch_dominance` que falla/devuelve shape inesperado/fuera de
rango → `dominancia_fetch.ok=false`, componente `muerto`, estado se compone con 2 votantes, sin
excepción.

**Endpoint (`tests/test_alt_season_api.py`):**
- `/alt-season` trae `frescura`; snapshot fresco → `estado=fresco`.
- `generated_at` viejo (> `FRESCURA_VALLES_SEG`) → `rancio`.
- Archivo ausente → `muerto` (no un 200 con payload vacío mudo).

**Test de aceptación (doctrina):** el payload de `/alt-season` NO contiene lenguaje de
veredicto/consejo/valencia (lista negra: `comprar`, `vender`, `subirá`, `entra`, `señal de compra`,
`mandan`, `manda`, `fuertes`, `débil`, `débiles`) ni ningún campo per-símbolo — solo hechos de
mercado + estado + votos + frescura.

**Frontend:**
- Unit (vitest): el banner renderiza estado + 3 componentes + frescura desde un `/alt-season`
  mockeado; muestra el degradado cuando la dominancia está `muerta`.
- e2e (Playwright, `frontend/e2e/`): la cabecera aparece en el stack real **y** el estado del
  régimen NO modula color/orden/énfasis de la lista de coins (assert de doctrina en UI).

## Acceptance criteria

1. `regime/alt_season.py` es puro (sin imports de red/DB) y sus dos funciones pasan sus tests, con
   la regla de estado determinista.
2. La pasada del screener escribe `data/alt_season.json` (atómico) con `regime` + `coverage` +
   `dominancia_fetch`, sin red extra más allá de 1 llamada a CoinGecko.
3. `GET /alt-season` (registrado en `btc_api.py`) emite SIEMPRE la frescura vía `LiveSnapshot`;
   ausencia → `muerto`; umbral reconciliado con `FRESCURA_VALLES_SEG`.
4. CoinGecko caído/invalido ⟹ estado se compone con los votantes vivos y lo declara; nunca tumba
   la pasada ni finge frescura; `< MIN_LIVE_VOTERS` → `mixto`.
5. El payload pasa el test de aceptación de doctrina (sin veredicto/valencia, sin campo
   per-símbolo).
6. La cabecera del frontend muestra estado + componentes + frescura + la frase honesta, y no
   modula la lista de coins.
7. Fila `migrado` añadida al inventario de estado vivo; `_regenerate_screener` loguea el estado del
   régimen.
8. Gate del repo en verde: `python -m pytest tests/ -m "not network" -n auto -q` y el job de
   frontend (vitest).

## Docs / scaffold tocados

- Actualizar `.mex/patterns/correr-screener-valles.md` (nuevo output `alt_season.json` + dependencia
  CoinGecko).
- Puntero desde el doc de evidencia (`2026-06-18-musikito-firma-estadistica-evidencia.md`, su P2) a
  este spec.
- `mex log` del cierre del subproyecto.

## Fuera de alcance (explícito)

- **Subproyecto 2:** reframe del detector per-coin (`valley_filter` deja de gatear por `pct_rango`
  invariante al orden; exhibe estado sin claim de selección). Spec aparte.
- **Subproyecto 3:** rediseño profundo de la UI per-coin / layout de subordinación.
- **v1.1:** (a) componente de **tendencia** de la dominancia — recién ahí se introduce
  `data/alt_season_dominance_history.json`, con su PROPIA fila de inventario + escritura atómica
  (en v1 NO se escribe historia); (b) `breadth200` como hecho informativo.
- **POST-SHIP:** calibrar los seis umbrales contra el panel 2020–2025.
- Cero claims de retorno: la pieza exhibe estado, no promete rendimiento.
- No se toca `strategy/regime.py` (régimen macro de BTC del scanner — concepto distinto).
