# Vista Valles A — Filtro de Vida: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Producir una lista plana y neutral de monedas spot USDT vivas y actualmente en consolidación geométrica, para descubrimiento operado por humano — sin ranking de atractivo, sin señal de entrada, sin claim de edge.

**Architecture:** Cálculo puro sobre barras diarias (`screener/valley_filter.py`, sin red, testeable) → enumeración del universo vivo + fetch de Binance + escritura de una foto regenerable (`tools/run_valley_screener.py`) → API que lee la foto (`GET /valley-candidates`, no per-tenant) → vista frontend neutral (`ValleysView`, sin badges de compra).

**Tech Stack:** Python 3.12 / FastAPI / SQLite (aquí no hay DB — el artefacto es JSON), React 18 + TypeScript (Vite), pytest + vitest.

**Spec:** `docs/superpowers/specs/es/2026-06-11-screener-valles-filtro-vida-design.md` — leer COMPLETO antes de empezar, especialmente §1 (frontera ontológica) y §0 (qué NO es).

**Reglas del repo que aplican aquí (no negociables):**
- I/O de red NUNCA dentro de un cálculo puro ni de una transacción (incidente de contención 2026-06-10). El fetch vive solo en el comando orquestador.
- **Frontera ontológica (§1 del spec):** el screener afirma solo "está viva" y "está en rango", ambos hechos verificables hoy. NUNCA rankea por "calidad de valle" (eso es la celda B del programa). El orden es por liquidez (hecho) o alfabético.
- La UI NO usa badges de compra ni colores de señal — la presentación no debe contrabandear juicio.
- NO es per-tenant: el universo de mercado es global.
- Tests rápidos por tarea: `python -m pytest tests/<archivo> -v`. Gate final: `python -m pytest tests/ -m "not network" -q`. Frontend: `cd frontend && npx vitest run <archivo>` + `npm run build`.
- Comentarios y docstrings en español (convención del repo). UI en español.

**Representación de barras (contrato compartido por todas las tareas):** las funciones puras reciben `bars: list[dict]`, cada dict con claves:
`{"open_time": int_ms, "open": float, "high": float, "low": float, "close": float, "volume": float, "quote_volume": float}`.
`quote_volume` = volumen en USDT (campo índice 7 de la kline cruda de Binance) — es el "volumen USD/día" del filtro. Las barras son **diarias** (`1d`), ordenadas ascendente por `open_time`.

---

### Task 1: Constantes + clasificación de vida (`classify_liveness`)

**Files:**
- Create: `screener/__init__.py` (vacío)
- Create: `screener/valley_filter.py`
- Test: `tests/test_valley_filter.py`

- [ ] **Step 1: Write the failing test**

Crear `tests/test_valley_filter.py`:

```python
"""Tests del cálculo puro del screener de valles (Vista Valles A).

Spec: docs/superpowers/specs/es/2026-06-11-screener-valles-filtro-vida-design.md §7.
Cero red, cero DB — todo sobre barras diarias sintéticas.
"""
from screener.valley_filter import classify_liveness


def _bar(t, close, quote_vol, *, high=None, low=None):
    """Barra diaria sintética. high/low por defecto = ±0.5% de close (vela viva)."""
    high = high if high is not None else close * 1.005
    low = low if low is not None else close * 0.995
    return {"open_time": t, "open": close, "high": high, "low": low,
            "close": close, "volume": quote_vol / close, "quote_volume": quote_vol}


def _serie(n, close=1.0, quote_vol=1_000_000.0, **kw):
    """n barras diarias consecutivas (1 día = 86_400_000 ms)."""
    return [_bar(i * 86_400_000, close, quote_vol, **kw) for i in range(n)]


class TestClassifyLiveness:
    def test_moneda_viva_pasa(self):
        vivo, razones = classify_liveness(_serie(200, quote_vol=2_000_000.0))
        assert vivo is True
        assert razones == []

    def test_volumen_bajo_piso_excluye(self):
        vivo, razones = classify_liveness(_serie(200, quote_vol=100_000.0))
        assert vivo is False
        assert "volumen_bajo_piso" in razones

    def test_historia_insuficiente_excluye(self):
        vivo, razones = classify_liveness(_serie(50, quote_vol=2_000_000.0))
        assert vivo is False
        assert "historia_insuficiente" in razones

    def test_volumen_agonizante_excluye(self):
        # Primeros 90 días con volumen alto, últimos 90 con un tercio → agoniza.
        viejo = [_bar(i * 86_400_000, 1.0, 3_000_000.0) for i in range(90)]
        nuevo = [_bar((90 + i) * 86_400_000, 1.0, 800_000.0) for i in range(90)]
        vivo, razones = classify_liveness(viejo + nuevo)
        assert vivo is False
        assert "volumen_agonizante" in razones

    def test_velas_planas_excluye(self):
        # 200 barras con rango ~0 (high==low==close) → libro abandonado.
        planas = [_bar(i * 86_400_000, 1.0, 2_000_000.0, high=1.0, low=1.0) for i in range(200)]
        vivo, razones = classify_liveness(planas)
        assert vivo is False
        assert "velas_planas" in razones

    def test_descansa_con_vida_vs_agoniza(self):
        # Caso límite nombrado por el operador: volumen BAJO pero ESTABLE = vive.
        estable = _serie(200, quote_vol=700_000.0)  # bajo pero constante y > piso
        vivo, razones = classify_liveness(estable)
        assert vivo is True, f"volumen bajo-estable debe vivir, razones={razones}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_valley_filter.py::TestClassifyLiveness -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'screener'`

- [ ] **Step 3: Write the implementation**

Crear `screener/__init__.py` vacío. Crear `screener/valley_filter.py`:

```python
"""Cálculo puro del screener de valles (Vista Valles A) — sin red, sin DB.

Afirma SOLO hechos verificables en t: "está viva" (classify_liveness) y
"está en rango" (measure_consolidation). NUNCA rankea por atractivo — eso es
la celda B del programa. Spec §1, §5.1.

Contrato de barras: list[dict] diarias ascendentes con claves
{open_time, open, high, low, close, volume, quote_volume}. quote_volume = USDT.
"""
from __future__ import annotations

from statistics import median

# ── Umbrales de vida (arranque; calibrables, spec §2) ───────────────────────
MIN_VOLUME_USD_DAY = 500_000.0     # piso absoluto de volumen diario USDT
MIN_HISTORY_DAYS = 120             # historia mínima para juzgar consolidación
AGONY_LOOKBACK_DAYS = 90           # ventana vieja vs nueva para tendencia de volumen
AGONY_RATIO = 0.5                  # nuevo < ratio × viejo ⟹ agonizante
FLAT_RANGE_PCT = 0.005             # (high-low)/close < esto ⟹ vela "plana"
FLAT_MAX_FRACTION = 0.5            # > esta fracción de velas planas ⟹ libro muerto
FLAT_WINDOW_DAYS = 90              # ventana reciente para medir velas planas


def _quote_vols(bars: list[dict]) -> list[float]:
    return [float(b["quote_volume"]) for b in bars]


def classify_liveness(bars: list[dict]) -> tuple[bool, list[str]]:
    """¿La moneda está viva y operable? Devuelve (vivo, razones_de_muerte).

    vivo=True sólo si NINGUNA señal de muerte mecánica dispara. Las 4 señales
    (spec §2): volumen bajo piso, volumen agonizante, velas planas, historia
    insuficiente. Cada razón es un hecho, no un juicio."""
    razones: list[str] = []

    if len(bars) < MIN_HISTORY_DAYS:
        razones.append("historia_insuficiente")
        return (False, razones)  # sin historia no se puede juzgar el resto

    vols = _quote_vols(bars)

    # 1. Volumen bajo el piso (mediana reciente de 30 días).
    vol_reciente = median(vols[-30:])
    if vol_reciente < MIN_VOLUME_USD_DAY:
        razones.append("volumen_bajo_piso")

    # 2. Volumen agonizante: mediana de los últimos AGONY_LOOKBACK_DAYS vs la
    #    de la ventana inmediatamente anterior del mismo tamaño.
    if len(bars) >= 2 * AGONY_LOOKBACK_DAYS:
        nuevo = median(vols[-AGONY_LOOKBACK_DAYS:])
        viejo = median(vols[-2 * AGONY_LOOKBACK_DAYS:-AGONY_LOOKBACK_DAYS])
        if viejo > 0 and nuevo < AGONY_RATIO * viejo:
            razones.append("volumen_agonizante")

    # 3. Velas planas: fracción de velas recientes con rango ≈0 o volumen 0.
    ventana = bars[-FLAT_WINDOW_DAYS:]
    planas = 0
    for b in ventana:
        close = float(b["close"]) or 1.0
        rango_pct = (float(b["high"]) - float(b["low"])) / close
        if rango_pct < FLAT_RANGE_PCT or float(b["quote_volume"]) <= 0:
            planas += 1
    if ventana and planas / len(ventana) > FLAT_MAX_FRACTION:
        razones.append("velas_planas")

    return (len(razones) == 0, razones)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_valley_filter.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add screener/__init__.py screener/valley_filter.py tests/test_valley_filter.py
git commit -m "feat(screener): classify_liveness — 4 señales de muerte mecánica, cálculo puro (Valles A §2)"
```

---

### Task 2: Consolidación geométrica (`measure_consolidation`)

**Files:**
- Modify: `screener/valley_filter.py`
- Test: `tests/test_valley_filter.py` (añadir clase)

- [ ] **Step 1: Write the failing tests**

Añadir a `tests/test_valley_filter.py`:

```python
from screener.valley_filter import measure_consolidation


class TestMeasureConsolidation:
    def test_valle_en_rango_estrecho(self):
        # 120 días oscilando dentro de ±3% de 1.0 → en rango.
        bars = []
        for i in range(120):
            c = 1.0 + (0.03 if i % 2 else -0.03)  # ±3%
            bars.append(_bar(i * 86_400_000, c, 1_000_000.0,
                             high=c * 1.005, low=c * 0.995))
        out = measure_consolidation(bars)
        assert out["en_rango"] is True
        assert out["pct_rango"] < 0.25
        assert out["semanas"] >= 1

    def test_tendencia_no_esta_en_rango(self):
        # Precio subiendo de 1.0 a 2.0 → NO en rango (rango ancho).
        bars = [_bar(i * 86_400_000, 1.0 + i / 120.0, 1_000_000.0) for i in range(120)]
        out = measure_consolidation(bars)
        assert out["en_rango"] is False
        assert out["pct_rango"] > 0.25

    def test_reporta_metricas_aunque_no_este_en_rango(self):
        bars = [_bar(i * 86_400_000, 1.0 + i / 60.0, 1_000_000.0) for i in range(120)]
        out = measure_consolidation(bars)
        # Siempre devuelve las 4 claves (hechos), aunque en_rango sea False.
        assert set(out.keys()) == {"en_rango", "pct_rango", "semanas", "vol_percentil"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_valley_filter.py::TestMeasureConsolidation -v`
Expected: FAIL — `ImportError: cannot import name 'measure_consolidation'`

- [ ] **Step 3: Write the implementation**

Añadir a `screener/valley_filter.py` (tras las constantes de vida, añade las de consolidación; tras `classify_liveness`, añade la función):

```python
# ── Umbrales de consolidación geométrica (arranque; calibrables, spec §4) ───
CONSOLIDATION_WINDOW_DAYS = 84     # 12 semanas
RANGE_BAND_MAX = 0.25              # (max-min)/mediana ≤ esto ⟹ en rango
VOL_PERCENTILE_WINDOW_DAYS = 365   # historia para el percentil de volatilidad
```

```python
def _realized_vol(bars: list[dict]) -> float:
    """Desviación de retornos diarios close-to-close (proxy de volatilidad)."""
    closes = [float(b["close"]) for b in bars]
    if len(closes) < 2:
        return 0.0
    rets = [(closes[i] - closes[i - 1]) / closes[i - 1]
            for i in range(1, len(closes)) if closes[i - 1] != 0]
    if len(rets) < 2:
        return 0.0
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return var ** 0.5


def measure_consolidation(bars: list[dict]) -> dict:
    """¿El precio está geométricamente en rango AHORA? Hecho descriptivo del
    presente — NO afirma que sea buena entrada (spec §4).

    Devuelve SIEMPRE las 4 claves:
      en_rango (bool), pct_rango (float), semanas (int), vol_percentil (float).
    pct_rango = (max-min)/mediana sobre la ventana de consolidación.
    vol_percentil = posición de la volatilidad de 30d en su historia de 1 año
    (0.0 = la más baja que ha tenido; 1.0 = la más alta)."""
    ventana = bars[-CONSOLIDATION_WINDOW_DAYS:]
    closes = [float(b["close"]) for b in ventana]
    med = median(closes) if closes else 1.0
    hi = max(float(b["high"]) for b in ventana) if ventana else 0.0
    lo = min(float(b["low"]) for b in ventana) if ventana else 0.0
    pct_rango = (hi - lo) / med if med else float("inf")
    en_rango = pct_rango <= RANGE_BAND_MAX

    # Semanas dentro de banda: cuenta semanas recientes (bloques de 7 días)
    # cuyo rango propio ≤ RANGE_BAND_MAX, desde la más reciente hacia atrás.
    semanas = 0
    i = len(bars)
    while i - 7 >= 0:
        bloque = bars[i - 7:i]
        b_hi = max(float(b["high"]) for b in bloque)
        b_lo = min(float(b["low"]) for b in bloque)
        b_med = median([float(b["close"]) for b in bloque]) or 1.0
        if (b_hi - b_lo) / b_med <= RANGE_BAND_MAX:
            semanas += 1
            i -= 7
        else:
            break

    # Percentil de volatilidad: vol de los últimos 30d vs la distribución de
    # vol de ventanas de 30d a lo largo del último año.
    vol_actual = _realized_vol(bars[-30:])
    hist = bars[-VOL_PERCENTILE_WINDOW_DAYS:]
    muestras = []
    for j in range(30, len(hist) + 1, 7):  # paso semanal para no sobre-muestrear
        muestras.append(_realized_vol(hist[j - 30:j]))
    if muestras:
        menores = sum(1 for v in muestras if v <= vol_actual)
        vol_percentil = menores / len(muestras)
    else:
        vol_percentil = 0.0

    return {"en_rango": en_rango, "pct_rango": pct_rango,
            "semanas": semanas, "vol_percentil": vol_percentil}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_valley_filter.py -v`
Expected: PASS (vida + consolidación)

- [ ] **Step 5: Commit**

```bash
git add screener/valley_filter.py tests/test_valley_filter.py
git commit -m "feat(screener): measure_consolidation — geometría del valle como hecho descriptivo (Valles A §4)"
```

---

### Task 3: Evaluación por símbolo + orden neutral (`evaluate_symbol`, `order_neutral`)

**Files:**
- Modify: `screener/valley_filter.py`
- Test: `tests/test_valley_filter.py` (añadir clase)

- [ ] **Step 1: Write the failing tests**

Añadir a `tests/test_valley_filter.py`:

```python
from screener.valley_filter import evaluate_symbol, order_neutral, liquidity_value


class TestEvaluateYorden:
    def test_evaluate_viva_en_rango_es_candidata(self):
        bars = []
        for i in range(150):
            c = 1.0 + (0.03 if i % 2 else -0.03)
            bars.append(_bar(i * 86_400_000, c, 2_000_000.0,
                             high=c * 1.005, low=c * 0.995))
        cand = evaluate_symbol("XYZUSDT", bars)
        assert cand is not None
        assert cand["symbol"] == "XYZUSDT"
        assert set(cand.keys()) >= {
            "symbol", "price", "pct_rango", "semanas_consolidando",
            "volumen_usd_dia", "distancia_ath_pct", "razones_vida"}
        assert cand["razones_vida"] == []

    def test_evaluate_muerta_devuelve_none(self):
        cand = evaluate_symbol("DEADUSDT", _serie(200, quote_vol=50_000.0))
        assert cand is None  # volumen bajo piso ⟹ no candidata

    def test_evaluate_viva_pero_no_en_rango_devuelve_none(self):
        bars = [_bar(i * 86_400_000, 1.0 + i / 100.0, 2_000_000.0) for i in range(150)]
        cand = evaluate_symbol("TRENDUSDT", bars)
        assert cand is None  # viva pero en tendencia, no es valle

    def test_orden_neutral_por_liquidez_desc(self):
        a = {"symbol": "AUSDT", "volumen_usd_dia": 1_000_000.0}
        b = {"symbol": "BUSDT", "volumen_usd_dia": 5_000_000.0}
        c = {"symbol": "CUSDT", "volumen_usd_dia": 2_000_000.0}
        ordenado = order_neutral([a, b, c])
        assert [x["symbol"] for x in ordenado] == ["BUSDT", "CUSDT", "AUSDT"]

    def test_liquidity_value_es_mediana_volumen(self):
        bars = _serie(60, quote_vol=1_500_000.0)
        assert liquidity_value(bars) == 1_500_000.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_valley_filter.py::TestEvaluateYorden -v`
Expected: FAIL — `ImportError: cannot import name 'evaluate_symbol'`

- [ ] **Step 3: Write the implementation**

Añadir a `screener/valley_filter.py`:

```python
def liquidity_value(bars: list[dict]) -> float:
    """Liquidez como HECHO: mediana del volumen USDT de los últimos 30 días.
    Es el criterio de ORDEN NEUTRAL — un hecho, no una medida de 'calidad'."""
    vols = _quote_vols(bars[-30:])
    return median(vols) if vols else 0.0


def _distancia_ath_pct(bars: list[dict]) -> float:
    """% por debajo del máximo histórico de la serie (dato informativo, NO
    criterio de filtro ni de orden — spec §4)."""
    ath = max(float(b["high"]) for b in bars) if bars else 0.0
    last = float(bars[-1]["close"]) if bars else 0.0
    if ath <= 0:
        return 0.0
    return (ath - last) / ath


def evaluate_symbol(symbol: str, bars: list[dict]) -> dict | None:
    """Evalúa un símbolo. Devuelve la candidata (dict de HECHOS) si está VIVA
    y EN RANGO; None en cualquier otro caso. Cero ranking, cero claim.

    El dict resultante NO incluye ningún score de 'atractivo' — sólo hechos
    descriptivos que el humano interpreta (spec §0, §1)."""
    vivo, razones = classify_liveness(bars)
    if not vivo:
        return None
    cons = measure_consolidation(bars)
    if not cons["en_rango"]:
        return None
    return {
        "symbol": symbol,
        "price": float(bars[-1]["close"]),
        "pct_rango": cons["pct_rango"],
        "semanas_consolidando": cons["semanas"],
        "vol_percentil": cons["vol_percentil"],
        "volumen_usd_dia": liquidity_value(bars),
        "distancia_ath_pct": _distancia_ath_pct(bars),
        "razones_vida": razones,  # [] cuando viva; presente por simetría
    }


def order_neutral(candidatas: list[dict]) -> list[dict]:
    """Orden NEUTRAL por liquidez descendente (hecho). NO ordena por 'calidad
    de valle' — ese ranking es la celda B del programa, prohibido aquí."""
    return sorted(candidatas, key=lambda c: c.get("volumen_usd_dia", 0.0), reverse=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_valley_filter.py -v`
Expected: PASS (vida + consolidación + evaluación/orden)

- [ ] **Step 5: Commit**

```bash
git add screener/valley_filter.py tests/test_valley_filter.py
git commit -m "feat(screener): evaluate_symbol + order_neutral — candidata de hechos, orden por liquidez (Valles A §5.1)"
```

---

### Task 4: Enumeración del universo vivo (`list_live_usdt_spot`)

**Files:**
- Create: `screener/universe.py`
- Test: `tests/test_screener_universe.py`

- [ ] **Step 1: Write the failing test**

Crear `tests/test_screener_universe.py`:

```python
"""Tests de la enumeración del universo vivo (Vista Valles A §3)."""
from unittest.mock import patch

from screener.universe import list_live_usdt_spot, _is_eligible


class TestEligibilidad:
    def test_par_usdt_normal_elegible(self):
        assert _is_eligible({"symbol": "BTCUSDT", "quoteAsset": "USDT",
                             "status": "TRADING", "baseAsset": "BTC"}) is True

    def test_no_usdt_excluido(self):
        assert _is_eligible({"symbol": "BTCETH", "quoteAsset": "ETH",
                             "status": "TRADING", "baseAsset": "BTC"}) is False

    def test_no_trading_excluido(self):
        assert _is_eligible({"symbol": "XYZUSDT", "quoteAsset": "USDT",
                             "status": "BREAK", "baseAsset": "XYZ"}) is False

    def test_stablecoin_excluido(self):
        assert _is_eligible({"symbol": "USDCUSDT", "quoteAsset": "USDT",
                             "status": "TRADING", "baseAsset": "USDC"}) is False

    def test_apalancado_excluido(self):
        assert _is_eligible({"symbol": "BTCUPUSDT", "quoteAsset": "USDT",
                             "status": "TRADING", "baseAsset": "BTCUP"}) is False


class TestListLiveUsdtSpot:
    def test_filtra_y_devuelve_simbolos(self):
        fake = {"symbols": [
            {"symbol": "BTCUSDT", "quoteAsset": "USDT", "status": "TRADING", "baseAsset": "BTC"},
            {"symbol": "USDCUSDT", "quoteAsset": "USDT", "status": "TRADING", "baseAsset": "USDC"},
            {"symbol": "ETHBTC", "quoteAsset": "BTC", "status": "TRADING", "baseAsset": "ETH"},
            {"symbol": "ADAUSDT", "quoteAsset": "USDT", "status": "TRADING", "baseAsset": "ADA"},
        ]}

        class _Resp:
            status_code = 200
            def json(self):
                return fake

        with patch("screener.universe._http_get", return_value=_Resp()):
            out = list_live_usdt_spot()
        assert out == ["ADAUSDT", "BTCUSDT"]  # ordenado, sin stable ni cross
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_screener_universe.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'screener.universe'`

- [ ] **Step 3: Write the implementation**

Crear `screener/universe.py`:

```python
"""Enumeración del universo vivo de Binance spot (Vista Valles A §3).

Sólo pares USDT TRADING, excluyendo stablecoins, fiat y apalancados. Las
delistadas NO aparecen (status != TRADING) — correcto: sólo lo comprable
importa para listar candidatas operables (spec §1, nota de survivorship)."""
from __future__ import annotations

import requests

_EXCHANGE_INFO = "https://api.binance.com/api/v3/exchangeInfo"

# Bases que NO son inversión direccional (stablecoins/fiat envueltos).
_STABLE_FIAT_BASES = {
    "USDC", "BUSD", "TUSD", "USDP", "DAI", "FDUSD", "USDD", "EUR", "GBP",
    "AEUR", "EURI", "USDS", "PAX", "SUSD", "GUSD",
}
# Sufijos de tokens apalancados de Binance.
_LEVERAGED_SUFFIXES = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")


def _http_get(url, params=None, timeout=15):
    """Wrapper fino para que los tests mockeen sólo esta llamada."""
    return requests.get(url, params=params, timeout=timeout)


def _is_eligible(s: dict) -> bool:
    if s.get("quoteAsset") != "USDT":
        return False
    if s.get("status") != "TRADING":
        return False
    if s.get("baseAsset") in _STABLE_FIAT_BASES:
        return False
    sym = s.get("symbol", "")
    if any(sym.endswith(suf) for suf in _LEVERAGED_SUFFIXES):
        return False
    return True


def list_live_usdt_spot() -> list[str]:
    """Lista ordenada de símbolos USDT spot vivos y elegibles. Lanza
    requests.RequestException / RuntimeError si exchangeInfo falla (el caller
    decide; sin universo no hay screener)."""
    r = _http_get(_EXCHANGE_INFO)
    if r.status_code != 200:
        raise RuntimeError(f"exchangeInfo HTTP {r.status_code}")
    symbols = r.json().get("symbols", [])
    return sorted(s["symbol"] for s in symbols if _is_eligible(s))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_screener_universe.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add screener/universe.py tests/test_screener_universe.py
git commit -m "feat(screener): list_live_usdt_spot — universo vivo, sin stable/fiat/apalancados (Valles A §3)"
```

---

### Task 5: Comando orquestador (`tools/run_valley_screener.py`)

**Files:**
- Create: `tools/run_valley_screener.py`
- Test: `tests/test_run_valley_screener.py`

- [ ] **Step 1: Write the failing tests**

Crear `tests/test_run_valley_screener.py`:

```python
"""Tests del orquestador del screener (Vista Valles A §5.2, §6).

La red se mockea por completo: universo + fetch de klines."""
from unittest.mock import patch

from tools.run_valley_screener import build_snapshot


def _kline_rows(n, close, quote_vol):
    """Filas crudas de Binance: [open_time, o, h, l, c, vol, close_time,
    quote_vol, ...]. La barra diaria usa índices 0,1,2,3,4,5,7."""
    rows = []
    for i in range(n):
        rows.append([
            i * 86_400_000, str(close), str(close * 1.03), str(close * 0.97),
            str(close), str(quote_vol / close), 0, str(quote_vol),
            0, "0", "0", "0",
        ])
    return rows


def test_snapshot_incluye_candidata_viva_y_omite_muerta():
    universo = ["LIVEUSDT", "DEADUSDT"]

    def fake_klines(symbol, **kw):
        if symbol == "LIVEUSDT":
            return _kline_rows(150, 1.0, 2_000_000.0)   # viva + en rango (±3%)
        return _kline_rows(150, 1.0, 50_000.0)          # volumen bajo piso

    with patch("tools.run_valley_screener.list_live_usdt_spot", return_value=universo), \
         patch("tools.run_valley_screener._fetch_daily_klines", side_effect=fake_klines):
        snap = build_snapshot()

    syms = [c["symbol"] for c in snap["candidates"]]
    assert "LIVEUSDT" in syms
    assert "DEADUSDT" not in syms
    assert snap["coverage"]["universe"] == 2
    assert snap["coverage"]["evaluated"] == 2
    assert snap["coverage"]["complete"] is True
    assert "generated_at" in snap


def test_fallo_de_un_simbolo_no_tumba_el_run_y_marca_cobertura():
    universo = ["GOODUSDT", "BROKENUSDT"]

    def fake_klines(symbol, **kw):
        if symbol == "BROKENUSDT":
            raise RuntimeError("kline fetch boom")
        return _kline_rows(150, 1.0, 2_000_000.0)

    with patch("tools.run_valley_screener.list_live_usdt_spot", return_value=universo), \
         patch("tools.run_valley_screener._fetch_daily_klines", side_effect=fake_klines):
        snap = build_snapshot()

    assert snap["coverage"]["universe"] == 2
    assert snap["coverage"]["evaluated"] == 1        # BROKENUSDT omitida
    assert snap["coverage"]["complete"] is False     # cobertura incompleta, honesta
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_run_valley_screener.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.run_valley_screener'`

- [ ] **Step 3: Write the implementation**

Crear `tools/run_valley_screener.py`:

```python
"""Orquestador del screener de valles (Vista Valles A §5.2).

ÚNICO lugar con I/O de red: enumera el universo vivo, baja klines diarias
frescas, aplica el cálculo puro (screener.valley_filter) y escribe una foto
regenerable a data/valley_candidates.json. Un símbolo que falla se OMITE con
su razón (fallo parcial no corrompe el resultado, spec §6); la cobertura se
reporta con honestidad (complete=False si faltó alguno).

Uso: python -m tools.run_valley_screener
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone

import requests

from screener.universe import list_live_usdt_spot
from screener.valley_filter import evaluate_symbol, order_neutral

log = logging.getLogger("tools.run_valley_screener")

_KLINES_URL = "https://api.binance.com/api/v3/klines"
_HISTORY_DAYS = 400   # cubre la ventana de percentil (365) + margen
_OUTPUT = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                       "data", "valley_candidates.json")


def _http_get(url, params=None, timeout=15):
    return requests.get(url, params=params, timeout=timeout)


def _fetch_daily_klines(symbol: str, *, limit: int = _HISTORY_DAYS) -> list[list]:
    """Filas crudas de klines 1d de Binance (público). Lanza en error de red /
    HTTP no-200 para que el caller cuente el símbolo como no evaluado."""
    r = _http_get(_KLINES_URL, params={"symbol": symbol, "interval": "1d", "limit": limit})
    if r.status_code in (429, 418):
        raise RuntimeError(f"rate banned HTTP {r.status_code}")
    if r.status_code != 200:
        raise RuntimeError(f"klines HTTP {r.status_code}")
    return r.json()


def _rows_to_bars(rows: list[list]) -> list[dict]:
    """Filas crudas de Binance → barras del contrato puro (índices 0,1,2,3,4,5,7)."""
    return [
        {"open_time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4]), "volume": float(r[5]),
         "quote_volume": float(r[7])}
        for r in rows
    ]


def build_snapshot(*, pause_s: float = 0.0) -> dict:
    """Construye la foto del screener. Devuelve el dict serializable (no
    escribe a disco — eso lo hace main, para que los tests no toquen el FS)."""
    universo = list_live_usdt_spot()
    candidatas: list[dict] = []
    evaluadas = 0
    for sym in universo:
        try:
            rows = _fetch_daily_klines(sym)
        except (requests.RequestException, RuntimeError) as e:
            log.warning("SCREENER_SYMBOL_SKIPPED symbol=%s causa=%s", sym, e)
            continue
        evaluadas += 1
        cand = evaluate_symbol(sym, _rows_to_bars(rows))
        if cand is not None:
            candidatas.append(cand)
        if pause_s:
            time.sleep(pause_s)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "coverage": {
            "universe": len(universo),
            "evaluated": evaluadas,
            "complete": evaluadas == len(universo),
        },
        "candidates": order_neutral(candidatas),
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    snap = build_snapshot(pause_s=0.05)  # pausa suave para no golpear el rate-limit
    os.makedirs(os.path.dirname(_OUTPUT), exist_ok=True)
    with open(_OUTPUT, "w", encoding="utf-8") as f:
        json.dump(snap, f, indent=2, ensure_ascii=False)
    cov = snap["coverage"]
    print(f"valley_candidates.json: {len(snap['candidates'])} candidatas; "
          f"cobertura {cov['evaluated']}/{cov['universe']} "
          f"({'completa' if cov['complete'] else 'INCOMPLETA'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_run_valley_screener.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/run_valley_screener.py tests/test_run_valley_screener.py
git commit -m "feat(screener): orquestador run_valley_screener — fetch + foto regenerable, cobertura honesta (Valles A §5.2/§6)"
```

---

### Task 6: API — `GET /valley-candidates`

**Files:**
- Create: `api/valleys.py`
- Modify: `btc_api.py` (registrar el router junto a los demás `include_router`)
- Test: `tests/test_valleys_api.py`

- [ ] **Step 1: Write the failing tests**

Crear `tests/test_valleys_api.py`:

```python
"""Tests del endpoint GET /valley-candidates (Vista Valles A §5.3)."""
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.valleys import router


def _app():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_devuelve_la_foto_cuando_existe(tmp_path, monkeypatch):
    foto = {"generated_at": "2026-06-11T00:00:00+00:00",
            "coverage": {"universe": 2, "evaluated": 2, "complete": True},
            "candidates": [{"symbol": "XYZUSDT", "price": 1.0, "volumen_usd_dia": 2_000_000.0}]}
    p = tmp_path / "valley_candidates.json"
    p.write_text(json.dumps(foto), encoding="utf-8")
    monkeypatch.setattr("api.valleys._OUTPUT", str(p))

    r = _app().get("/valley-candidates")
    assert r.status_code == 200
    body = r.json()
    assert body["coverage"]["complete"] is True
    assert body["candidates"][0]["symbol"] == "XYZUSDT"


def test_foto_ausente_devuelve_vacio_no_500(tmp_path, monkeypatch):
    monkeypatch.setattr("api.valleys._OUTPUT", str(tmp_path / "no_existe.json"))
    r = _app().get("/valley-candidates")
    assert r.status_code == 200
    body = r.json()
    assert body["candidates"] == []
    assert body["coverage"]["complete"] is False
    assert body["generated_at"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_valleys_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'api.valleys'`

- [ ] **Step 3: Write the implementation**

Crear `api/valleys.py`:

```python
"""API de la Vista Valles (sub-proyecto A). Read-only, NO per-tenant — el
universo de mercado es global (spec §0, §5.3).

Lee la foto regenerable que escribe tools.run_valley_screener. NO computa nada
en el request (el fetch de 200+ símbolos es pesado y vive en el comando)."""
from __future__ import annotations

import json
import logging
import os

from fastapi import APIRouter

log = logging.getLogger("api.valleys")

router = APIRouter(tags=["valleys"])

_OUTPUT = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                       "data", "valley_candidates.json")

_EMPTY = {"generated_at": None,
          "coverage": {"universe": 0, "evaluated": 0, "complete": False},
          "candidates": []}


@router.get("/valley-candidates", summary="Candidatas del screener de valles (vivas + en rango)")
def get_valley_candidates() -> dict:
    """Devuelve la foto del screener. Si aún no se ha corrido el comando, la
    respuesta es vacía con complete=False (no 500) — la UI muestra 'sin foto'."""
    if not os.path.exists(_OUTPUT):
        return dict(_EMPTY)
    try:
        with open(_OUTPUT, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.error("VALLEY_SNAPSHOT_UNREADABLE causa=%s", e)
        return dict(_EMPTY)
```

En `btc_api.py`, junto al bloque de `app.include_router(...)` (tras `notifications_router`, ~línea 302): añadir el import del router con los otros imports de routers, y registrar:

```python
from api.valleys import router as valleys_router   # junto a los otros router imports
...
app.include_router(valleys_router)                 # junto a los otros include_router
```

(Buscar cómo se importan los demás routers — p. ej. `from api.signals import router as signals_router` — y seguir EXACTO ese estilo.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_valleys_api.py -v`
Expected: PASS

Verificar que la app sigue arrancando (import sano): `python -c "import btc_api"` → sin error.

- [ ] **Step 5: Commit**

```bash
git add api/valleys.py btc_api.py tests/test_valleys_api.py
git commit -m "feat(api): GET /valley-candidates — lee la foto del screener, read-only no-tenant (Valles A §5.3)"
```

---

### Task 7: Frontend — vista "Valles"

**Files:**
- Modify: `frontend/src/types-ui.ts` (`MainTab` union)
- Modify: `frontend/src/types.ts` (interfaces `ValleyCandidate`, `ValleySnapshot`)
- Modify: `frontend/src/api.ts` (fetch `getValleyCandidates`)
- Create: `frontend/src/components/ValleysView.tsx`, `ValleysView.module.css`, `ValleysView.test.tsx`
- Modify: `frontend/src/components/LeftRail.tsx` (item de análisis)
- Modify: `frontend/src/App.tsx` (import + render condicional)

- [ ] **Step 1: Write the failing test**

Crear `frontend/src/components/ValleysView.test.tsx` (replicar el setup de `HistorialView.test.tsx` si existe, o `ObservedOrders.test.tsx`):

```tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ValleysView } from './ValleysView';
import type { ValleySnapshot } from '../types';

const snap: ValleySnapshot = {
  generated_at: '2026-06-11T12:00:00+00:00',
  coverage: { universe: 210, evaluated: 198, complete: false },
  candidates: [
    { symbol: 'ADAUSDT', price: 0.42, pct_rango: 0.18, semanas_consolidando: 9,
      vol_percentil: 0.22, volumen_usd_dia: 3_000_000, distancia_ath_pct: 0.86, razones_vida: [] },
    { symbol: 'XLMUSDT', price: 0.11, pct_rango: 0.21, semanas_consolidando: 6,
      vol_percentil: 0.31, volumen_usd_dia: 1_500_000, distancia_ath_pct: 0.91, razones_vida: [] },
  ],
};

describe('ValleysView', () => {
  it('lista las candidatas con sus hechos', () => {
    render(<ValleysView snapshot={snap} loading={false} />);
    expect(screen.getByText('ADAUSDT')).toBeInTheDocument();
    expect(screen.getByText('XLMUSDT')).toBeInTheDocument();
  });

  it('muestra cobertura incompleta de forma honesta', () => {
    render(<ValleysView snapshot={snap} loading={false} />);
    expect(screen.getByText(/198\s*\/\s*210/u)).toBeInTheDocument();
  });

  it('no renderiza ningún badge de compra ni señal', () => {
    const { container } = render(<ValleysView snapshot={snap} loading={false} />);
    const txt = container.textContent ?? '';
    expect(/comprar|compra|señal|signal|buy/i.test(txt)).toBe(false);
    expect(container.querySelector('[class*="buy"], [class*="signal"], [class*="senal"]')).toBeNull();
  });

  it('estado sin foto: muestra aviso, no rompe', () => {
    const vacio: ValleySnapshot = {
      generated_at: null, coverage: { universe: 0, evaluated: 0, complete: false }, candidates: [],
    };
    render(<ValleysView snapshot={vacio} loading={false} />);
    expect(screen.getByText(/sin foto|aún no/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/ValleysView.test.tsx`
Expected: FAIL — módulo no existe.

- [ ] **Step 3: Write the implementation**

En `frontend/src/types.ts` (al final):

```ts
// Vista Valles A — candidata del screener (HECHOS, sin score de atractivo).
export interface ValleyCandidate {
  symbol:               string;
  price:                number;
  pct_rango:            number;
  semanas_consolidando: number;
  vol_percentil:        number;
  volumen_usd_dia:      number;
  distancia_ath_pct:    number;
  razones_vida:         string[];
}

export interface ValleySnapshot {
  generated_at: string | null;
  coverage:     { universe: number; evaluated: number; complete: boolean };
  candidates:   ValleyCandidate[];
}
```

En `frontend/src/types-ui.ts`, extender el union `MainTab` con `'valles'`:

```ts
export type MainTab = 'mercado' | 'posiciones' | 'kill-switch' | 'historial' | 'autotune' | 'valles';
```

En `frontend/src/api.ts` (junto a los otros fetch, seguir el estilo de `getPositions`):

```ts
export function getValleyCandidates() {
  return request<import('./types').ValleySnapshot>('/valley-candidates');
}
```

Crear `frontend/src/components/ValleysView.tsx`:

```tsx
import React from 'react';
import type { ValleySnapshot } from '../types';
import { formatPrice } from '../utils';
import styles from './ValleysView.module.css';

// Vista Valles A — lista NEUTRAL de monedas vivas en consolidación.
// Sin badges de compra, sin colores de señal: presenta hechos, no juicios.
export const ValleysView: React.FC<{ snapshot: ValleySnapshot; loading: boolean }> = ({
  snapshot, loading,
}) => {
  if (loading) return <div className={styles.empty}>Cargando…</div>;
  const { generated_at, coverage, candidates } = snapshot;
  if (!generated_at || candidates.length === 0) {
    return (
      <div className={styles.empty}>
        Aún no hay foto del screener. Corré <code>python -m tools.run_valley_screener</code>.
      </div>
    );
  }
  return (
    <div className={styles.wrap}>
      <div className={`${styles.meta} prose`}>
        Foto del {new Date(generated_at).toLocaleString('es-ES')} · cobertura{' '}
        {coverage.evaluated} / {coverage.universe}
        {!coverage.complete && ' (incompleta)'}
      </div>
      <table className={styles.table}>
        <thead>
          <tr>
            <th>Símbolo</th><th>Precio</th><th>Rango</th><th>Semanas</th>
            <th>Vol. percentil</th><th>Volumen/día</th><th>Desde máx.</th>
          </tr>
        </thead>
        <tbody>
          {candidates.map((c) => (
            <tr key={c.symbol}>
              <td className={styles.sym}>{c.symbol.replace('USDT', '')}</td>
              <td className="num">{formatPrice(c.price)}</td>
              <td className="num">{(c.pct_rango * 100).toFixed(1)}%</td>
              <td className="num">{c.semanas_consolidando}</td>
              <td className="num">{Math.round(c.vol_percentil * 100)}%</td>
              <td className="num">${Math.round(c.volumen_usd_dia).toLocaleString('en-US')}</td>
              <td className="num">−{(c.distancia_ath_pct * 100).toFixed(0)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};
```

Crear `frontend/src/components/ValleysView.module.css` (tonos neutros — reutilizar tokens del proyecto, sin colores de señal):

```css
.wrap { padding: 8px 0; }
.meta { font-size: 12px; color: var(--nbc-fg-muted); margin-bottom: 12px; }
.empty { padding: 32px; text-align: center; color: var(--nbc-fg-muted); }
.table { width: 100%; border-collapse: collapse; font-size: 13px; }
.table th {
  text-align: right; padding: 6px 10px; color: var(--nbc-fg-muted);
  border-bottom: 1px solid var(--nbc-border-dim); font-weight: 500;
}
.table th:first-child, .table td:first-child { text-align: left; }
.table td { padding: 6px 10px; border-bottom: 1px solid var(--nbc-border-dim); }
.sym { font-weight: 600; }
```

En `frontend/src/components/LeftRail.tsx`, añadir el item en la sección de análisis (junto a `history`):

```tsx
    { id: 'valles', label: 'Valles', icon: 'history', tab: 'valles' },
```

(Usar un `icon` ya existente — `history` sirve; no inventar uno nuevo.)

En `frontend/src/App.tsx`:
1. Importar la vista (junto a los otros imports de vistas): `import { ValleysView } from './components/ValleysView';`
2. Estado + fetch: añadir `const [valleys, setValleys] = useState<ValleySnapshot>({ generated_at: null, coverage: { universe: 0, evaluated: 0, complete: false }, candidates: [] });` y un `useEffect`/handler que llame `getValleyCandidates()` cuando `mainTab === 'valles'` (seguir el patrón de cómo se cargan datos de otras pestañas; importar `getValleyCandidates` de `./api` y el tipo `ValleySnapshot` de `./types`).
3. Render condicional (junto a los otros `mainTab === ...`):

```tsx
          {mainTab === 'valles' && (
            <ValleysView snapshot={valleys} loading={false} />
          )}
```

- [ ] **Step 4: Run test + build to verify**

Run: `cd frontend && npx vitest run src/components/ValleysView.test.tsx && npm run build`
Expected: tests PASS (4); build sin errores NUEVOS (la baseline de `useAgentStream.test.tsx` no cuenta).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/types-ui.ts frontend/src/api.ts \
        frontend/src/components/ValleysView.tsx frontend/src/components/ValleysView.module.css \
        frontend/src/components/ValleysView.test.tsx \
        frontend/src/components/LeftRail.tsx frontend/src/App.tsx
git commit -m "feat(frontend): vista Valles — tabla neutral de candidatas, sin badges de compra (Valles A §5.4)"
```

---

### Task 8: Gate final + GROW

**Files:**
- Modify: `.mex/ROUTER.md` (Current Project State)
- Create: `.mex/patterns/correr-screener-valles.md` + fila en `.mex/patterns/INDEX.md`

- [ ] **Step 1: Run the full fast gate**

Run: `python -m pytest tests/ -m "not network" -q`
Expected: todo verde. Si algo falla y NO toca screener/valleys, verificar que también falla en el merge-base (flake ortogonal preexistente) antes de tocarlo; si toca lo nuestro, arreglar.

- [ ] **Step 2: Frontend build final**

Run: `cd frontend && npm run build`
Expected: limpio salvo la baseline conocida de `useAgentStream.test.tsx`.

- [ ] **Step 3: GROW — pattern nuevo**

Crear `.mex/patterns/correr-screener-valles.md` con el formato de los patterns existentes (Purpose / When / Steps / Gotchas / Verify Checklist), en español. Contenido mínimo:
- Purpose: refrescar la foto de candidatas del screener de valles.
- Steps: `python -m tools.run_valley_screener` → escribe `data/valley_candidates.json` → la vista "Valles" del dashboard lo lee vía `GET /valley-candidates`.
- Gotchas: (1) es observabilidad, NO estrategia — la lista es plana y neutral, el ranking por calidad es la celda B del programa (no implementar aquí); (2) corre sobre universo VIVO de Binance + data fresca, NO sobre `program_ohlcv.db` (panel congelado, ese es para falsificación); (3) cobertura incompleta se reporta honestamente (`complete=false`), nunca se finge foto completa; (4) la UI no lleva badges de compra por diseño.
- Verify Checklist: el JSON tiene `generated_at` + `coverage`; la vista muestra "sin foto" cuando no se ha corrido; tests puros verdes.

Añadir fila a `.mex/patterns/INDEX.md`: `| Refrescar la lista de candidatas del screener de valles | [correr-screener-valles.md](correr-screener-valles.md) |`

- [ ] **Step 4: GROW — actualizar ROUTER + log**

En `.mex/ROUTER.md` §Working añadir: `- Vista Valles A: screener de vida + consolidación (observabilidad, lista neutral; ranking=celda B diferida, dossier=C diferido).`

Run: `mex log "Vista Valles A: filtro de vida + consolidación geométrica como observabilidad; ranking-por-calidad reconocido como claim → celda B del programa (Voronov 2026-06-11)"` (si `mex` no está en PATH, anexar la línea JSONL equivalente a `.mex/events/decisions.jsonl`).

- [ ] **Step 5: Commit**

```bash
git add .mex/
git commit -m "docs(mex): pattern correr-screener-valles + estado Valles A en ROUTER"
```

---

## Validación end-to-end (manual, post-merge)

```bash
python -m tools.run_valley_screener     # baja universo + klines, escribe la foto
# Revisar data/valley_candidates.json: cobertura, nº de candidatas, que el orden
# sea por volumen descendente (NO por 'calidad'). Abrir la vista "Valles" en el
# dashboard y confirmar: tabla neutral, banner de frescura+cobertura, sin badges
# de compra. Pasar la lista a Samuel/Simón para el juicio humano (paso 2 = C, futuro).
```
