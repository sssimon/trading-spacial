# D.1 — Detector neutral de soporte/resistencia · Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir un detector neutral de niveles soporte/resistencia que, dado un símbolo, detecta zonas S/R desde las velas diarias y ubica el precio en vivo respecto a ellas — afirmando solo hechos, sin veredicto.

**Architecture:** Tres piezas que espejan A (screener) y C (dossier): un módulo puro `screener/sr_levels.py` (sin red, sin DB — velas → zonas), un endpoint on-demand `GET /levels/{symbol}` en `api/levels.py` (trae velas + precio vivo de Binance fuera de toda tx, corre el detector, NUNCA cachea ni toca DB), y una vista mínima `LevelsPanel.tsx` colgada de la Vista Valles. Hereda la disciplina de `valley_filter.py`: cada campo es un hecho derivado de las velas, no un juicio.

**Tech Stack:** Python 3.12 (stdlib `statistics.median`, `math`), FastAPI + `requests`, pytest. Frontend React + TypeScript, Vitest + Testing Library.

**Spec:** `docs/superpowers/specs/es/2026-06-12-detector-sr-neutral-design.md`

**Branch:** `feat/sr-level-detector-d1` (ya creada).

---

## File Structure

| Archivo | Responsabilidad |
|---|---|
| `screener/sr_levels.py` (crear) | Detector puro: constantes, `_pivots`, `_round_confluence`, `_cluster`, `locate_price`, `detect_levels`. Sin red, sin DB. |
| `tests/test_sr_levels.py` (crear) | Tests del detector puro. |
| `api/levels.py` (crear) | Endpoint `GET /levels/{symbol}`: fetch de velas + precio vivo (red fuera de tx), corre el detector, contrato `no_disponible`. Sin caché, sin DB. |
| `tests/test_levels_api.py` (crear) | Tests del endpoint (fetch mockeado). |
| `btc_api.py` (modificar) | Registrar el router de `levels`. |
| `frontend/src/types.ts` (modificar) | Tipos `SrZona`, `SrRef`, `SrUbicacion`, `SrLevels`. |
| `frontend/src/api.ts` (modificar) | Cliente `getLevels(symbol)`. |
| `frontend/src/components/LevelsPanel.tsx` (crear) | Panel mínimo: precio vivo + bandas + techo/piso. Sin badge. |
| `frontend/src/components/LevelsPanel.module.css` (crear) | Estilos del panel. |
| `frontend/src/components/LevelsPanel.test.tsx` (crear) | Tests del panel. |
| `frontend/src/components/ValleysView.tsx` (modificar) | Botón "Niveles" + panel. |
| `frontend/src/components/ValleysView.test.tsx` (modificar) | Test del botón "Niveles". |

---

### Task 1: Detector puro — constantes + detección de pivotes

**Files:**
- Create: `screener/sr_levels.py`
- Test: `tests/test_sr_levels.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sr_levels.py
"""Tests del detector neutral de S/R (D.1). Puro: sin red, sin DB. Spec §3."""
from screener.sr_levels import _pivots


def _bar(h, l):
    return {"open_time": 0, "open": l, "high": h, "low": l,
            "close": (h + l) / 2, "volume": 1, "quote_volume": 1}


def test_pivot_alto_es_maximo_local_estricto():
    highs = [10, 11, 12, 15, 12, 11, 10]   # idx 3 (=15) es el pico
    bars = [_bar(h, 9) for h in highs]
    altos, _ = _pivots(bars, k=2)
    assert altos == [15.0]


def test_pivot_bajo_es_minimo_local_estricto():
    lows = [10, 9, 8, 5, 8, 9, 10]         # idx 3 (=5) es el valle
    bars = [_bar(20, l) for l in lows]
    _, bajos = _pivots(bars, k=2)
    assert bajos == [5.0]


def test_pivot_excluye_ultimas_k_velas():
    # un pico en la última vela no se confirma con k=2 (faltan velas a la derecha)
    highs = [10, 11, 12, 13, 99]
    bars = [_bar(h, 9) for h in highs]
    altos, _ = _pivots(bars, k=2)
    assert 99.0 not in altos


def test_meseta_plana_no_es_pivote():
    highs = [10, 12, 12, 12, 10]           # meseta: ningún high estrictamente mayor
    bars = [_bar(h, 9) for h in highs]
    altos, _ = _pivots(bars, k=1)
    assert altos == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sr_levels.py -v`
Expected: FAIL with `ImportError: cannot import name '_pivots'`

- [ ] **Step 3: Write minimal implementation**

```python
# screener/sr_levels.py
"""Detector neutral de soporte/resistencia (D.1) — cálculo puro, sin red, sin DB.

Afirma SOLO hechos observables en las velas: dónde la trayectoria cambió
(pivotes), agrupados en zonas. NUNCA emite veredicto ni ranking — hereda la
disciplina de screener/valley_filter.py. Spec §3.

Contrato de barras: list[dict] diarias ascendentes con claves
{open_time, open, high, low, close, volume, quote_volume}.
"""
from __future__ import annotations

import math
from statistics import median

# ── Constantes de arranque (calibrables, spec §3.6) ─────────────────────────
PIVOT_REACH = 3          # velas a cada lado para confirmar un giro
CLUSTER_TOL_PCT = 0.0075  # 0.75% → pivotes más cercanos = misma zona
LOOKBACK_DAYS = 365      # un año de velas diarias (lo pide el endpoint)
MIN_TOUCHES = 2          # zona defendida ≥2 veces (=1 mostraría cada giro suelto)


def _pivots(bars: list[dict], k: int) -> tuple[list[float], list[float]]:
    """Precios de pivotes confirmados → (pivote-altos, pivote-bajos).

    Pivote-alto: `high` estrictamente mayor que el de los k vecinos a cada lado.
    Pivote-bajo: `low` estrictamente menor. Las primeras y últimas k velas se
    excluyen (sin k vecinos confirmatorios → sin look-ahead, sin pivote prematuro).
    La comparación estricta hace que una meseta plana no produzca pivote."""
    altos: list[float] = []
    bajos: list[float] = []
    n = len(bars)
    for i in range(k, n - k):
        hi = float(bars[i]["high"])
        lo = float(bars[i]["low"])
        vecinos = bars[i - k:i] + bars[i + 1:i + 1 + k]
        if all(hi > float(b["high"]) for b in vecinos):
            altos.append(hi)
        if all(lo < float(b["low"]) for b in vecinos):
            bajos.append(lo)
    return altos, bajos
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sr_levels.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add screener/sr_levels.py tests/test_sr_levels.py
git commit -m "feat(sr): pivotes diarios confirmados (sin look-ahead, meseta no cuenta)"
```

---

### Task 2: Detector puro — confluencia con número redondo

**Files:**
- Modify: `screener/sr_levels.py`
- Test: `tests/test_sr_levels.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sr_levels.py — añadir
from screener.sr_levels import _round_confluence


def test_confluencia_detecta_redondo_en_banda():
    # ~69.000: step = 1.000 → 69.000 cae dentro de [69.000, 69.200]
    assert _round_confluence(69000.0, 69200.0) == [69000.0]


def test_confluencia_vacia_si_no_hay_redondo():
    # banda estrecha sin múltiplo de 1.000
    assert _round_confluence(69100.0, 69200.0) == []


def test_confluencia_precio_pequeno():
    # ~6.0e-6: step = 1e-7; la banda contiene 6.0e-6
    res = _round_confluence(5.95e-6, 6.05e-6)
    assert any(abs(x - 6.0e-6) < 1e-12 for x in res)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sr_levels.py -k confluencia -v`
Expected: FAIL with `ImportError: cannot import name '_round_confluence'`

- [ ] **Step 3: Write minimal implementation**

```python
# screener/sr_levels.py — añadir tras _pivots
def _round_confluence(precio_bajo: float, precio_alto: float) -> list[float]:
    """Números redondos notables dentro de [bajo, alto]. ANOTACIÓN observable —
    NO reubica el nivel (spec §3.3). Paso a un orden por debajo de la magnitud:
    step = 10^(floor(log10(alto)) - 1). Como las bandas son estrechas, da 0–1."""
    if precio_alto <= 0:
        return []
    step = 10 ** (math.floor(math.log10(precio_alto)) - 1)
    if step <= 0:
        return []
    primero = math.ceil(precio_bajo / step)
    ultimo = math.floor(precio_alto / step)
    return [round(step * m, 10) for m in range(primero, ultimo + 1)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sr_levels.py -k confluencia -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add screener/sr_levels.py tests/test_sr_levels.py
git commit -m "feat(sr): confluencia con número redondo como anotación (no reubica)"
```

---

### Task 3: Detector puro — agrupación de pivotes en zonas

**Files:**
- Modify: `screener/sr_levels.py`
- Test: `tests/test_sr_levels.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sr_levels.py — añadir
from screener.sr_levels import _cluster


def test_cluster_agrupa_dentro_de_tolerancia():
    # 100 y 100.5 (0.5% < 0.75%) → una zona; 110 abre otra
    zonas = _cluster([100.0, 100.5, 110.0], "soporte", tol_pct=0.0075, min_touches=1)
    assert len(zonas) == 2
    assert zonas[0]["toques"] == 2
    assert zonas[0]["centro"] == 100.25
    assert zonas[0]["tipo"] == "soporte"
    assert zonas[0]["precio_bajo"] == 100.0 and zonas[0]["precio_alto"] == 100.5


def test_cluster_min_touches_filtra_giros_sueltos():
    zonas = _cluster([100.0, 200.0], "resistencia", tol_pct=0.0075, min_touches=2)
    assert zonas == []   # cada giro suelto, ninguno con 2 toques


def test_centro_es_mediana_no_redondo():
    # mediana de los pivotes = 65.100, NO el redondo 65.000
    zonas = _cluster([64800.0, 65100.0, 65400.0], "soporte", tol_pct=0.02, min_touches=1)
    assert zonas[0]["centro"] == 65100.0
    assert 65000.0 in zonas[0]["confluencia_redondo"]   # el redondo se ANOTA aparte
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sr_levels.py -k cluster -v`
Expected: FAIL with `ImportError: cannot import name '_cluster'`

- [ ] **Step 3: Write minimal implementation**

```python
# screener/sr_levels.py — añadir tras _round_confluence
def _cluster(precios: list[float], tipo: str, tol_pct: float,
             min_touches: int) -> list[dict]:
    """Agrupa pivotes cercanos en zonas. Un precio entra al clúster si está
    dentro de tol_pct del CENTRO corriente (mediana); si no, abre uno nuevo.
    Cada clúster con ≥min_touches produce una zona de HECHOS (spec §3.2)."""
    if not precios:
        return []
    ordenados = sorted(precios)
    grupos: list[list[float]] = [[ordenados[0]]]
    for p in ordenados[1:]:
        centro = median(grupos[-1])
        if centro > 0 and abs(p - centro) / centro <= tol_pct:
            grupos[-1].append(p)
        else:
            grupos.append([p])

    zonas: list[dict] = []
    for g in grupos:
        if len(g) < min_touches:
            continue
        bajo, alto = min(g), max(g)
        zonas.append({
            "tipo": tipo,
            "precio_bajo": bajo,
            "precio_alto": alto,
            "centro": float(median(g)),
            "toques": len(g),
            "confluencia_redondo": _round_confluence(bajo, alto),
        })
    return zonas
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sr_levels.py -k cluster -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add screener/sr_levels.py tests/test_sr_levels.py
git commit -m "feat(sr): agrupa pivotes en zonas (centro=mediana, toques, min_touches)"
```

---

### Task 4: Detector puro — ubicación del precio vivo

**Files:**
- Modify: `screener/sr_levels.py`
- Test: `tests/test_sr_levels.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sr_levels.py — añadir
from screener.sr_levels import locate_price


def _zona(tipo, bajo, alto, centro, toques):
    return {"tipo": tipo, "precio_bajo": bajo, "precio_alto": alto,
            "centro": centro, "toques": toques, "confluencia_redondo": []}


def test_locate_entre_zonas():
    zonas = [_zona("soporte", 64800, 65400, 65100, 3),
             _zona("resistencia", 69000, 69200, 69100, 4)]
    u = locate_price(67230, zonas)
    assert u["dentro_de"] is None
    assert u["techo"]["centro"] == 69100
    assert u["piso"]["centro"] == 65100
    assert u["piso"]["dist_pct"] == round((65100 - 67230) / 67230 * 100, 2)


def test_locate_dentro_de_zona():
    zonas = [_zona("soporte", 64800, 65400, 65100, 3)]
    u = locate_price(65000, zonas)
    assert u["dentro_de"]["centro"] == 65100
    assert u["techo"] is None and u["piso"] is None


def test_locate_sin_zonas():
    assert locate_price(100.0, []) == {"dentro_de": None, "techo": None, "piso": None}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sr_levels.py -k locate -v`
Expected: FAIL with `ImportError: cannot import name 'locate_price'`

- [ ] **Step 3: Write minimal implementation**

```python
# screener/sr_levels.py — añadir tras _cluster
def locate_price(price: float, zonas: list[dict]) -> dict:
    """Ubica el precio vivo respecto a las zonas — HECHOS geométricos, no consejo.

    dentro_de: zona que contiene al precio (la de más toques si hay varias), o None.
    techo: zona inmediata por encima (menor centro con precio_bajo > price).
    piso: zona inmediata por debajo (mayor centro con precio_alto < price)."""
    dentro = [z for z in zonas if z["precio_bajo"] <= price <= z["precio_alto"]]
    dentro_de = max(dentro, key=lambda z: z["toques"]) if dentro else None
    arriba = [z for z in zonas if z["precio_bajo"] > price]
    abajo = [z for z in zonas if z["precio_alto"] < price]
    techo = min(arriba, key=lambda z: z["centro"]) if arriba else None
    piso = max(abajo, key=lambda z: z["centro"]) if abajo else None

    def _ref(z: dict | None) -> dict | None:
        if z is None:
            return None
        return {"centro": z["centro"],
                "dist_pct": round((z["centro"] - price) / price * 100, 2)}

    return {"dentro_de": dentro_de, "techo": _ref(techo), "piso": _ref(piso)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sr_levels.py -k locate -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add screener/sr_levels.py tests/test_sr_levels.py
git commit -m "feat(sr): locate_price — techo/piso/dentro_de como hechos geométricos"
```

---

### Task 5: Detector puro — orquestación `detect_levels`

**Files:**
- Modify: `screener/sr_levels.py`
- Test: `tests/test_sr_levels.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sr_levels.py — añadir
from screener.sr_levels import detect_levels


def test_detect_levels_clasifica_y_ordena():
    # serie plana a 100 con spikes aislados: 2 picos a 110, 2 valles a 90.
    bars = [_bar(100, 100) for _ in range(40)]
    for i in (5, 20):
        bars[i] = _bar(110, 100)   # pico aislado (high=110)
    for i in (12, 30):
        bars[i] = _bar(100, 90)    # valle aislado (low=90)
    zonas = detect_levels(bars)

    assert {z["tipo"] for z in zonas} == {"resistencia", "soporte"}
    res = [z for z in zonas if z["tipo"] == "resistencia"][0]
    sop = [z for z in zonas if z["tipo"] == "soporte"][0]
    assert res["toques"] == 2 and res["centro"] == 110.0
    assert sop["toques"] == 2 and sop["centro"] == 90.0
    # ordenado por centro ascendente: soporte (90) antes que resistencia (110)
    assert [z["centro"] for z in zonas] == [90.0, 110.0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sr_levels.py -k detect_levels -v`
Expected: FAIL with `ImportError: cannot import name 'detect_levels'`

- [ ] **Step 3: Write minimal implementation**

```python
# screener/sr_levels.py — añadir al final
def detect_levels(bars: list[dict]) -> list[dict]:
    """Velas diarias → zonas S/R ordenadas por centro ascendente. Única función
    que el endpoint llama además de locate_price. Sin red, sin DB (spec §3.5)."""
    altos, bajos = _pivots(bars, PIVOT_REACH)
    zonas = (_cluster(altos, "resistencia", CLUSTER_TOL_PCT, MIN_TOUCHES)
             + _cluster(bajos, "soporte", CLUSTER_TOL_PCT, MIN_TOUCHES))
    return sorted(zonas, key=lambda z: z["centro"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sr_levels.py -v`
Expected: PASS (todos: pivotes, confluencia, cluster, locate, detect_levels)

- [ ] **Step 5: Commit**

```bash
git add screener/sr_levels.py tests/test_sr_levels.py
git commit -m "feat(sr): detect_levels orquesta pivotes→zonas ordenadas por centro"
```

---

### Task 6: Endpoint `GET /levels/{symbol}`

**Files:**
- Create: `api/levels.py`
- Test: `tests/test_levels_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_levels_api.py
"""Tests del endpoint GET /levels/{symbol}. Fetch mockeado: sin red real. Spec §4."""
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.levels import router


def _app():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _bars():
    bars = [{"open_time": 0, "open": 100, "high": 100, "low": 100,
             "close": 100, "volume": 1, "quote_volume": 1} for _ in range(40)]
    for i in (5, 20):
        bars[i] = {**bars[i], "high": 110}
    for i in (12, 30):
        bars[i] = {**bars[i], "low": 90}
    return bars


def test_payload_ok():
    with patch("api.levels._fetch_daily_bars", return_value=_bars()), \
         patch("api.levels._fetch_live_price", return_value=100.0):
        r = _app().get("/levels/BTCUSDT")
    assert r.status_code == 200
    body = r.json()
    assert body["estado"] == "ok"
    assert body["price_live"] == 100.0
    assert {z["tipo"] for z in body["zonas"]} == {"resistencia", "soporte"}
    assert "ubicacion" in body


def test_binance_caido_es_no_disponible_sin_500():
    with patch("api.levels._fetch_daily_bars", side_effect=RuntimeError("klines HTTP 503")):
        r = _app().get("/levels/BTCUSDT")
    assert r.status_code == 200
    assert r.json()["estado"] == "no_disponible"
    assert r.json()["zonas"] == []
    assert r.json()["price_live"] is None


def test_symbol_invalido_es_no_disponible():
    with patch("api.levels._fetch_daily_bars", side_effect=RuntimeError("klines HTTP 400")):
        r = _app().get("/levels/NOPEUSDT")
    assert r.json()["estado"] == "no_disponible"


def test_endpoint_no_toca_db():
    # D.1 no persiste: el módulo NO debe referenciar transaction/snapshot_connection.
    import inspect
    import api.levels
    src = inspect.getsource(api.levels)
    assert "transaction" not in src
    assert "snapshot_connection" not in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_levels_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'api.levels'`

- [ ] **Step 3: Write minimal implementation**

```python
# api/levels.py
"""API del detector neutral de S/R (D.1). GET /levels/{symbol}.

Read-only, NO per-tenant (los niveles de un símbolo son globales). Trae velas
diarias + precio vivo de Binance (red FUERA de toda tx), corre el detector puro
y ubica el precio. NUNCA cachea, NUNCA toca DB — el precio es vivo, se computa
fresco cada request. Fallo externo → 'no_disponible' sin 500. Spec §4."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests
from fastapi import APIRouter

from screener.sr_levels import LOOKBACK_DAYS, detect_levels, locate_price

log = logging.getLogger("api.levels")

router = APIRouter(tags=["levels"])

_KLINES_URL = "https://api.binance.com/api/v3/klines"
_PRICE_URL = "https://api.binance.com/api/v3/ticker/price"


def _http_get(url, params=None, timeout=15):
    return requests.get(url, params=params, timeout=timeout)


def _fetch_daily_bars(symbol: str) -> list[dict]:
    """Velas 1d del contrato puro (índices 0,1,2,3,4,5,7). Lanza en no-200."""
    r = _http_get(_KLINES_URL,
                  params={"symbol": symbol, "interval": "1d", "limit": LOOKBACK_DAYS})
    if r.status_code != 200:
        raise RuntimeError(f"klines HTTP {r.status_code}")
    return [
        {"open_time": int(x[0]), "open": float(x[1]), "high": float(x[2]),
         "low": float(x[3]), "close": float(x[4]), "volume": float(x[5]),
         "quote_volume": float(x[7])}
        for x in r.json()
    ]


def _fetch_live_price(symbol: str) -> float:
    """Precio spot vivo (/ticker/price). Lanza en no-200 o payload inesperado."""
    r = _http_get(_PRICE_URL, params={"symbol": symbol})
    if r.status_code != 200:
        raise RuntimeError(f"price HTTP {r.status_code}")
    return float(r.json()["price"])


def _no_disponible(symbol: str) -> dict:
    return {"symbol": symbol, "estado": "no_disponible", "generated_at": None,
            "price_live": None, "zonas": [],
            "ubicacion": {"dentro_de": None, "techo": None, "piso": None}}


@router.get("/levels/{symbol}", summary="Niveles S/R neutrales + ubicación del precio vivo")
def get_levels(symbol: str) -> dict:
    """Detecta zonas S/R desde velas diarias y ubica el precio vivo. NUNCA 500ea
    por fallo externo (Binance caído / símbolo inválido) → 'no_disponible'."""
    symbol = symbol.upper()[:20]
    try:
        bars = _fetch_daily_bars(symbol)
        price = _fetch_live_price(symbol)
    except (requests.RequestException, RuntimeError, KeyError, ValueError) as e:
        log.warning("LEVELS_NO_DISPONIBLE symbol=%s causa=%s", symbol, e)
        return _no_disponible(symbol)

    zonas = detect_levels(bars)
    return {"symbol": symbol, "estado": "ok",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "price_live": price, "zonas": zonas,
            "ubicacion": locate_price(price, zonas)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_levels_api.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add api/levels.py tests/test_levels_api.py
git commit -m "feat(api): GET /levels/{symbol} — detector S/R con precio vivo, no_disponible sin 500"
```

---

### Task 7: Registrar el router en la app

**Files:**
- Modify: `btc_api.py` (import junto a `dossier_router` ~línea 91; `include_router` junto al de dossier ~línea 310)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_levels_api.py — añadir
def test_router_registrado_en_la_app():
    import btc_api
    rutas = {getattr(r, "path", None) for r in btc_api.app.routes}
    assert "/levels/{symbol}" in rutas
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_levels_api.py::test_router_registrado_en_la_app -v`
Expected: FAIL (la ruta no está registrada)

- [ ] **Step 3: Write minimal implementation**

En `btc_api.py`, tras la línea `from api.dossier import router as dossier_router`:

```python
from api.levels import router as levels_router
```

Y tras la línea `app.include_router(dossier_router)`:

```python
app.include_router(levels_router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_levels_api.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add btc_api.py tests/test_levels_api.py
git commit -m "feat(api): registrar el router de levels en la app"
```

---

### Task 8: Frontend — tipos + cliente de API

**Files:**
- Modify: `frontend/src/types.ts` (al final, tras los tipos de Dossier ~línea 540)
- Modify: `frontend/src/api.ts` (al final, tras `getDossier` ~línea 423)
- Test: `frontend/src/api.test.ts` (añadir)

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/api.test.ts — añadir dentro del describe existente o uno nuevo
import { describe, it, expect, vi, afterEach } from 'vitest';
import { getLevels } from './api';

describe('getLevels', () => {
  afterEach(() => vi.restoreAllMocks());

  it('pide GET /levels/:symbol y devuelve el payload', async () => {
    const payload = {
      symbol: 'BTCUSDT', estado: 'ok', generated_at: '2026-06-12T00:00:00+00:00',
      price_live: 100, zonas: [], ubicacion: { dentro_de: null, techo: null, piso: null },
    };
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    );
    const res = await getLevels('BTCUSDT');
    expect(res.symbol).toBe('BTCUSDT');
    expect(spy.mock.calls[0][0]).toContain('/levels/BTCUSDT');
  });
});
```

> Nota: si `api.test.ts` ya tiene un patrón distinto para mockear `fetch`/`request`, seguí ese patrón en vez de este; lo esencial es verificar que `getLevels` llama a `/levels/{symbol}` y tipa la respuesta como `SrLevels`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/api.test.ts`
Expected: FAIL (`getLevels` no exportado)

- [ ] **Step 3: Write minimal implementation**

En `frontend/src/types.ts`, al final del archivo:

```typescript
// ---- D.1 Detector neutral de S/R (zonas, sin veredicto). Spec §4 ----
export interface SrZona {
  tipo:                'resistencia' | 'soporte';
  precio_bajo:         number;
  precio_alto:         number;
  centro:              number;
  toques:              number;
  confluencia_redondo: number[];
}
export interface SrRef { centro: number; dist_pct: number; }
export interface SrUbicacion {
  dentro_de: SrZona | null;
  techo:     SrRef | null;
  piso:      SrRef | null;
}
export interface SrLevels {
  symbol:       string;
  estado:       'ok' | 'no_disponible';
  generated_at: string | null;
  price_live:   number | null;
  zonas:        SrZona[];
  ubicacion:    SrUbicacion;
}
```

En `frontend/src/api.ts`, al final del archivo:

```typescript
// ---- D.1 niveles S/R — GET /levels/:symbol -------------------------------

export function getLevels(symbol: string) {
  return request<import('./types').SrLevels>(`/levels/${symbol}`);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/api.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/api.ts frontend/src/api.test.ts
git commit -m "feat(fe): tipos SrLevels + cliente getLevels"
```

---

### Task 9: Frontend — componente `LevelsPanel`

**Files:**
- Create: `frontend/src/components/LevelsPanel.tsx`
- Create: `frontend/src/components/LevelsPanel.module.css`
- Test: `frontend/src/components/LevelsPanel.test.tsx`

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/components/LevelsPanel.test.tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { LevelsPanel } from './LevelsPanel';
import type { SrLevels } from '../types';

const ok: SrLevels = {
  symbol: 'BTCUSDT', estado: 'ok', generated_at: '2026-06-12T00:00:00+00:00',
  price_live: 67230,
  zonas: [
    { tipo: 'soporte', precio_bajo: 64800, precio_alto: 65400, centro: 65100, toques: 3, confluencia_redondo: [65000] },
    { tipo: 'resistencia', precio_bajo: 69000, precio_alto: 69200, centro: 69100, toques: 4, confluencia_redondo: [69000] },
  ],
  ubicacion: { dentro_de: null, techo: { centro: 69100, dist_pct: 2.78 }, piso: { centro: 65100, dist_pct: -3.17 } },
};

describe('LevelsPanel', () => {
  it('muestra las zonas como bandas con toques', () => {
    render(<LevelsPanel levels={ok} loading={false} />);
    expect(screen.getByText(/Resistencias/i)).toBeInTheDocument();
    expect(screen.getByText(/Soportes/i)).toBeInTheDocument();
    expect(screen.getByText(/3 toques/)).toBeInTheDocument();
  });

  it('distingue "no disponible"', () => {
    const nd: SrLevels = {
      symbol: 'BTCUSDT', estado: 'no_disponible', generated_at: null,
      price_live: null, zonas: [], ubicacion: { dentro_de: null, techo: null, piso: null },
    };
    render(<LevelsPanel levels={nd} loading={false} />);
    expect(screen.getByText(/sin datos/i)).toBeInTheDocument();
  });

  it('no muestra ningún texto de recomendación / veredicto', () => {
    const { container } = render(<LevelsPanel levels={ok} loading={false} />);
    expect(/recomend|comprar|vender|señal|signal|buy|sell|score|veredicto/i
      .test(container.textContent ?? '')).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/LevelsPanel.test.tsx`
Expected: FAIL (`LevelsPanel` no existe)

- [ ] **Step 3: Write minimal implementation**

```typescript
// frontend/src/components/LevelsPanel.tsx
// ============================================================
// LevelsPanel — niveles S/R NEUTRALES de un símbolo (D.1).
// Sin veredicto, sin badge de compra: bandas de pivotes + dónde
// está el precio vivo. Spec D.1 §5.
// ============================================================
import React from 'react';
import type { SrLevels, SrZona } from '../types';
import { formatPrice } from '../utils';
import styles from './LevelsPanel.module.css';

const Banda: React.FC<{ z: SrZona }> = ({ z }) => (
  <div className={styles.row}>
    <span className={styles.banda}>
      {formatPrice(z.precio_bajo)} – {formatPrice(z.precio_alto)}
    </span>
    <span className={styles.centro}>centro {formatPrice(z.centro)}</span>
    <span className={styles.toques}>{z.toques} toques</span>
    {z.confluencia_redondo.length > 0 && (
      <span className={styles.redondo}>
        redondo {z.confluencia_redondo.map((r) => formatPrice(r)).join(', ')}
      </span>
    )}
  </div>
);

export const LevelsPanel: React.FC<{ levels: SrLevels; loading: boolean }> = ({ levels, loading }) => {
  if (loading) return <div className={styles.empty}>Calculando niveles…</div>;
  const l = levels;
  if (l.estado === 'no_disponible') {
    return <div className={styles.empty}>Sin datos ahora. Probá de nuevo.</div>;
  }

  const resistencias = l.zonas.filter((z) => z.tipo === 'resistencia').sort((a, b) => b.centro - a.centro);
  const soportes = l.zonas.filter((z) => z.tipo === 'soporte').sort((a, b) => b.centro - a.centro);

  return (
    <div className={styles.wrap}>
      <header className={styles.head}>
        <span className={styles.sym}>{l.symbol.replace('USDT', '')}</span>
        {l.price_live != null && <span className={styles.price}>precio {formatPrice(l.price_live)}</span>}
        {l.generated_at && (
          <span className={styles.ts}>{new Date(l.generated_at).toLocaleTimeString('es-ES')}</span>
        )}
      </header>

      {resistencias.length > 0 && (
        <section className={styles.sec}>
          <h4>Resistencias</h4>
          {resistencias.map((z, i) => <Banda key={i} z={z} />)}
        </section>
      )}

      <div className={styles.locator}>
        {l.ubicacion.dentro_de ? (
          <span>Precio dentro de la zona {formatPrice(l.ubicacion.dentro_de.centro)}</span>
        ) : (
          <>
            <span>techo {l.ubicacion.techo
              ? `${formatPrice(l.ubicacion.techo.centro)} (+${l.ubicacion.techo.dist_pct}%)` : '—'}</span>
            <span>piso {l.ubicacion.piso
              ? `${formatPrice(l.ubicacion.piso.centro)} (${l.ubicacion.piso.dist_pct}%)` : '—'}</span>
          </>
        )}
      </div>

      {soportes.length > 0 && (
        <section className={styles.sec}>
          <h4>Soportes</h4>
          {soportes.map((z, i) => <Banda key={i} z={z} />)}
        </section>
      )}

      {l.zonas.length === 0 && (
        <p className={styles.gap}>No se detectaron niveles con los toques mínimos.</p>
      )}
    </div>
  );
};
```

```css
/* frontend/src/components/LevelsPanel.module.css */
.wrap { border: 1px solid var(--border, #2a2a2a); border-radius: 8px; padding: 12px; margin-top: 12px; }
.head { display: flex; align-items: baseline; gap: 12px; margin-bottom: 8px; }
.sym { font-weight: 600; }
.price { font-variant-numeric: tabular-nums; }
.ts { margin-left: auto; opacity: 0.6; font-size: 0.85em; }
.sec { margin: 8px 0; }
.sec h4 { margin: 4px 0; font-size: 0.9em; opacity: 0.8; }
.row { display: flex; gap: 12px; padding: 3px 0; font-variant-numeric: tabular-nums; font-size: 0.9em; }
.banda { min-width: 12em; }
.centro { opacity: 0.85; }
.toques { opacity: 0.7; }
.redondo { opacity: 0.6; font-style: italic; }
.locator { display: flex; gap: 18px; padding: 6px 0; border-top: 1px dashed var(--border, #2a2a2a); border-bottom: 1px dashed var(--border, #2a2a2a); margin: 6px 0; font-variant-numeric: tabular-nums; }
.empty { opacity: 0.7; padding: 12px; }
.gap { opacity: 0.7; }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/LevelsPanel.test.tsx`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/LevelsPanel.tsx frontend/src/components/LevelsPanel.module.css frontend/src/components/LevelsPanel.test.tsx
git commit -m "feat(fe): LevelsPanel — bandas S/R + ubicación del precio (sin veredicto)"
```

---

### Task 10: Frontend — botón "Niveles" en la Vista Valles

**Files:**
- Modify: `frontend/src/components/ValleysView.tsx`
- Modify: `frontend/src/components/ValleysView.test.tsx`

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/components/ValleysView.test.tsx — añadir dentro del describe
it('ofrece un botón "Niveles" por candidata', () => {
  render(<ValleysView snapshot={snap} loading={false} />);
  expect(screen.getAllByRole('button', { name: /niveles/i })).toHaveLength(2);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/ValleysView.test.tsx`
Expected: FAIL (no hay botón "Niveles")

- [ ] **Step 3: Write minimal implementation**

En `frontend/src/components/ValleysView.tsx`:

Cambiar los imports de tipos y de api:

```typescript
import type { ValleySnapshot, Dossier, SrLevels } from '../types';
import { getDossier, getLevels } from '../api';
import { ProjectDossier } from './ProjectDossier';
import { LevelsPanel } from './LevelsPanel';
```

Añadir estado tras las líneas de `dossier`:

```typescript
  const [levels, setLevels] = useState<SrLevels | null>(null);
  const [levelsLoading, setLevelsLoading] = useState(false);
```

En la celda de acciones de cada fila (el `<td>` que hoy tiene solo el botón Dossier), añadir el botón Niveles junto al existente:

```tsx
              <td>
                <button
                  className={styles.dossierBtn}
                  onClick={() => {
                    setDossier(null);
                    setDossierLoading(true);
                    getDossier(c.symbol)
                      .then(setDossier)
                      .finally(() => setDossierLoading(false));
                  }}
                >Dossier</button>
                <button
                  className={styles.dossierBtn}
                  onClick={() => {
                    setLevels(null);
                    setLevelsLoading(true);
                    getLevels(c.symbol)
                      .then(setLevels)
                      .finally(() => setLevelsLoading(false));
                  }}
                >Niveles</button>
              </td>
```

Y, tras el bloque que renderiza `<ProjectDossier .../>`, añadir el panel de niveles:

```tsx
      {(levels || levelsLoading) && (
        <LevelsPanel levels={levels!} loading={levelsLoading} />
      )}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/ValleysView.test.tsx`
Expected: PASS (incl. el test nuevo y los existentes, que no rompen)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ValleysView.tsx frontend/src/components/ValleysView.test.tsx
git commit -m "feat(fe): botón Niveles + panel S/R en la Vista Valles"
```

---

## Verificación final

- [ ] **Backend:** `python -m pytest tests/test_sr_levels.py tests/test_levels_api.py -v` → todo verde.
- [ ] **Gate rápido (como CI):** `python -m pytest tests/ -m "not network" -n auto -q` → sin regresiones.
- [ ] **Frontend:** `cd frontend && npm test` → todo verde.
- [ ] **Lint frontend (si aplica):** `cd frontend && npm run lint`.
- [ ] **Humo manual (opcional, requiere red):** con el API corriendo, `curl localhost:8100/levels/BTCUSDT` (puerto real de prod/local) y verificar `estado:"ok"` con zonas.
