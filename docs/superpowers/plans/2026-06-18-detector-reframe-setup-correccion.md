# Reframe del detector per-coin (SP2) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cambiar el gate del detector de "valle" (amplitud 84d ciega al orden) a "parte baja del rango" (`pos_in_30d_range ≤ 0.25`), exhibir RSI/SMA/consolidación/volumen como hechos, y reframear el frontend mínimo honesto (matar las frases falsas de "valle", exhibir el under-rendimiento medido).

**Architecture:** Función pura nueva `measure_setup` (espejo de `measure_consolidation`) calcula 7 hechos; solo `pos_in_30d_range` gatea. El candidato cambia de claves; `run_valley_screener` y `api/valleys.py` propagan por spread sin tocar código. El frontend cambia el contrato de tipos y el copy. `measure_consolidation` + el probe legacy quedan intactos.

**Tech Stack:** Python (stdlib `statistics`), FastAPI, pytest; React + TypeScript + vitest.

**Spec:** `docs/superpowers/specs/es/2026-06-18-detector-reframe-setup-correccion-design.md`.
**Branch:** `feat/detector-reframe-setup` (creada, con el commit del spec).

## Global Constraints

- **Doctrina léxica** (test léxico + `git grep`): el payload y `frontend/src` NO contienen `valle`, `va a subir`, `señal`, `fuertes`, `débil`, `cazaba`, `tiene jugada`, `setup de corrección`. `musikito`/`filtro de 2019` solo como PROCEDENCIA y acompañados del resultado medido.
- **AC7 (honestidad, no-negociable):** la costura per-coin exhibe el hecho medido — *en el único régimen medido (alt-bull 2019) este filtro NO le ganó al azar de alts: 14d 9.92% vs 12.54%* — y el objeto se nombra por procedencia, no por tesis de mercado.
- **Solo `pos_in_30d_range` es gate.** Los otros 6 hechos son exhibidos, nunca thresholded.
- **`measure_consolidation`, el probe `valle_calidad_probe`, y sus tests quedan INTACTOS.**
- **Gate del repo:** `python -m pytest tests/ -m "not network" -n auto -q` y `cd frontend && npx vitest run && npx tsc --noEmit`. Ningún test nuevo lleva marker `network`.
- **Tuteo venezolano** (nunca voseo).

---

## Task 1: Núcleo puro — `_wilder_rsi` + `measure_setup`

**Files:**
- Modify: `screener/valley_filter.py` (añadir constantes + 2 funciones; NO tocar lo existente)
- Test: `tests/test_valley_filter.py` (añadir clase `TestMeasureSetup`)

**Interfaces:**
- Produces: `measure_setup(bars: list[dict]) -> dict` con claves `{pos_in_30d_range, rsi14, pct_vs_sma20, pct_vs_sma50, consol_30d, vol_ratio, drawdown_from_90h}` (todas `float`); `_wilder_rsi(closes: list[float], period=14) -> float`. Constantes `SETUP_POS_MAX=0.25`, `RANGE_WINDOW_DAYS=30`, `SMA_FAST=20`, `SMA_SLOW=50`, `DRAWDOWN_WINDOW_DAYS=90`, `VOL_FAST_DAYS=3`, `VOL_SLOW_DAYS=30`.

- [ ] **Step 1: Escribir los tests que fallan**

Añadir al final de `tests/test_valley_filter.py` (reusa `_bar`/`_serie` del archivo):
```python
from screener.valley_filter import measure_setup, _wilder_rsi, SETUP_POS_MAX


def _serie_rango(n, lo, hi, last_close, vol=2_000_000.0):
    """n barras vivas; las últimas 30 barran [lo, hi] y la última cierra en last_close.
    Las primeras n-30 quedan planas en el extremo opuesto para fijar amplitud."""
    bars = []
    anchor = hi if last_close <= (lo + hi) / 2 else lo
    for i in range(n):
        if i < n - 30:
            c = anchor
        else:
            frac = (i - (n - 30)) / 29.0
            c = anchor + (last_close - anchor) * frac
        bars.append(_bar(i * 86_400_000, c, vol, high=c * 1.005, low=c * 0.995))
    return bars


class TestMeasureSetup:
    def test_pos_in_30d_range_piso(self):
        bars = _serie_rango(150, lo=0.92, hi=1.20, last_close=0.93)
        out = measure_setup(bars)
        assert out["pos_in_30d_range"] < 0.25       # cuartil inferior

    def test_pos_in_30d_range_techo(self):
        bars = _serie_rango(150, lo=0.92, hi=1.20, last_close=1.19)
        out = measure_setup(bars)
        assert out["pos_in_30d_range"] > 0.75       # cuartil superior

    def test_claves_exactas(self):
        out = measure_setup(_serie(150))
        assert set(out.keys()) == {
            "pos_in_30d_range", "rsi14", "pct_vs_sma20", "pct_vs_sma50",
            "consol_30d", "vol_ratio", "drawdown_from_90h"}

    def test_denominador_cero_no_revienta(self):
        # libro plano (high==low==close) → sin nan/inf en ningún hecho
        planas = [_bar(i * 86_400_000, 1.0, 2_000_000.0, high=1.0, low=1.0) for i in range(150)]
        out = measure_setup(planas)
        for k, v in out.items():
            assert v == v and abs(v) != float("inf"), f"{k} es nan/inf"

    def test_drawdown_no_positivo(self):
        out = measure_setup(_serie_rango(150, lo=0.92, hi=1.20, last_close=0.95))
        assert out["drawdown_from_90h"] <= 0.0

    def test_rsi_subida_pura_alto(self):
        closes = [1.0 + i * 0.01 for i in range(40)]
        assert _wilder_rsi(closes, 14) > 90.0

    def test_rsi_bajada_pura_bajo(self):
        closes = [2.0 - i * 0.01 for i in range(40)]
        assert _wilder_rsi(closes, 14) < 10.0

    def test_rsi_pocos_datos_neutral(self):
        assert _wilder_rsi([1.0, 1.1], 14) == 50.0
```

- [ ] **Step 2: Correr para verque fallan**

Run: `python -m pytest tests/test_valley_filter.py::TestMeasureSetup -q`
Expected: FAIL con `ImportError: cannot import name 'measure_setup'`.

- [ ] **Step 3: Implementar las constantes + funciones**

En `screener/valley_filter.py`, tras el bloque de constantes de consolidación (después de `VOL_PERCENTILE_WINDOW_DAYS = 365`), añadir:
```python
# ── Setup "parte baja del rango" — réplica del filtro histórico de musikito (SP2) ──
# Provisionales, sin calibrar (POST-SHIP). Mediana de musikito 2019 = 0.165; corte 0.25
# = el que midió el estudio multi-régimen. SOLO pos_in_30d_range gatea; el resto son hechos.
SETUP_POS_MAX = 0.25
RANGE_WINDOW_DAYS = 30
SMA_FAST = 20
SMA_SLOW = 50
DRAWDOWN_WINDOW_DAYS = 90
VOL_FAST_DAYS = 3
VOL_SLOW_DAYS = 30
```

Tras `measure_consolidation` (antes de `liquidity_value`), añadir:
```python
def _wilder_rsi(closes: list[float], period: int = 14) -> float:
    """RSI de Wilder sobre la última barra (semilla = promedio simple de los primeros
    `period` cambios, luego suavizado de Wilder). Hecho EXHIBIDO, no gate. 50.0 si no
    hay datos suficientes; 100.0 si no hubo bajadas en la ventana suavizada."""
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def measure_setup(bars: list[dict]) -> dict:
    """Hechos del setup "parte baja del rango" (reframe SP2). PURA. SIEMPRE 7 claves.
    Solo pos_in_30d_range es gate (en evaluate_symbol); el resto son hechos EXHIBIDOS.
    Todo denominador clampeado — datos ralos/muertos producen un hecho degradado, NUNCA
    nan/inf (espejando la protección `or 1.0` de measure_consolidation)."""
    closes = [float(b["close"]) for b in bars]
    qvols = [float(b["quote_volume"]) for b in bars]
    close = closes[-1]
    eps = 1e-9 * close if close else 1e-9

    rango = bars[-RANGE_WINDOW_DAYS:]
    lo30 = min(float(b["low"]) for b in rango)
    hi30 = max(float(b["high"]) for b in rango)
    pos = (close - lo30) / max(hi30 - lo30, eps)

    sma20 = (sum(closes[-SMA_FAST:]) / min(len(closes), SMA_FAST)) or eps
    sma50 = (sum(closes[-SMA_SLOW:]) / min(len(closes), SMA_SLOW)) or eps
    med30 = median([float(b["close"]) for b in rango]) or eps
    consol30 = (hi30 - lo30) / med30 * 100.0

    qv30 = median(qvols[-VOL_SLOW_DAYS:]) if qvols else 0.0
    qv3 = median(qvols[-VOL_FAST_DAYS:]) if qvols else 0.0
    vol_ratio = (qv3 / qv30) if qv30 else 0.0

    hist = bars[-DRAWDOWN_WINDOW_DAYS:]
    hi90 = max(float(b["high"]) for b in hist) or eps
    drawdown = (close - hi90) / hi90 * 100.0

    return {
        "pos_in_30d_range": pos,
        "rsi14": _wilder_rsi(closes, 14),
        "pct_vs_sma20": (close - sma20) / sma20 * 100.0,
        "pct_vs_sma50": (close - sma50) / sma50 * 100.0,
        "consol_30d": consol30,
        "vol_ratio": vol_ratio,
        "drawdown_from_90h": drawdown,
    }
```

- [ ] **Step 4: Correr para verque pasan**

Run: `python -m pytest tests/test_valley_filter.py::TestMeasureSetup -q`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add screener/valley_filter.py tests/test_valley_filter.py
git commit -m "feat(valles): measure_setup + _wilder_rsi — hechos del setup parte baja del rango"
```

---

## Task 2: `evaluate_symbol` reframe + tests backend

**Files:**
- Modify: `screener/valley_filter.py:159-180` (`evaluate_symbol`)
- Test: `tests/test_valley_filter.py` (`TestEvaluateYorden`), `tests/test_run_valley_screener.py`, `tests/test_valley_eval_api.py`, `tests/test_valles_freshness.py`

**Interfaces:**
- Consumes: `measure_setup`, `SETUP_POS_MAX`, `classify_liveness`, `liquidity_value`, `_distancia_ath_pct` (Task 1 + existentes).
- Produces: candidato con claves EXACTAS `{symbol, price, pos_in_30d_range, rsi14, pct_vs_sma20, pct_vs_sma50, consol_30d, vol_ratio, drawdown_from_90h, volumen_usd_dia, distancia_ath_pct, razones_vida}`.

- [ ] **Step 1: Reescribir los tests de `TestEvaluateYorden` (gate nuevo + claves exactas + aceptación)**

En `tests/test_valley_filter.py`, REEMPLAZAR los dos primeros métodos de `TestEvaluateYorden` (`test_evaluate_viva_en_rango_es_candidata` y `test_evaluate_viva_pero_no_en_rango_devuelve_none`) por (usa el helper `_serie_rango` de Task 1):
```python
    def test_evaluate_viva_en_parte_baja_es_candidata(self):
        bars = _serie_rango(150, lo=0.92, hi=1.20, last_close=0.93)
        cand = evaluate_symbol("XYZUSDT", bars)
        assert cand is not None
        assert cand["symbol"] == "XYZUSDT"
        assert set(cand.keys()) == {
            "symbol", "price", "pos_in_30d_range", "rsi14", "pct_vs_sma20",
            "pct_vs_sma50", "consol_30d", "vol_ratio", "drawdown_from_90h",
            "volumen_usd_dia", "distancia_ath_pct", "razones_vida"}
        assert cand["razones_vida"] == []
        assert "pct_rango" not in cand and "semanas_consolidando" not in cand

    def test_aceptacion_techo_no_pasa_con_amplitud_identica(self):
        # MISMA amplitud (0.92–1.20) que la candidata, pero el precio está en el TECHO.
        piso = _serie_rango(150, lo=0.92, hi=1.20, last_close=0.93)
        techo = _serie_rango(150, lo=0.92, hi=1.20, last_close=1.19)
        assert evaluate_symbol("PISOUSDT", piso) is not None      # piso PASA
        assert evaluate_symbol("TECHOUSDT", techo) is None        # techo NO pasa

    def test_payload_sin_lenguaje_de_veredicto(self):
        import json
        cand = evaluate_symbol("XYZUSDT", _serie_rango(150, lo=0.92, hi=1.20, last_close=0.93))
        blob = json.dumps(cand, ensure_ascii=False).lower()
        for prohibido in ("valle", "va a subir", "señal", "fuertes", "débil",
                          "cazaba", "tiene jugada", "setup de corrección"):
            assert prohibido not in blob, f"lenguaje prohibido: {prohibido}"
```
(El método `test_evaluate_muerta_devuelve_none`, `test_orden_neutral_*` y `test_liquidity_value_*` quedan IGUAL.)

- [ ] **Step 2: Correr para verque fallan**

Run: `python -m pytest tests/test_valley_filter.py::TestEvaluateYorden -q`
Expected: FAIL (evaluate_symbol aún gatea por `en_rango` y emite `pct_rango`).

- [ ] **Step 3: Reescribir `evaluate_symbol`**

REEMPLAZAR `evaluate_symbol` (`screener/valley_filter.py:159-180`) por:
```python
def evaluate_symbol(symbol: str, bars: list[dict]) -> dict | None:
    """Candidata si está VIVA y en la PARTE BAJA de su rango de 30d
    (pos_in_30d_range ≤ SETUP_POS_MAX) — réplica del filtro de un canal de 2019, NO un
    claim de selección (el estudio multi-régimen probó que no tiene edge). Devuelve
    hechos descriptivos; cero ranking, cero veredicto (spec SP2). None si no aplica."""
    vivo, razones = classify_liveness(bars)
    if not vivo:
        return None
    setup = measure_setup(bars)
    if setup["pos_in_30d_range"] > SETUP_POS_MAX:
        return None
    return {
        "symbol": symbol,
        "price": float(bars[-1]["close"]),
        **setup,
        "volumen_usd_dia": liquidity_value(bars),
        "distancia_ath_pct": _distancia_ath_pct(bars),
        "razones_vida": razones,
    }
```

- [ ] **Step 4: Correr `TestEvaluateYorden`**

Run: `python -m pytest tests/test_valley_filter.py -q`
Expected: PASS (TestMeasureSetup + TestEvaluateYorden + TestMeasureConsolidation + TestClassifyLiveness; `TestMeasureConsolidation` sigue verde — no se tocó).

- [ ] **Step 5: Arreglar los fixtures de candidata en los tests aguas abajo**

El gate nuevo rechaza las series planas. Actualizar:

(a) `tests/test_run_valley_screener.py` — la candidata viva ahora debe estar en la parte baja. En `test_snapshot_incluye_candidata_viva_y_omite_muerta`, reemplazar el `fake_klines` de `LIVEUSDT` por una serie en parte baja. Cambiar:
```python
        if symbol == "LIVEUSDT":
            return _kline_rows(150, 1.0, 2_000_000.0)   # viva + en rango (±3%)
```
por:
```python
        if symbol == "LIVEUSDT":
            # viva + en la PARTE BAJA de su rango: alto y estable, cae al piso al final.
            rows = []
            for i in range(150):
                c = 1.20 if i < 120 else 1.20 - 0.28 * ((i - 120) / 29.0)
                rows.append([i * 86_400_000, str(c), str(c * 1.005), str(c * 0.995),
                             str(c), str(2_000_000.0 / c), 0, str(2_000_000.0),
                             0, "0", "0", "0"])
            return rows
```
(`DEADUSDT` queda igual: volumen bajo piso → no vivo → no candidata, independiente del gate.)

(b) `tests/test_valley_eval_api.py` — el fixture de candidata del `/valley-eval` carga `pct_rango/semanas_consolidando/vol_percentil` y asserta `body["pct_rango"]`. Reemplazar esos campos del candidato por los nuevos (`pos_in_30d_range`, `rsi14`, …) y el assert por `assert body["pos_in_30d_range"] == <valor>`. (Leer el archivo y migrar el dict + el assert.)

(c) `tests/test_valles_freshness.py` — el candidato del fixture carga `semanas_consolidando: 8` y asserta `out["semanas_consolidando"] == 8`. Reemplazar el campo por `pos_in_30d_range: 0.1` y el assert por `out["pos_in_30d_range"] == 0.1`.

- [ ] **Step 6: Correr el gate backend completo**

Run: `python -m pytest tests/ -m "not network" -n auto -q`
Expected: PASS. (`test_valle_probe_*` y `TestMeasureConsolidation` intactos.)

- [ ] **Step 7: Commit**

```bash
git add screener/valley_filter.py tests/test_valley_filter.py tests/test_run_valley_screener.py tests/test_valley_eval_api.py tests/test_valles_freshness.py
git commit -m "feat(valles): evaluate_symbol gatea por parte baja del rango (no más valle) + tests"
```

---

## Task 3: Frontend — contrato de tipos + componentes de texto (PickScreen, Narrativa, recap, Copilot)

**Files:**
- Modify: `frontend/src/types.ts`, `frontend/src/components/valles/PickScreen.tsx`, `frontend/src/components/valles/idea/Narrativa.tsx`, `frontend/src/components/valles/recap.ts`, `frontend/src/components/valles/Copilot.tsx`
- Test: `frontend/src/components/valles/PickScreen.test.tsx`, `recap.test.ts`, `idea/Narrativa.test.tsx`

**Interfaces:**
- Consumes: el contrato del candidato de Task 2 (`pos_in_30d_range`, `rsi14`, …).
- Produces: `ValleyCandidate`/`ValleyEval` con los campos nuevos.

- [ ] **Step 1: Migrar los tipos** (`frontend/src/types.ts`)

En `ValleyCandidate` (≈L532-541): quitar `pct_rango`, `semanas_consolidando`, `vol_percentil`; añadir:
```typescript
  pos_in_30d_range:    number;
  rsi14:               number;
  pct_vs_sma20:        number;
  pct_vs_sma50:        number;
  consol_30d:          number;
  vol_ratio:           number;
  drawdown_from_90h:   number;
```
En `ValleyEval` (≈L598-611): hacer lo mismo pero con los campos OPCIONALES (`pos_in_30d_range?: number;` etc.), quitando los tres viejos.

- [ ] **Step 2: PickScreen** (`frontend/src/components/valles/PickScreen.tsx`)

- L19: `` `Hoy hay ${candidates.length} ${candidates.length === 1 ? 'moneda' : 'monedas'} en valle.` `` → `` `Hoy hay ${candidates.length} ${candidates.length === 1 ? 'moneda' : 'monedas'} en la parte baja de su rango.` ``
- L21: `'Hoy ninguna moneda en valle.'` → `'Hoy ninguna en la parte baja de su rango.'`
- L28-29 (lead): reemplazar el texto por:
```jsx
          En el cuartil inferior de su rango de 30d — la réplica del filtro que usaba el
          canal de 2019, mecánico, no un consejo. Elige una para mirarla de cerca.
```
- L41: `● en valle` → `● parte baja del rango`
- L42: reemplazar la línea por:
```jsx
              cuartil inferior (pos <b>{(c.pos_in_30d_range * 100).toFixed(0)}%</b>) · RSI <b>{c.rsi14.toFixed(0)}</b>
```
- L44-48 (bloque `ju-pickmark` "tiene jugada" + su comentario `TODO`): **eliminar** las 5 líneas completas (el `{/* TODO… */}` y el `<span className={juStyles['ju-pickmark']}>…</span>`). Quitar también el import `juStyles` (L8) si queda sin uso.

- [ ] **Step 3: Narrativa** (`frontend/src/components/valles/idea/Narrativa.tsx`)

- L41-48 (`vida.candidata === false`): reemplazar el `<p>` por:
```jsx
      <p className={styles['na-empty']}>
        No está en la parte baja de su rango ahora.
      </p>
```
- L50-67 (rama candidata): reemplazar desde `// Candidata = true` hasta el cierre del `return` por:
```jsx
  // Candidata = true → viva y en la parte baja de su rango
  const pos = vida.pos_in_30d_range != null ? `${Math.round(vida.pos_in_30d_range * 100)}%` : '—';
  const vsSma = vida.pct_vs_sma20 != null ? `${vida.pct_vs_sma20.toFixed(1)}%` : '—';
  const rsi = vida.rsi14 != null ? vida.rsi14.toFixed(0) : '—';

  return (
    <>
      <p className={styles['na-body']}>
        Está viva y en la <b>parte baja de su rango de 30d</b> (posición <b>{pos}</b>),
        por debajo de su SMA20 (<b>{vsSma}</b>), RSI <b>{rsi}</b>.
      </p>
      <p className={styles['na-costura']}>
        Esto es la réplica del filtro que usaba el canal de 2019. Medido, no le ganó al
        azar de alts ni en su mejor régimen (alt-bull 2019: 14d 9.92% vs 12.54%). Lo que
        movió el retorno fue el régimen, no esta selección. La decisión es tuya.
      </p>
    </>
  );
```
(Quita `semanas`, `volDia`, `franja` — ya no se usan. La costura es AC7, load-bearing.)
- L184 (segundo `franja` en BloqueJugada, zona de entrada): cambiar `franja{' '}` por `zona{' '}` (es la zona de entrada del plan, no la franja de consolidación).

- [ ] **Step 4: recap** (`frontend/src/components/valles/recap.ts`)

Reemplazar la línea 5 (anclar al campo `candidata`, NO a los strings viejos):
```typescript
  !v ? '—' : v.estado === 'no_disponible' ? '—' : v.candidata === false ? 'No en la parte baja' : 'En la parte baja de su rango';
```

- [ ] **Step 5: Copilot** (`frontend/src/components/valles/Copilot.tsx:17`)

`'¿Qué quiere decir "en valle"?'` → `'¿Qué quiere decir "parte baja del rango"?'`

- [ ] **Step 6: Actualizar los tests de texto**

- `PickScreen.test.tsx`: L12-13 fixtures → reemplazar `pct_rango/semanas_consolidando/vol_percentil` por `pos_in_30d_range: 0.12, rsi14: 38, pct_vs_sma20: -6, pct_vs_sma50: -9, consol_30d: 40, vol_ratio: 0.7, drawdown_from_90h: -35`. L31 assert `/ninguna moneda en valle/i` → `/ninguna en la parte baja/i`.
- `recap.test.ts`: L6 `'Viva y tranquila'` → `'En la parte baja de su rango'`; L7 `'Muy quieta'` → `'No en la parte baja'`.
- `Narrativa.test.tsx`: L17-18 fixture → campos nuevos (como arriba). L90-93 (`muestra semanas_consolidando`) → reemplazar por un test que asserta la posición y la costura: `expect(screen.getByText(/parte baja de su rango/i)).toBeTruthy()` y `expect(screen.getByText(/no le ganó al azar/i)).toBeTruthy()`; y un assert de doctrina `expect(screen.queryByText(/en valle|franja angosta/i)).toBeNull()`.

- [ ] **Step 7: Verificar (tsc + vitest de los tocados)**

Run: `cd frontend && npx tsc --noEmit && npx vitest run src/components/valles/PickScreen.test.tsx src/components/valles/recap.test.ts src/components/valles/idea/Narrativa.test.tsx`
Expected: tsc sin errores; los 3 test files verdes.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/types.ts frontend/src/components/valles/PickScreen.tsx frontend/src/components/valles/idea/Narrativa.tsx frontend/src/components/valles/recap.ts frontend/src/components/valles/Copilot.tsx frontend/src/components/valles/PickScreen.test.tsx frontend/src/components/valles/recap.test.ts frontend/src/components/valles/idea/Narrativa.test.tsx
git commit -m "feat(valles): frontend honesto — parte baja del rango + costura AC7, fuera 'valle'/'tiene jugada'"
```

---

## Task 4: Frontend — gráfico (chartLayers, IdeaChart) + barrido de fixtures + gate completo

**Files:**
- Modify: `frontend/src/components/valles/idea/chartLayers.ts`, `frontend/src/components/valles/idea/IdeaChart.tsx`
- Test: `frontend/src/components/valles/idea/chartLayers.test.ts` + barrido de TODOS los fixtures restantes

**Interfaces:**
- Consumes: `ValleyEval` con campos nuevos (Task 3).

- [ ] **Step 1: chartLayers** (`frontend/src/components/valles/idea/chartLayers.ts`)

- L18 (tipo `LayersModel.vida`): quitar la banda y `semanas` → `vida: { pos: number | null; vivoStamp: string };`
- L32-39 (cómputo de `band` desde `pct_rango`): eliminar el bloque del `band`.
- L63-71 (el objeto `vida` del return): reemplazar por:
```typescript
    vida: {
      pos: vida?.pos_in_30d_range ?? null,
      vivoStamp:
        vida?.vivo || vida?.candidata
          ? `viva · pos ${vida?.pos_in_30d_range != null ? Math.round(vida.pos_in_30d_range * 100) : '—'}% del rango 30d`
          : 'sin actividad',
    },
```

- [ ] **Step 2: IdeaChart** (`frontend/src/components/valles/idea/IdeaChart.tsx`)

- L33: `vida: 'Vida (el valle)',` → `vida: 'Vida (¿viva? · posición)',`
- L281: eliminar la línea `{m.vida.semanas > 0 && ` · ${m.vida.semanas} sem en rango`}` (la banda dibujada y "sem en rango" son SP3). Si hay render de la banda (`m.vida.band`) en el canvas, eliminarlo también (buscar `m.vida.band` en el archivo).

- [ ] **Step 3: Barrido de fixtures restantes**

Migrar los fixtures que aún cargan `pct_rango`/`semanas_consolidando`/`vol_percentil` a los campos nuevos (`pos_in_30d_range`, `rsi14`, etc.) en: `frontend/src/components/valles/ValleysFlow.test.tsx:41`, `useValleyBundle.test.tsx:8`, `doctrine.test.tsx:15/19/39`, `idea/IdeaView.test.tsx:56-57`, `idea/IdeaChart.test.tsx:50-51`, `idea/chartLayers.test.ts:5`, `frontend/src/api.test.ts` (≈L216), y `recap.test.ts` (ya en Task 3). En `chartLayers.test.ts` L21-25 (`vida expone la banda… semanas===12`): reemplazar por un test del sello de posición: `expect(m.vida.pos).toBeCloseTo(0.18)` y `expect(m.vida.vivoStamp).toMatch(/pos/i)`.

- [ ] **Step 4: Verificación de doctrina — cero referencias supervivientes**

Run:
```bash
git -C "$(pwd)" grep -nE "pct_rango|semanas_consolidando|vol_percentil|en valle|tiene jugada" -- frontend/src
```
Expected: **sin salida** (cero matches). Si algo aparece, migrarlo.

- [ ] **Step 5: Gate frontend completo**

Run: `cd frontend && npx vitest run && npx tsc --noEmit`
Expected: toda la suite vitest verde (incl. doctrine.test.tsx) + tsc limpio.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/valles/idea/chartLayers.ts frontend/src/components/valles/idea/IdeaChart.tsx frontend/src/components/valles/idea/chartLayers.test.ts frontend/src/components/valles/ValleysFlow.test.tsx frontend/src/components/valles/useValleyBundle.test.tsx frontend/src/components/valles/doctrine.test.tsx frontend/src/components/valles/idea/IdeaView.test.tsx frontend/src/components/valles/idea/IdeaChart.test.tsx frontend/src/api.test.ts
git commit -m "feat(valles): gráfico sin banda 'valle' — sello de posición + barrido de fixtures"
```

---

## Task 5: Deploy doc + gate final

**Files:**
- Modify: `.mex/patterns/correr-screener-valles.md` (si existe)

**Interfaces:** ninguna (cierre).

- [ ] **Step 1: Documentar el paso de regeneración del snapshot (#8)**

En `.mex/patterns/correr-screener-valles.md` (verificar con `ls` que existe; si no, omitir y anotar en el PR), añadir:
```markdown
> **SP2 / deploy:** el contrato del candidato cambió (de `pct_rango`/`semanas_consolidando` a
> `pos_in_30d_range`/`rsi14`/…). El snapshot persistido `data/valley_candidates.json` queda
> incompatible hasta que el `screener_loop` lo regenere. El deploy DEBE forzar una regeneración
> (correr `python -m tools.run_valley_screener` o esperar un ciclo del loop) al activar el front
> nuevo; si no, `/valley-candidates` sirve campos viejos y el front lee `undefined` → "NaN%".
```

- [ ] **Step 2: Gate completo del repo**

Run:
```bash
python -m pytest tests/ -m "not network" -n auto -q
cd frontend && npx vitest run && npx tsc --noEmit
```
Expected: TODO verde (salvo flakes ortogonales conocidos — verificar en aislamiento, ver `ci-discipline`).

- [ ] **Step 3: `mex log` + commit**

```bash
mex log "feat: reframe del detector per-coin (SP2) — de 'valle' a 'parte baja del rango', honestidad AC7"
git add .mex docs
git commit -m "docs(valles): paso de deploy (regen snapshot) + mex log del reframe del detector"
```

---

## Notas de ejecución

- **Doctrina (candado automático):** `test_payload_sin_lenguaje_de_veredicto` (Task 2) + el `git grep` (Task 4 Step 4) son los dos candados. AC7 (la costura con los números medidos) se verifica en `Narrativa.test.tsx` (Task 3 Step 6).
- **Intacto:** `measure_consolidation`, `tests/test_valle_probe_*`, `TestMeasureConsolidation` — NO tocar.
- **Deuda conocida declarada:** `_realized_vol`/`vol_percentil`/`VOL_PERCENTILE_WINDOW_DAYS` quedan sin consumidor de producto (solo el probe legacy); retirarlos es otra decisión (fuera de SP2).
- **Fuera de alcance:** banda de rango-30d dibujada + marcador (SP3); calibrar `SETUP_POS_MAX` (POST-SHIP); retirar el probe.
