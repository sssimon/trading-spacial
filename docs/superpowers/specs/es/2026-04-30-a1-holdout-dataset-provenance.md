# A.1 — Provenance del holdout de validación

**Fecha de lock:** 2026-04-30T07:52:17.939277+00:00
**Commit del lock:** `9dcde3d0f32848fe2eb5cdc4ce3b001333d94a07`
**Estado del árbol al lock:** `git_clean = true`
**Epic:** [#246](https://github.com/sssimon/trading-spacial/issues/246)
**Ticket:** [#247](https://github.com/sssimon/trading-spacial/issues/247)
**Autor:** Samuel Ballesteros (decisión); Claude Opus 4.7 (ejecución)

Este documento es la **fuente autoritativa** del corte de validación. A.6 (#252) lo cita textualmente como required reading. A.4 (#250) hereda de aquí los caveats que limitan qué constituye validación honesta.

---

## 1. Decisiones cerradas

| # | Decisión | Resuelta |
|---|---|---|
| 1 | Corte temporal | **12 meses, fixed cutoff desde la fecha del lock**. Sin rolling. |
| 2 | Snapshot retroactivo vs going-forward | **Retroactivo, snapshot único** |
| 3 | Storage | **Directorio dedicado `data/holdout/`** con OHLCV + F&G + funding + manifest |
| 4 | Cobertura F&G/funding | **Lockeable retroactivo** con caveat de drift documentado en manifest |

### Rationale del corte (12 meses)

Se evaluaron 3 opciones (3, 12, 18 meses). El comparativo fue:

| Opción | Holdout | Train restante | Evaluación |
|---|---|---|---|
| 3 meses | 90 días | ~5 años | sample muy chico — #246 ya nota "1 señal real en 90 días"; no soporta walk-forward de ≥3 períodos sin perder potencia estadística |
| **12 meses (elegida)** | 365 días | ~4 años | balance: walk-forward 3×4m o 4×3m es viable; cubre mix de regímenes recientes; preserva train suficiente |
| 18 meses | 545 días | ~3.5 años | máxima evidencia OOS pero deja a JUP con 9m de train (arranca 2024-02), PENDLE con 16m — riesgo de tuning empobrecido en símbolos jóvenes |

**Elegida: 12 meses.** Decisión humana de Samuel Ballesteros; no es óptimo automático.

### Rationale de fixed vs rolling

El issue original (#247) sugería *rolling 90 días desde latest scan*. **Rolling rompe walk-forward**: el blanco de evaluación se mueve con el tiempo, "pasar el bar" deja de ser una afirmación reproducible (un PR puede pasar hoy y fallar mañana sólo porque el holdout se desplazó).

Decisión: **fixed**. El corte queda congelado a la fecha del lock (`2025-04-30T00:00:00+00:00`). Sólo se abrirá un nuevo segmento de holdout cuando A.4 ya haya ejecutado contra el actual y se documenten las conclusiones.

### Rationale de retroactivo

Las opciones eran:

- **Going-forward**: el holdout empieza HOY (lock day) y se llena durante 12 meses → mata el cronograma; bloquea Epic B otro año entero.
- **Retroactivo (elegida)**: snapshot único hoy desde lo que tenemos en `data/ohlcv.db` + caches de F&G/funding. El manifest con hash + timestamp ES la verdad oficial.
- **Híbrido**: OHLCV retroactivo, F&G/funding "best-effort retroactivo + lock going-forward" — más complejo, sin beneficio operativo claro.

El riesgo del retroactivo es que F&G/funding pueden ser revisados por el proveedor; eso queda capturado como caveat `DRIFT_NOT_AUTODETECTABLE_FROM_LOCK` y obliga a A.4 a re-fetch + diff.

### Rationale de `data/holdout/` (directorio dedicado)

Las opciones eran:

- **A**: DB separada `data/ohlcv_holdout.db` con `chmod 444` — protege OHLCV pero F&G/funding quedan fuera, requieren un guard separado.
- **B**: flag `holdout=1` en `data/ohlcv.db` — solo code-level; olvido del filtro = contaminación silenciosa, débil.
- **C (elegida)**: directorio dedicado `data/holdout/` con OHLCV + F&G + funding + manifest, filesystem read-only — un solo lugar congelado, cobertura completa de fuentes en un punto.

---

## 2. Layout del lock

```
data/holdout/
├── ohlcv.sqlite        # OHLCV de los 10 símbolos curados, holdout window
├── fng.parquet         # Fear & Greed (alternative.me), holdout window
├── funding.parquet     # Binance Futures funding rate BTC, holdout window
├── MANIFEST.json       # hashes + commit + timestamps + caveats
└── README.md           # quick reference (apunta a este doc)
```

**Permisos:** todos los archivos son `-r--r--r--` (444), todos los directorios son `dr-xr-xr-x` (555). Re-escribir requiere `chmod +w` explícito.

**Política de versionado (git):** los archivos pequeños (MANIFEST.json, README.md, fng.parquet, funding.parquet) **se commitean**. `ohlcv.sqlite` (~158 MB) **está gitignored** porque excede el límite de 100 MB por archivo de GitHub — su SHA-256 está en el manifest, así que cualquiera puede regenerarlo bit-a-bit desde `data/ohlcv.db` y verificar contra el hash. El procedimiento exacto está en la sección 6 (Reproducibilidad).

---

## 3. Cobertura de fuentes

### 3.1 OHLCV — `ohlcv.sqlite`

- **Hash SHA-256:** `545d7b18643f3658ebef0ce5f8b14112f5cad68532755a8b560cd17bd527f91d`
- **Origen:** `data/ohlcv.db` filtrado por `symbol IN curated_symbols AND open_time >= holdout_start_ms`
- **Total filas:** 1,162,898
- **Schema:** idéntico al source (`ohlcv`, `meta`, `symbol_earliest`)
- **Símbolos:** BTCUSDT, ETHUSDT, ADAUSDT, AVAXUSDT, DOGEUSDT, UNIUSDT, XLMUSDT, PENDLEUSDT, JUPUSDT, RUNEUSDT (10 — `DEFAULT_SYMBOLS` en `btc_scanner.py`)
- **Timeframes:** 1d, 1h, 4h, 5m
- **Ventana de cobertura por timeframe (todos los símbolos):**
  - 1d: `2025-04-30T00:00:00 → 2026-04-28T00:00:00` (364 barras por símbolo, ETH 362 por gap)
  - 1h: `2025-04-30T00:00:00 → 2026-04-29T12:00:00` (8,749 barras por símbolo)
  - 4h: `2025-04-30T00:00:00 → 2026-04-29T08:00:00` (2,187 barras por símbolo)
  - 5m: `2025-04-30T00:00:00 → 2026-04-29T13:05:00` (104,990 barras por símbolo)
- **Drift caveat:** ninguno relevante. Las barras OHLCV son inmutables una vez registradas por Binance/Bybit. `data/_storage.py` es append-only en código.

### 3.2 Fear & Greed Index — `fng.parquet`

- **Hash SHA-256:** `ede33681d6801e94b8b5aa248146e09d7cbff1f24f6d899e66d4b1084c81b7e7`
- **Origen:** `https://api.alternative.me/fng/?limit=0` (full history) → filtrado a holdout window
- **Filas:** 363 valores diarios
- **Ventana:** `2025-04-30 → 2026-04-27`
- **Fetched at:** `2026-04-30T07:52:29.144228+00:00`
- **Drift caveat:** alternative.me **puede revisar valores históricos** retroactivamente (corrección de inputs sociales o agregación). El hash congela el snapshot al `fetched_at_utc`. **Detectar drift requiere re-fetch + diff** — obligación heredada por A.4 (caveat `DRIFT_NOT_AUTODETECTABLE_FROM_LOCK`).

### 3.3 BTC Funding Rate — `funding.parquet`

- **Hash SHA-256:** `8cead903e9095e8ee7ccb4ae51fe367525777e37b9e12a505a4084dacc7e38c1`
- **Origen:** `https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT` (paginado) → filtrado a holdout window
- **Filas:** 1,088 entradas (períodos 8-horarios)
- **Ventana:** `2025-04-30T00:00:00.001 → 2026-04-27T08:00:00`
- **Símbolos cubiertos:** BTCUSDT (sólo)
- **Símbolos uncovered y rationale:** los otros 9 símbolos curados **no** tienen funding rate snapshoteado. Justificación: `strategy/regime.py` líneas 240 y 349 usan **explícitamente BTC funding rate como señal global** del detector de régimen, para todos los símbolos. Snapshotear sólo BTC matches lo que el scoring de producción consume. Si A.4 propone un detector per-symbol, debe extender este snapshot ese momento, no antes.
- **Drift caveat:** Binance puede revisar funding rates históricos. Mismo tratamiento que F&G — A.4 debe re-fetch + diff.

### 3.4 Fuentes uncovered

Vacío. Todo input al regime detector y al scoring de producción está cubierto:

- ✅ OHLCV (input al scoring + indicadores)
- ✅ F&G (input al regime detector)
- ✅ Funding rate BTC (input al regime detector)
- ✅ Indicadores derivados (LRC, RSI, BB, SMA, ATR, ADX) — recomputables determinísticamente desde OHLCV

Si A.2 / A.4 / futuras versiones del scoring incorporan una nueva fuente externa, **debe extenderse este snapshot y rotarse el lock**, no parchear el rango.

---

## 4. Caveats heredados — restricciones para A.4 (#250) y A.6 (#252)

Tres caveats **bloqueantes**. Están además codificados machine-readable en `data/holdout/MANIFEST.json` bajo `caveats`.

### 4.1 `RE_TUNE_REQUIRED_FOR_A4`

> Los `atr_sl_mult/tp/be` actuales en `config.json["symbol_overrides"]` fueron tuneados sobre **todo el histórico**, incluyendo el rango que ahora pertenece al holdout. Evaluar esos parámetros directamente contra el holdout es **leakage**.

**Obligación heredada por A.4:** retunear sobre el segmento de train `[ohlcv earliest, holdout_start - 1 bar]` ANTES de evaluar contra este holdout. La validación honesta requiere que los parámetros no hayan visto los datos contra los que se mide su generalización.

### 4.2 `REGIME_COMPOSITION_NOT_GUARANTEED`

> La ventana de 12 meses puede no cubrir todos los regímenes (bull/bear/neutral). Si la ventana resulta dominada por un régimen, A.4 no puede testear SHORT gating o transiciones BULL/BEAR out-of-sample con cobertura equivalente.

**Obligación heredada por A.4:** reportar la mezcla bull/bear/neutral observada en la ventana (reconstruida desde los componentes locked F&G + funding + price) y declarar explícitamente las coverage gaps antes de afirmar validación.

### 4.3 `DRIFT_NOT_AUTODETECTABLE_FROM_LOCK`

> Los hashes de F&G y funding congelan el snapshot tomado al `fetched_at_utc`. Las revisiones retroactivas del proveedor **NO son detectables desde este lock**.

**Obligación heredada por A.4:** re-fetch F&G y funding para la ventana del holdout desde sus APIs y diff contra el snapshot locked. Cualquier divergencia debe reportarse (no overridearse silenciosamente).

---

## 5. Guards (decisión #247: A + B con B reforzado)

### 5.1 Guard A — `data/holdout_access.py` (ergonomía + huella explícita)

Wrapper único `open_holdout(rel_path, *, evaluation_mode: bool) -> Path`. Levanta `HoldoutAccessError` si `evaluation_mode is not True`. También refusa path traversal fuera de `data/holdout/`.

**Política explícita: no hay opt-out vía monkey-patch ni env var.** Costo/beneficio negativo, frágil. A es opt-in deliberadamente — su valor es marcar inequívocamente cada acceso legítimo.

### 5.2 Guard B — `tests/test_holdout_isolation.py` (red estructural)

AST scanner whitelist-based. Escanea **todo el repo** por defecto. Whitelist explícita en `HOLDOUT_LEGITIMATE_MODULES`:

```python
HOLDOUT_LEGITIMATE_MODULES = {
    "data/holdout_access.py",
    "scripts/lock_holdout.py",
    "tests/test_holdout_isolation.py",
}
```

Detecta los 4 patterns reforzados:

1. **String literal** que contiene `"data/holdout"` o que es `"holdout"` como segmento de path
2. ***.join(..., "holdout", ...)** — cualquier `Attribute.join()` con arg literal `"holdout"`
3. **`pathlib.Path(...) / "holdout" / ...`** — `BinOp(op=Div)` con operando string literal
4. **f-strings con `holdout`** — partes literales de `JoinedStr`

Skipea **docstrings** para no penalizar manifests/headers/docs citados en código. Comments no son visibles al AST por construcción.

Tests demostrativos en el mismo archivo:

- `test_pattern_1_string_literal_is_caught`
- `test_pattern_2_os_path_join_is_caught`
- `test_pattern_3_pathlib_division_is_caught`
- `test_pattern_4_fstring_is_caught`
- `test_pattern_4_fstring_with_holdout_segment`
- `test_docstrings_are_skipped`
- `test_function_docstring_is_skipped`
- `test_unrelated_string_does_not_fire`
- `test_no_holdout_references_in_non_whitelisted_modules` (real guard)
- `test_wrapper_raises_without_evaluation_mode` (A integration)
- `test_wrapper_path_traversal_is_refused` (A integration)
- `test_wrapper_module_exists_and_exposes_open_holdout` (whitelist sanity)

**Para introducir un nuevo módulo legítimo (e.g. A.2 walk-forward harness):** agregar el path a `HOLDOUT_LEGITIMATE_MODULES` con justificación visible en el PR. La whitelist es review-gated; el reviewer es el backstop estructural humano.

---

## 6. Reproducibilidad

Para regenerar este lock bit-a-bit (modulo el drift de proveedor de F&G/funding):

```bash
git checkout 9dcde3d0f32848fe2eb5cdc4ce3b001333d94a07
rm -rf data/holdout/  # requiere chmod +w previo en macOS/Linux
python scripts/lock_holdout.py
```

El script:

1. Verifica que `data/holdout/` no existe (refusa overwrite).
2. Verifica `git_status` clean (sólo tracked files; untracked `??` permitido).
3. Calcula `holdout_start = lock_timestamp - 365d` redondeado a `00:00 UTC`.
4. Copia OHLCV filtrado por símbolos curados + ventana.
5. Fetch F&G + funding via `backtest.py::get_historical_fear_greed/funding_rate`.
6. Slice a la ventana, escribe parquet.
7. Calcula SHA-256 de cada artifact.
8. Escribe `MANIFEST.json` con todo.
9. `chmod -R 444/555` el directorio.

**No determinismo conocido:** `lock_timestamp_utc` y `fetched_at_utc` capturan el momento del run. Hash de OHLCV es determinista (mismo subset, mismo orden, mismo schema). Hash de F&G/funding **puede divergir si el proveedor revisó valores entre runs** — es exactamente el drift que el caveat `DRIFT_NOT_AUTODETECTABLE_FROM_LOCK` advierte.

---

## 7. Cuándo se rota este lock

**No se rota.** Este es el holdout autoritativo de la metodología epic A.

**Cuándo se abre un nuevo holdout** (segundo lock, no reemplazo del primero):

- Cuando A.4 (#250) haya ejecutado contra este holdout y se hayan documentado las conclusiones.
- Cuando se acumule suficiente data nueva (post-2026-04-29) como para justificar un segundo período de validación independiente.
- Cuando se incorpore una nueva fuente al scoring/regime detector — el lock actual queda obsoleto y se rota a un snapshot que cubra la nueva fuente.

Cualquier rotación requiere actualizar este doc y abrir un PR explícito. El lock anterior **no se borra**; se mantiene para auditoría histórica.

---

## 8. Referencias cruzadas

- Issue **#247** — `feat(strategy-validation): reserve and lock holdout dataset` (este ticket)
- Epic **#246** — `epic: strategy validation methodology — walk-forward on intact holdout`
- Policy **#271** — `DO NOT INVITE additional users until Epic A validated and Epic B implemented`
- Issue **#272** — `methodology: re-baseline backtest numbers post-phantom-fix` (bloquea A.4 + A.6 — los números autoritativos previos a 2026-04-27 están inflados por phantom profit pre-#223/#224)
- Issue **#273** — `methodology: audit and quarantine data/backtest/*.csv — isolate dead-symbol artifacts post-#135` (bloquea A.4 operacionalmente)
- PRs **#223 / #224** — phantom-profit fix; sin ellos, los números de los docs `2026-04-17-formula-ganadora` y `2026-04-18-documento-completo-sistema-trading` están inflados
- Tickets dependientes: **#248** (A.2 walk-forward harness), **#249** (A.3 quantitative bar), **#250** (A.4 re-evaluate), **#251** (A.5 codify lessons), **#252** (A.6 publish methodology — required reading)

---

*Este doc es la fuente autoritativa del corte. Si conflicta con `MANIFEST.json`, el manifest manda; abrir un issue para reconciliar.*
