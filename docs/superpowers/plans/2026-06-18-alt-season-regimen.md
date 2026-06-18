# Pieza de régimen "¿es alt-season?" — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Computar y exponer el régimen de mercado ("¿es alt-season?") como un hecho de mercado vivo (no veredicto per-coin) y exhibirlo como la cabecera honesta de Valles.

**Architecture:** Núcleo puro `regime/alt_season.py` (espejo de `screener/valley_filter.py`) calcula la inclinación del mercado por voto de 3 componentes (breadth + outperformance alt-vs-BTC + dominancia BTC). El screener (`tools/run_valley_screener.py`) acumula las contribuciones en su pasada de 6h existente (cero red extra salvo 1 llamada a CoinGecko) y escribe `data/alt_season.json` atómicamente. `GET /alt-season` lo sirve con `freshness.LiveSnapshot`. Una cabecera en el frontend lo exhibe.

**Tech Stack:** Python (stdlib + requests), FastAPI, pytest; React + TypeScript + vitest + Playwright.

**Spec:** `docs/superpowers/specs/es/2026-06-18-alt-season-regimen-design.md` (revisado por el roster; BLOCKERS+HIGH aplicados).

**Branch:** `feat/alt-season-regimen` (ya creada, ya tiene el commit del spec).

---

## File Structure

- **Create** `regime/__init__.py` — paquete nuevo (no colisiona con `strategy/regime.py`, que es otro concepto).
- **Create** `regime/alt_season.py` — núcleo puro: `symbol_contribution`, `compose_regime`, constantes.
- **Create** `tests/test_alt_season.py` — tests del núcleo puro.
- **Modify** `tools/run_valley_screener.py` — `_fetch_dominance`, `_atomic_write_json`, extender `build_snapshot`/`regenerate`/`main`.
- **Modify** `tests/test_run_valley_screener.py` — actualizar los 2 tests (unpack tuple + mock dominancia) + nuevos.
- **Create** `api/alt_season.py` — router lector `GET /alt-season`.
- **Modify** `btc_api.py` — import + `include_router`.
- **Create** `tests/test_alt_season_api.py` — tests del endpoint.
- **Modify** `scanner/runtime.py` — `_regenerate_screener` desempaqueta y loguea el régimen.
- **Modify** `docs/superpowers/inventario-estado-vivo.md` — fila `migrado`.
- **Modify** `frontend/src/types.ts` — tipos `RegimeComponent`/`RegimePayload`/`RegimeSnapshot`.
- **Modify** `frontend/src/api.ts` — `getAltSeason()`.
- **Create** `frontend/src/components/valles/AltSeasonHeader.tsx` (+ `AltSeasonHeader.module.css`).
- **Create** `frontend/src/components/valles/AltSeasonHeader.test.tsx` — vitest.
- **Modify** `frontend/src/components/valles/ValleysFlow.tsx` — montar la cabecera.
- **Create** `frontend/e2e/alt-season.spec.ts` — e2e liviano.
- **Modify** `.mex/patterns/correr-screener-valles.md` + puntero desde el doc de evidencia.

---

## Task 1: Núcleo puro — `symbol_contribution`

**Files:**
- Create: `regime/__init__.py`
- Create: `regime/alt_season.py`
- Test: `tests/test_alt_season.py`

- [ ] **Step 1: Crear el paquete**

```bash
# regime/__init__.py vacío (marca el paquete)
```
Contenido de `regime/__init__.py`: archivo vacío.

- [ ] **Step 2: Escribir el test que falla**

`tests/test_alt_season.py`:
```python
"""Tests del núcleo puro del régimen de mercado (alt-season). Sin red, sin DB."""
from regime.alt_season import symbol_contribution, MIN_HISTORY_DAYS


def _bars(closes):
    """Barras diarias mínimas desde una lista de cierres (highs/lows = close)."""
    return [{"open_time": i * 86_400_000, "open": c, "high": c, "low": c,
             "close": c, "volume": 1.0, "quote_volume": 1.0}
            for i, c in enumerate(closes)]


def test_contribution_none_si_historia_insuficiente():
    assert symbol_contribution("X", _bars([1.0] * (MIN_HISTORY_DAYS - 1))) is None


def test_contribution_above_sma50_y_ret_30d():
    # 50 cierres planos a 1.0, luego sube a 1.20 al final.
    closes = [1.0] * 49 + [1.20]
    c = symbol_contribution("X", _bars(closes))
    assert c is not None
    assert c["above_sma50"] is True            # 1.20 > media de la ventana
    # ret_30d = (1.20 - close_{t-30}) / close_{t-30}; close_{t-30} = 1.0
    assert abs(c["ret_30d"] - 0.20) < 1e-9


def test_contribution_below_sma50():
    closes = [1.0] * 49 + [0.80]
    c = symbol_contribution("X", _bars(closes))
    assert c["above_sma50"] is False
    assert abs(c["ret_30d"] - (-0.20)) < 1e-9
```

- [ ] **Step 3: Correr el test para verque falla**

Run: `python -m pytest tests/test_alt_season.py -q`
Expected: FAIL con `ModuleNotFoundError: No module named 'regime'` o `ImportError`.

- [ ] **Step 4: Implementar el núcleo (constantes + `symbol_contribution`)**

`regime/alt_season.py`:
```python
"""Régimen de mercado "¿es alt-season?" — cálculo PURO (sin red, sin DB).

Espejo de screener/valley_filter.py. Exhibe un HECHO de mercado (no per-símbolo,
no veredicto): la inclinación del mercado por voto de 3 componentes
(breadth + outperformance alt-vs-BTC + dominancia BTC), con los componentes
visibles. Eje MERCADO, distinto de classify_liveness (eje símbolo).

Contrato de barras: list[dict] diarias ascendentes con claves
{open_time, open, high, low, close, volume, quote_volume}.
"""
from __future__ import annotations

from statistics import mean, median

SMA_FAST = 50
RET_WINDOW_DAYS = 30
MIN_HISTORY_DAYS = 50   # cuello de botella = SMA50 (ret_30d sólo necesita 31)

# Umbrales de lean (PROVISIONALES, sin calibrar contra el panel 2020-2025).
BREADTH_ALT = 0.60
BREADTH_BEAR = 0.40
OUTPERF_ALT = 0.05
OUTPERF_BEAR = -0.05
DOM_ALT = 0.50
DOM_BTC = 0.58

# Gobierno de evidencia.
COVERAGE_MIN = 0.70
MIN_LIVE_VOTERS = 2


def symbol_contribution(symbol: str, bars: list[dict]) -> dict | None:
    """Contribución de UN símbolo al régimen. None si len(bars) < MIN_HISTORY_DAYS."""
    if len(bars) < MIN_HISTORY_DAYS:
        return None
    closes = [float(b["close"]) for b in bars]
    sma50 = mean(closes[-SMA_FAST:])
    close_t = closes[-1]
    close_30 = closes[-(RET_WINDOW_DAYS + 1)]
    ret_30d = (close_t - close_30) / close_30 if close_30 else 0.0
    return {"above_sma50": close_t > sma50, "ret_30d": ret_30d}
```

- [ ] **Step 5: Correr el test para verque pasa**

Run: `python -m pytest tests/test_alt_season.py -q`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add regime/__init__.py regime/alt_season.py tests/test_alt_season.py
git commit -m "feat(regime): núcleo puro alt-season — symbol_contribution"
```

---

## Task 2: Núcleo puro — `compose_regime`

**Files:**
- Modify: `regime/alt_season.py`
- Test: `tests/test_alt_season.py`

- [ ] **Step 1: Escribir los tests que fallan (batería de votación)**

Añadir a `tests/test_alt_season.py`:
```python
from regime.alt_season import compose_regime


def _contrib(above, ret):
    return {"above_sma50": above, "ret_30d": ret}


def test_compose_tres_votos_alts():
    # breadth alto (todos sobre sma50), outperf alto, dominancia baja → 3 votos alts.
    contribs = [_contrib(True, 0.30)] * 10       # breadth=1.0, ret alto
    out = compose_regime(contribs, btc_ret_30d=0.0, btc_dominance=0.45, coverage_ratio=1.0)
    assert out["estado"] == "alts"
    assert out["votos"] == {"alts": 3, "neutral": 0, "btc": 0, "vivos": 3}


def test_compose_dos_alts_un_btc_gana_alts():
    contribs = [_contrib(True, 0.30)] * 10       # breadth alts, outperf alts
    out = compose_regime(contribs, btc_ret_30d=0.0, btc_dominance=0.65, coverage_ratio=1.0)  # dom → btc
    assert out["estado"] == "alts"
    assert out["votos"]["vivos"] == 3


def test_compose_empate_es_mixto():
    # breadth alts, dominancia btc, outperf neutral → 1-1-1 → mixto.
    contribs = [_contrib(True, 0.0)] * 10        # breadth=1.0 (alts), outperf=0.0 (neutral)
    out = compose_regime(contribs, btc_ret_30d=0.0, btc_dominance=0.65, coverage_ratio=1.0)
    assert out["estado"] == "mixto"


def test_compose_dominancia_muerta_vota_con_dos():
    contribs = [_contrib(True, 0.30)] * 10       # breadth alts, outperf alts
    out = compose_regime(contribs, btc_ret_30d=0.0, btc_dominance=None, coverage_ratio=1.0)
    assert out["estado"] == "alts"
    assert out["votos"]["vivos"] == 2
    assert out["componentes"]["dominancia_btc"]["estado"] == "muerto"
    assert out["componentes"]["dominancia_btc"]["valor"] is None


def test_compose_un_solo_votante_vivo_es_mixto():
    # outperf muerto (btc_ret None) + dominancia muerta → solo breadth vivo → mixto.
    contribs = [_contrib(True, 0.30)] * 10
    out = compose_regime(contribs, btc_ret_30d=None, btc_dominance=None, coverage_ratio=1.0)
    assert out["estado"] == "mixto"
    assert out["votos"]["vivos"] == 1


def test_compose_cobertura_baja_mata_breadth():
    contribs = [_contrib(True, 0.30)] * 10
    out = compose_regime(contribs, btc_ret_30d=0.0, btc_dominance=0.45, coverage_ratio=0.5)
    assert out["componentes"]["breadth50"]["estado"] == "muerto"
    assert out["componentes"]["breadth50"]["razon"] == "cobertura_baja"
    assert out["componentes"]["breadth50"]["valor"] is not None   # el valor se muestra igual
    assert out["votos"]["vivos"] == 2                              # solo outperf + dominancia


def test_compose_frontera_breadth_060_es_alts():
    # 6 de 10 sobre sma50 → breadth=0.60 → alts (>=).
    contribs = [_contrib(True, 0.0)] * 6 + [_contrib(False, 0.0)] * 4
    out = compose_regime(contribs, btc_ret_30d=0.0, btc_dominance=0.55, coverage_ratio=1.0)
    assert out["componentes"]["breadth50"]["lean"] == "alts"
```

- [ ] **Step 2: Correr para verque fallan**

Run: `python -m pytest tests/test_alt_season.py -q`
Expected: FAIL con `ImportError: cannot import name 'compose_regime'`.

- [ ] **Step 3: Implementar `compose_regime` (+ helpers de lean)**

Añadir a `regime/alt_season.py`:
```python
def _lean_higher_alt(value: float, alt_thr: float, bear_thr: float) -> str:
    """alts si value ≥ alt_thr ; btc si value ≤ bear_thr ; si no neutral."""
    if value >= alt_thr:
        return "alts"
    if value <= bear_thr:
        return "btc"
    return "neutral"


def _lean_lower_alt(value: float, alt_thr: float, bear_thr: float) -> str:
    """Dominancia: MENOR = alts. alts si value ≤ alt_thr ; btc si value ≥ bear_thr."""
    if value <= alt_thr:
        return "alts"
    if value >= bear_thr:
        return "btc"
    return "neutral"


def compose_regime(alt_contribs: list[dict], btc_ret_30d: float | None,
                   btc_dominance: float | None, coverage_ratio: float) -> dict:
    """Compone el estado de régimen por voto de 3 componentes. Hecho de mercado,
    cero campo per-símbolo, cero valencia. Ver spec §Núcleo."""
    voters: list[str] = []
    componentes: dict[str, dict] = {}

    # 1. breadth50 — vota sólo si la cobertura alcanza el piso.
    breadth50 = (mean(1.0 if c["above_sma50"] else 0.0 for c in alt_contribs)
                 if alt_contribs else None)
    if breadth50 is not None and coverage_ratio >= COVERAGE_MIN:
        lean = _lean_higher_alt(breadth50, BREADTH_ALT, BREADTH_BEAR)
        componentes["breadth50"] = {"valor": breadth50, "lean": lean,
                                    "estado": "fresco", "n": len(alt_contribs)}
        voters.append(lean)
    else:
        componentes["breadth50"] = {
            "valor": breadth50, "lean": None, "estado": "muerto",
            "n": len(alt_contribs),
            "razon": "cobertura_baja" if breadth50 is not None else "sin_datos"}

    # 2. outperf_30d — mediana de (ret alt - ret BTC). Muerto si BTC no evaluable.
    if alt_contribs and btc_ret_30d is not None:
        outperf = median(c["ret_30d"] - btc_ret_30d for c in alt_contribs)
        lean = _lean_higher_alt(outperf, OUTPERF_ALT, OUTPERF_BEAR)
        componentes["outperf_30d"] = {"valor": outperf, "lean": lean, "estado": "fresco"}
        voters.append(lean)
    else:
        componentes["outperf_30d"] = {"valor": None, "lean": None, "estado": "muerto"}

    # 3. dominancia_btc — muerta si la llamada a CoinGecko falló.
    if btc_dominance is not None:
        lean = _lean_lower_alt(btc_dominance, DOM_ALT, DOM_BTC)
        componentes["dominancia_btc"] = {"valor": btc_dominance, "lean": lean,
                                         "estado": "fresco"}
        voters.append(lean)
    else:
        componentes["dominancia_btc"] = {"valor": None, "lean": None, "estado": "muerto"}

    n_alts = voters.count("alts")
    n_btc = voters.count("btc")
    n_neutral = voters.count("neutral")
    n_live = len(voters)
    if n_live < MIN_LIVE_VOTERS:
        estado = "mixto"
    elif n_alts > n_btc and n_alts > n_neutral:
        estado = "alts"
    elif n_btc > n_alts and n_btc > n_neutral:
        estado = "btc"
    else:
        estado = "mixto"

    return {
        "estado": estado,
        "componentes": componentes,
        "votos": {"alts": n_alts, "neutral": n_neutral, "btc": n_btc, "vivos": n_live},
        "n_alts_evaluadas": len(alt_contribs),
    }
```

- [ ] **Step 4: Correr para verque pasan**

Run: `python -m pytest tests/test_alt_season.py -q`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add regime/alt_season.py tests/test_alt_season.py
git commit -m "feat(regime): compose_regime — voto determinista de 3 componentes"
```

---

## Task 3: I/O — `_fetch_dominance` (CoinGecko, degradación elegante)

**Files:**
- Modify: `tools/run_valley_screener.py`
- Test: `tests/test_run_valley_screener.py`

- [ ] **Step 1: Escribir los tests que fallan**

Añadir a `tests/test_run_valley_screener.py`:
```python
from unittest.mock import MagicMock
import tools.run_valley_screener as rvs


def _fake_resp(status, payload):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = payload
    return m


def test_fetch_dominance_ok():
    payload = {"data": {"market_cap_percentage": {"btc": 53.9}}}
    with patch("tools.run_valley_screener.requests.get", return_value=_fake_resp(200, payload)):
        assert abs(rvs._fetch_dominance() - 0.539) < 1e-9


def test_fetch_dominance_shape_inesperado_es_none():
    with patch("tools.run_valley_screener.requests.get", return_value=_fake_resp(200, {"data": {}})):
        assert rvs._fetch_dominance() is None


def test_fetch_dominance_fuera_de_rango_es_none():
    payload = {"data": {"market_cap_percentage": {"btc": 150.0}}}  # >100% → 1.5
    with patch("tools.run_valley_screener.requests.get", return_value=_fake_resp(200, payload)):
        assert rvs._fetch_dominance() is None


def test_fetch_dominance_error_de_red_es_none():
    import requests
    with patch("tools.run_valley_screener.requests.get", side_effect=requests.RequestException("boom")):
        assert rvs._fetch_dominance() is None
```

- [ ] **Step 2: Correr para verque fallan**

Run: `python -m pytest tests/test_run_valley_screener.py -q`
Expected: FAIL con `AttributeError: module 'tools.run_valley_screener' has no attribute '_fetch_dominance'`.

- [ ] **Step 3: Implementar `_fetch_dominance`**

Añadir a `tools/run_valley_screener.py` (tras `_fetch_daily_klines`). Añadir también la constante de URL cerca de `_KLINES_URL`:
```python
_DOMINANCE_URL = "https://api.coingecko.com/api/v3/global"


def _fetch_dominance() -> float | None:
    """Dominancia de BTC (market-cap) de CoinGecko, fracción 0-1. None ante CUALQUIER
    fallo o valor fuera de rango — degradación elegante; NO tumba la pasada."""
    try:
        r = requests.get(_DOMINANCE_URL, timeout=(3.05, 10))
        if r.status_code != 200:
            log.warning("DOMINANCE_FETCH_HTTP status=%s", r.status_code)
            return None
        dom = float(r.json()["data"]["market_cap_percentage"]["btc"]) / 100.0
    except (requests.RequestException, KeyError, TypeError, ValueError) as e:
        log.warning("DOMINANCE_FETCH_FAILED causa=%s", e)
        return None
    if not (0.0 < dom < 1.0):
        log.warning("DOMINANCE_OUT_OF_RANGE value=%s", dom)
        return None
    return dom
```

- [ ] **Step 4: Correr para verque pasan**

Run: `python -m pytest tests/test_run_valley_screener.py -q`
Expected: PASS (los 2 viejos siguen verdes + 4 nuevos).

- [ ] **Step 5: Commit**

```bash
git add tools/run_valley_screener.py tests/test_run_valley_screener.py
git commit -m "feat(screener): _fetch_dominance (CoinGecko) con degradación elegante"
```

---

## Task 4: I/O — extender `build_snapshot`/`regenerate` (acumular régimen + escritura atómica)

**Files:**
- Modify: `tools/run_valley_screener.py`
- Test: `tests/test_run_valley_screener.py`

- [ ] **Step 1: Actualizar los 2 tests vigentes (unpack tuple + mock dominancia) y añadir el test de régimen**

En `tests/test_run_valley_screener.py`, reemplazar el cuerpo de los dos tests existentes para desempaquetar la tupla y mockear la dominancia, y añadir un test nuevo. Los dos tests vigentes quedan así:

```python
def test_snapshot_incluye_candidata_viva_y_omite_muerta():
    universo = ["LIVEUSDT", "DEADUSDT"]

    def fake_klines(symbol, **kw):
        if symbol == "LIVEUSDT":
            return _kline_rows(150, 1.0, 2_000_000.0)
        return _kline_rows(150, 1.0, 50_000.0)

    with patch("tools.run_valley_screener.list_live_usdt_spot", return_value=universo), \
         patch("tools.run_valley_screener._fetch_daily_klines", side_effect=fake_klines), \
         patch("tools.run_valley_screener._fetch_dominance", return_value=0.55):
        cand_snap, _regime_snap = build_snapshot()

    syms = [c["symbol"] for c in cand_snap["candidates"]]
    assert "LIVEUSDT" in syms
    assert "DEADUSDT" not in syms
    assert cand_snap["coverage"]["universe"] == 2
    assert cand_snap["coverage"]["evaluated"] == 2
    assert cand_snap["coverage"]["complete"] is True
    assert "generated_at" in cand_snap


def test_fallo_de_un_simbolo_no_tumba_el_run_y_marca_cobertura():
    universo = ["GOODUSDT", "BROKENUSDT"]

    def fake_klines(symbol, **kw):
        if symbol == "BROKENUSDT":
            raise RuntimeError("kline fetch boom")
        return _kline_rows(150, 1.0, 2_000_000.0)

    with patch("tools.run_valley_screener.list_live_usdt_spot", return_value=universo), \
         patch("tools.run_valley_screener._fetch_daily_klines", side_effect=fake_klines), \
         patch("tools.run_valley_screener._fetch_dominance", return_value=0.55):
        cand_snap, _regime_snap = build_snapshot()

    assert cand_snap["coverage"]["universe"] == 2
    assert cand_snap["coverage"]["evaluated"] == 1
    assert cand_snap["coverage"]["complete"] is False
```

Test nuevo (régimen acumulado + BTC excluido de alts + shape):
```python
def test_build_snapshot_acumula_regimen_y_excluye_btc():
    universo = ["BTCUSDT", "ALT1USDT", "ALT2USDT"]

    def fake_klines(symbol, **kw):
        # BTC plano; alts suben fuerte (above_sma50, ret alto) → outperf alts.
        if symbol == "BTCUSDT":
            return _kline_rows(60, 1.0, 5_000_000.0)
        return _kline_rows(60, 1.0, 2_000_000.0)[:-1] + [
            [59 * 86_400_000, "1.30", "1.34", "1.26", "1.30", "1538461.0", 0,
             "2000000.0", 0, "0", "0", "0"]]

    with patch("tools.run_valley_screener.list_live_usdt_spot", return_value=universo), \
         patch("tools.run_valley_screener._fetch_daily_klines", side_effect=fake_klines), \
         patch("tools.run_valley_screener._fetch_dominance", return_value=0.45):
        _cand_snap, regime_snap = build_snapshot()

    reg = regime_snap["regime"]
    assert regime_snap["dominancia_fetch"]["ok"] is True
    assert reg["n_alts_evaluadas"] == 2                      # BTC excluido
    assert reg["componentes"]["dominancia_btc"]["estado"] == "fresco"
    assert reg["votos"]["vivos"] == 3
    assert regime_snap["generated_at"] == _cand_snap["generated_at"]   # mismo cierre


def test_regenerate_escribe_alt_season_atomicamente(tmp_path, monkeypatch):
    monkeypatch.setattr(rvs, "_OUTPUT", str(tmp_path / "cand.json"))
    monkeypatch.setattr(rvs, "_ALT_SEASON_OUTPUT", str(tmp_path / "alt_season.json"))
    universo = ["BTCUSDT", "ALT1USDT"]

    with patch("tools.run_valley_screener.list_live_usdt_spot", return_value=universo), \
         patch("tools.run_valley_screener._fetch_daily_klines",
               side_effect=lambda s, **k: _kline_rows(60, 1.0, 2_000_000.0)), \
         patch("tools.run_valley_screener._fetch_dominance", return_value=0.50):
        cand_snap, regime_snap = rvs.regenerate(pause_s=0.0)

    assert (tmp_path / "cand.json").exists()
    assert (tmp_path / "alt_season.json").exists()
    written = json.loads((tmp_path / "alt_season.json").read_text(encoding="utf-8"))
    assert written["regime"]["estado"] in ("alts", "mixto", "btc")
    assert "dominancia_fetch" in written
```

Añadir al tope del archivo de test los imports que falten: `import json`.

- [ ] **Step 2: Correr para verque fallan**

Run: `python -m pytest tests/test_run_valley_screener.py -q`
Expected: FAIL (`build_snapshot` aún devuelve un dict, no una tupla; `_ALT_SEASON_OUTPUT` no existe).

- [ ] **Step 3: Implementar la extensión**

En `tools/run_valley_screener.py`:

(a) imports/constantes nuevas (arriba, junto a los existentes):
```python
import tempfile

from regime.alt_season import compose_regime, symbol_contribution

_ALT_SEASON_OUTPUT = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                  "data", "alt_season.json")
_BTC_SYMBOL = "BTCUSDT"
```

(b) helper de escritura atómica:
```python
def _atomic_write_json(path: str, obj: dict) -> None:
    """Escribe JSON atómicamente: tempfile en el MISMO dir + os.replace. Un lector
    concurrente nunca ve un archivo truncado (no falso 'muerto')."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
```

(c) reemplazar `build_snapshot` por la versión que acumula ambas fotos:
```python
def build_snapshot(*, pause_s: float = 0.0,
                   generated_at: str | None = None) -> tuple[dict, dict]:
    """Construye AMBAS fotos (candidatas + régimen) en UNA pasada del universo.
    Devuelve (candidates_snap, alt_season_snap). No escribe a disco."""
    universo = list_live_usdt_spot()
    ts = generated_at or datetime.now(timezone.utc).isoformat()
    candidatas: list[dict] = []
    alt_contribs: list[dict] = []
    btc_ret_30d: float | None = None
    btc_seen = False
    evaluadas = 0
    for sym in universo:
        try:
            rows = _fetch_daily_klines(sym)
        except (requests.RequestException, RuntimeError) as e:
            log.warning("SCREENER_SYMBOL_SKIPPED symbol=%s causa=%s", sym, e)
            continue
        evaluadas += 1
        bars = _rows_to_bars(rows)
        cand = evaluate_symbol(sym, bars)
        if cand is not None:
            candidatas.append(cand)
        contrib = symbol_contribution(sym, bars)
        if contrib is not None:
            if sym == _BTC_SYMBOL:
                btc_ret_30d = contrib["ret_30d"]
                btc_seen = True
            else:
                alt_contribs.append(contrib)
        if pause_s:
            time.sleep(pause_s)

    if not btc_seen:
        log.warning("REGIME_BTC_AUSENTE: BTCUSDT no evaluable en esta pasada")

    coverage = {"universe": len(universo), "evaluated": evaluadas,
                "complete": evaluadas == len(universo)}
    cand_snap = {"generated_at": ts, "coverage": coverage,
                 "candidates": order_neutral(candidatas)}

    dominance = _fetch_dominance()
    coverage_ratio = (evaluadas / len(universo)) if universo else 0.0
    regime = compose_regime(alt_contribs, btc_ret_30d, dominance, coverage_ratio)
    alt_season_snap = {
        "generated_at": ts,
        "coverage": coverage,
        "dominancia_fetch": {"ok": dominance is not None,
                             "fetched_at": ts if dominance is not None else None,
                             "source": "coingecko/global"},
        "regime": regime,
    }
    return cand_snap, alt_season_snap
```

(d) reemplazar `regenerate` (escribe candidatas primero, régimen atómico después):
```python
def regenerate(*, pause_s: float = 0.05) -> tuple[dict, dict]:
    """build_snapshot + escribe ambos JSON. Usado por main() y por _regenerate_screener."""
    cand_snap, alt_season_snap = build_snapshot(pause_s=pause_s)
    os.makedirs(os.path.dirname(_OUTPUT), exist_ok=True)
    with open(_OUTPUT, "w", encoding="utf-8") as f:
        json.dump(cand_snap, f, indent=2, ensure_ascii=False)
    _atomic_write_json(_ALT_SEASON_OUTPUT, alt_season_snap)   # atómico (BLOCKER del review)
    return cand_snap, alt_season_snap
```

(e) actualizar `main` para desempaquetar:
```python
def main() -> int:
    logging.basicConfig(level=logging.INFO)
    cand_snap, alt_season_snap = regenerate()
    cov = cand_snap["coverage"]
    print(f"valley_candidates.json: {len(cand_snap['candidates'])} candidatas; "
          f"cobertura {cov['evaluated']}/{cov['universe']} "
          f"({'completa' if cov['complete'] else 'INCOMPLETA'})")
    reg = alt_season_snap["regime"]
    print(f"alt_season.json: régimen={reg['estado']} votos={reg['votos']}")
    return 0
```

- [ ] **Step 4: Correr para verque pasan**

Run: `python -m pytest tests/test_run_valley_screener.py -q`
Expected: PASS (2 actualizados + 4 de Task 3 + 2 nuevos).

- [ ] **Step 5: Commit**

```bash
git add tools/run_valley_screener.py tests/test_run_valley_screener.py
git commit -m "feat(screener): acumular régimen en la pasada + escritura atómica de alt_season.json"
```

---

## Task 5: Reader — `api/alt_season.py` + registro en `btc_api.py`

**Files:**
- Create: `api/alt_season.py`
- Modify: `btc_api.py:91` (import), `btc_api.py:317` (include_router)
- Test: `tests/test_alt_season_api.py`

- [ ] **Step 1: Escribir los tests que fallan**

`tests/test_alt_season_api.py`:
```python
"""Tests del endpoint GET /alt-season (régimen de mercado, Valles)."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.alt_season import router


def _app():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _snap(generated_at, estado="alts"):
    return {"generated_at": generated_at,
            "coverage": {"universe": 3, "evaluated": 3, "complete": True},
            "dominancia_fetch": {"ok": True, "fetched_at": generated_at, "source": "coingecko/global"},
            "regime": {"estado": estado, "componentes": {}, "n_alts_evaluadas": 2,
                       "votos": {"alts": 3, "neutral": 0, "btc": 0, "vivos": 3}}}


def test_foto_fresca_es_fresca_y_trae_estado(tmp_path):
    ahora = datetime.now(timezone.utc).isoformat()
    p = tmp_path / "alt_season.json"
    p.write_text(json.dumps(_snap(ahora)), encoding="utf-8")
    with patch("api.alt_season._OUTPUT", str(p)):
        r = _app().get("/alt-season")
    assert r.status_code == 200
    body = r.json()
    assert body["frescura"]["estado"] == "fresco"
    assert body["regime"]["estado"] == "alts"


def test_foto_vieja_es_rancia(tmp_path):
    viejo = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    p = tmp_path / "alt_season.json"
    p.write_text(json.dumps(_snap(viejo)), encoding="utf-8")
    with patch("api.alt_season._OUTPUT", str(p)):
        r = _app().get("/alt-season")
    assert r.json()["frescura"]["estado"] == "rancio"


def test_foto_ausente_es_muerto_no_vacio_mudo(tmp_path):
    with patch("api.alt_season._OUTPUT", str(tmp_path / "nope.json")):
        r = _app().get("/alt-season")
    assert r.status_code == 200
    assert r.json()["frescura"]["estado"] == "muerto"


def test_payload_sin_lenguaje_de_veredicto_ni_per_simbolo(tmp_path):
    ahora = datetime.now(timezone.utc).isoformat()
    p = tmp_path / "alt_season.json"
    p.write_text(json.dumps(_snap(ahora)), encoding="utf-8")
    with patch("api.alt_season._OUTPUT", str(p)):
        body = _app().get("/alt-season").json()
    blob = json.dumps(body, ensure_ascii=False).lower()
    for prohibido in ("comprar", "vender", "subirá", "entra", "señal de compra",
                      "mandan", "manda", "fuertes", "débil", "débiles", "symbol"):
        assert prohibido not in blob, f"lenguaje prohibido en el payload: {prohibido}"
```

- [ ] **Step 2: Correr para verque fallan**

Run: `python -m pytest tests/test_alt_season_api.py -q`
Expected: FAIL con `ModuleNotFoundError: No module named 'api.alt_season'`.

- [ ] **Step 3: Implementar el router**

`api/alt_season.py`:
```python
"""API del régimen de mercado "¿es alt-season?" (Valles). Read-only, NO per-tenant —
el universo de mercado es global.

Lee la foto que escribe tools.run_valley_screener (misma pasada del screener, owner
de frescura = screener_loop). NO computa nada en el request. Eje MERCADO: hecho, no
veredicto per-símbolo."""
from __future__ import annotations

import json
import logging
import os

from fastapi import APIRouter

from api.valleys import FRESCURA_VALLES_SEG   # mismo writer/loop → misma semántica
from freshness import LiveSnapshot

log = logging.getLogger("api.alt_season")

router = APIRouter(tags=["alt-season"])

_OUTPUT = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                       "data", "alt_season.json")

_EMPTY = {
    "generated_at": None,
    "coverage": {"universe": 0, "evaluated": 0, "complete": False},
    "dominancia_fetch": {"ok": False, "fetched_at": None, "source": "coingecko/global"},
    "regime": {"estado": "mixto", "componentes": {},
               "votos": {"alts": 0, "neutral": 0, "btc": 0, "vivos": 0},
               "n_alts_evaluadas": 0},
}


@router.get("/alt-season",
            summary="Régimen de mercado ¿es alt-season? (hecho de mercado, no veredicto)")
def get_alt_season() -> dict:
    """Devuelve la foto del régimen con su FRESCURA en el contrato. Archivo ausente →
    'muerto' (el screener no ha corrido), distinto de una foto vieja → 'rancio'.
    'fresco' = el cálculo es reciente, NO que la afirmación de mercado siga vigente."""
    if not os.path.exists(_OUTPUT):
        return LiveSnapshot(payload=dict(_EMPTY), generated_at=None,
                            umbral_seg=FRESCURA_VALLES_SEG).to_response()
    try:
        with open(_OUTPUT, encoding="utf-8") as f:
            snap = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.error("ALT_SEASON_SNAPSHOT_UNREADABLE causa=%s", e)
        snap = dict(_EMPTY)
    return LiveSnapshot(payload=snap, generated_at=snap.get("generated_at"),
                        umbral_seg=FRESCURA_VALLES_SEG).to_response()
```

- [ ] **Step 4: Registrar el router en `btc_api.py`**

En `btc_api.py`, junto a la línea `from api.valleys import router as valleys_router` (:91):
```python
from api.alt_season import router as alt_season_router
```
Y junto a `app.include_router(valleys_router)` (:317), justo después:
```python
app.include_router(alt_season_router)
```

- [ ] **Step 5: Correr los tests del endpoint y la suite de API**

Run: `python -m pytest tests/test_alt_season_api.py tests/test_api.py -q`
Expected: PASS (4 nuevos + la suite de API sigue verde).

- [ ] **Step 6: Commit**

```bash
git add api/alt_season.py btc_api.py tests/test_alt_season_api.py
git commit -m "feat(api): GET /alt-season con LiveSnapshot + registro en btc_api"
```

---

## Task 6: Owner — `_regenerate_screener` loguea el régimen

**Files:**
- Modify: `scanner/runtime.py:408-411`
- Test: `tests/test_run_valley_screener.py` (cubre `regenerate`; el wrapper es un log)

- [ ] **Step 1: Verificar que `regenerate` devuelve la tupla (cubierto por Task 4)**

Run: `python -m pytest tests/test_run_valley_screener.py::test_regenerate_escribe_alt_season_atomicamente -q`
Expected: PASS.

- [ ] **Step 2: Actualizar `_regenerate_screener` para desempaquetar y loguear el régimen**

Reemplazar `scanner/runtime.py:408-411`:
```python
def _regenerate_screener() -> None:
    from tools.run_valley_screener import regenerate  # noqa: PLC0415
    cand_snap, alt_season_snap = regenerate()
    log.info("screener_loop: %d candidatas, régimen=%s",
             len(cand_snap.get("candidates", [])),
             alt_season_snap.get("regime", {}).get("estado"))
```

- [ ] **Step 3: Verificar que la suite del scanner/runtime sigue verde**

Run: `python -m pytest tests/test_scanner.py -q -k "screener or runtime" ; python -m pytest tests/ -m "not network" -n auto -q`
Expected: PASS (sin regresiones; el gate completo verde).

- [ ] **Step 4: Commit**

```bash
git add scanner/runtime.py
git commit -m "feat(runtime): _regenerate_screener loguea el estado del régimen"
```

---

## Task 7: Inventario de estado vivo — fila `migrado`

**Files:**
- Modify: `docs/superpowers/inventario-estado-vivo.md`

- [ ] **Step 1: Añadir la fila a la tabla**

En `docs/superpowers/inventario-estado-vivo.md`, añadir tras la fila de `GET /valley-eval/{symbol}`:
```markdown
| `GET /alt-season` | `tools.run_valley_screener.regenerate` (vía `_regenerate_screener`) | `screener_loop` (**trading-scanner.service**, 6h) | `LiveSnapshot` (+ frescura interna de dominancia en payload) | **migrado** |
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/inventario-estado-vivo.md
git commit -m "docs(liveness): registrar GET /alt-season en el inventario de estado vivo"
```

---

## Task 8: Frontend — tipos + cliente de API

**Files:**
- Modify: `frontend/src/types.ts` (tras `ValleySnapshot`, ~línea 548)
- Modify: `frontend/src/api.ts` (tras `getValleyCandidates`, ~línea 419)

- [ ] **Step 1: Añadir los tipos**

En `frontend/src/types.ts`, tras la interfaz `ValleySnapshot`:
```typescript
// ---- Régimen de mercado "¿es alt-season?" (hecho de mercado, no veredicto) ----
export interface RegimeComponent {
  valor:  number | null;
  lean:   'alts' | 'neutral' | 'btc' | null;
  estado: 'fresco' | 'muerto';
  n?:     number;
  razon?: string;
}
export interface RegimePayload {
  estado:            'alts' | 'mixto' | 'btc';
  componentes:       Record<string, RegimeComponent>;
  votos:             { alts: number; neutral: number; btc: number; vivos: number };
  n_alts_evaluadas:  number;
}
export interface RegimeSnapshot {
  generated_at:     string | null;
  coverage:         { universe: number; evaluated: number; complete: boolean };
  dominancia_fetch: { ok: boolean; fetched_at: string | null; source: string };
  regime:           RegimePayload;
  frescura?:        Frescura;
}
```

- [ ] **Step 2: Añadir la función de fetch**

En `frontend/src/api.ts`, tras `getValleyCandidates`:
```typescript
// ---- Régimen de mercado — GET /alt-season -------------------------------
export function getAltSeason() {
  return request<import('./types').RegimeSnapshot>('/alt-season');
}
```

- [ ] **Step 3: Verificar que compila (typecheck)**

Run: `cd frontend && npx tsc --noEmit`
Expected: sin errores nuevos.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types.ts frontend/src/api.ts
git commit -m "feat(frontend): tipos RegimeSnapshot + getAltSeason()"
```

---

## Task 9: Frontend — componente `AltSeasonHeader` (+ vitest)

**Files:**
- Create: `frontend/src/components/valles/AltSeasonHeader.tsx`
- Create: `frontend/src/components/valles/AltSeasonHeader.module.css`
- Test: `frontend/src/components/valles/AltSeasonHeader.test.tsx`

- [ ] **Step 1: Escribir el test que falla (vitest)**

`frontend/src/components/valles/AltSeasonHeader.test.tsx`:
```tsx
import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { AltSeasonHeader } from './AltSeasonHeader';
import type { RegimeSnapshot } from '../../types';

vi.mock('../../api', () => ({ getAltSeason: vi.fn() }));
import { getAltSeason } from '../../api';

function snap(over: Partial<RegimeSnapshot> = {}): RegimeSnapshot {
  return {
    generated_at: new Date().toISOString(),
    coverage: { universe: 3, evaluated: 3, complete: true },
    dominancia_fetch: { ok: true, fetched_at: null, source: 'coingecko/global' },
    regime: {
      estado: 'alts',
      componentes: {
        breadth50: { valor: 0.62, lean: 'alts', estado: 'fresco', n: 418 },
        outperf_30d: { valor: 0.071, lean: 'alts', estado: 'fresco' },
        dominancia_btc: { valor: 0.539, lean: 'alts', estado: 'fresco' },
      },
      votos: { alts: 3, neutral: 0, btc: 0, vivos: 3 },
      n_alts_evaluadas: 418,
    },
    frescura: { estado: 'fresco', edad_seg: 10, generated_at: null, umbral_seg: 43200 },
    ...over,
  };
}

describe('AltSeasonHeader', () => {
  it('muestra el estado, los 3 componentes y la frase honesta', async () => {
    (getAltSeason as any).mockResolvedValue(snap());
    render(<AltSeasonHeader />);
    await waitFor(() => expect(screen.getByTestId('regime-estado')).toBeInTheDocument());
    expect(screen.getByTestId('regime-estado').textContent).toMatch(/alts/i);
    expect(screen.getByText(/breadth/i)).toBeInTheDocument();
    expect(screen.getByText(/dominancia/i)).toBeInTheDocument();
    expect(screen.getByText(/régimen del mercado/i)).toBeInTheDocument();
  });

  it('muestra la dominancia degradada cuando está muerta', async () => {
    const s = snap();
    s.regime.componentes.dominancia_btc = { valor: null, lean: null, estado: 'muerto' };
    s.dominancia_fetch.ok = false;
    (getAltSeason as any).mockResolvedValue(s);
    render(<AltSeasonHeader />);
    await waitFor(() => expect(screen.getByTestId('dominancia-muerta')).toBeInTheDocument());
  });
});
```

- [ ] **Step 2: Correr para verque falla**

Run: `cd frontend && npx vitest run src/components/valles/AltSeasonHeader.test.tsx`
Expected: FAIL (no existe el componente).

- [ ] **Step 3: Implementar el componente + estilos**

`frontend/src/components/valles/AltSeasonHeader.module.css`:
```css
.header { padding: clamp(16px, 3vw, 28px); border-radius: 14px; font-size: 18px; }
.estado { font-size: 22px; font-weight: 600; }
.componentes { display: flex; gap: 24px; flex-wrap: wrap; margin-top: 8px; }
.comp { font-size: 18px; }
.muerta { opacity: 0.6; font-style: italic; }
.frase { margin-top: 12px; font-size: 18px; opacity: 0.85; }
.frescura { font-size: 15px; opacity: 0.7; margin-top: 6px; }
```

`frontend/src/components/valles/AltSeasonHeader.tsx`:
```tsx
import React, { useEffect, useState } from 'react';
import { getAltSeason } from '../../api';
import type { RegimeSnapshot } from '../../types';
import styles from './AltSeasonHeader.module.css';

const ESTADO_LABEL: Record<string, string> = {
  alts: 'Inclinación del mercado: hacia alts',
  mixto: 'Inclinación del mercado: mixta',
  btc: 'Inclinación del mercado: hacia BTC',
};

function pct(v: number | null): string {
  return v === null ? '—' : `${(v * 100).toFixed(1)}%`;
}

export const AltSeasonHeader: React.FC = () => {
  const [snap, setSnap] = useState<RegimeSnapshot | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let alive = true;
    getAltSeason().then(s => { if (alive) setSnap(s); }).catch(() => { if (alive) setError(true); });
    return () => { alive = false; };
  }, []);

  if (error) return <div className={styles.header}>El régimen de mercado no está disponible ahora.</div>;
  if (!snap) return <div className={styles.header}>Cargando el régimen del mercado…</div>;

  const { regime, frescura } = snap;
  const c = regime.componentes;
  const dom = c.dominancia_btc;

  return (
    <div className={styles.header}>
      <div className={styles.estado} data-testid="regime-estado">
        {ESTADO_LABEL[regime.estado] ?? regime.estado}
      </div>
      <div className={styles.componentes}>
        <span className={styles.comp}>breadth: {pct(c.breadth50?.valor ?? null)}
          {c.breadth50?.n != null ? ` (n=${c.breadth50.n})` : ''}</span>
        <span className={styles.comp}>outperf 30d: {pct(c.outperf_30d?.valor ?? null)}</span>
        {dom?.estado === 'muerto'
          ? <span className={`${styles.comp} ${styles.muerta}`} data-testid="dominancia-muerta">
              dominancia: sin dato (fuente caída)</span>
          : <span className={styles.comp}>dominancia BTC: {pct(dom?.valor ?? null)}</span>}
      </div>
      <div className={styles.frase}>
        Lo que más mueve el resultado es el régimen del mercado, no la moneda que elijas.
      </div>
      {frescura && (
        <div className={styles.frescura}>foto del régimen: {frescura.estado}</div>
      )}
    </div>
  );
};
```

- [ ] **Step 4: Correr para verque pasa**

Run: `cd frontend && npx vitest run src/components/valles/AltSeasonHeader.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/valles/AltSeasonHeader.tsx frontend/src/components/valles/AltSeasonHeader.module.css frontend/src/components/valles/AltSeasonHeader.test.tsx
git commit -m "feat(frontend): AltSeasonHeader — cabecera de régimen con componentes + frase honesta"
```

---

## Task 10: Frontend — montar la cabecera en `ValleysFlow` + e2e

**Files:**
- Modify: `frontend/src/components/valles/ValleysFlow.tsx`
- Create: `frontend/e2e/alt-season.spec.ts`

- [ ] **Step 1: Montar la cabecera sobre `vwStage`**

En `frontend/src/components/valles/ValleysFlow.tsx`, importar el componente:
```tsx
import { AltSeasonHeader } from './AltSeasonHeader';
```
y renderizarlo justo antes de `<div className={styles.vwStage}>` (dentro de `<div className={styles.vw}>`, después de `vwTop`):
```tsx
        <AltSeasonHeader />

        <div className={styles.vwStage}>
```

- [ ] **Step 2: Escribir el e2e (Playwright)**

`frontend/e2e/alt-season.spec.ts` (sigue el harness existente; backend aislado en :8001 con auth-bypass, frontend en :5174 — ver `e2e-playwright-harness`):
```ts
import { test, expect } from '@playwright/test';

test('la cabecera de régimen aparece y NO modula la lista de coins', async ({ page }) => {
  await page.addInitScript(() => localStorage.removeItem('vw_sym'));  // ver la lista, no una idea
  await page.goto('/');
  // La cabecera de régimen está presente con su frase honesta.
  await expect(page.getByText(/régimen del mercado/i)).toBeVisible();
  await expect(page.getByTestId('regime-estado')).toBeVisible();
  // Doctrina: el estado del régimen NO añade clases de color/énfasis a las tarjetas de coins.
  const cards = page.locator('[data-testid="pick-card"]');
  if (await cards.count() > 0) {
    const cls = await cards.first().getAttribute('class');
    expect(cls ?? '').not.toMatch(/alts|btc|regime/i);
  }
});
```
> Nota: si las tarjetas del PickScreen no exponen `data-testid="pick-card"`, el assert de no-modulación se vuelve no-op (count 0) — está bien para v1; el assert positivo (la cabecera aparece) es el que importa. Documentar en el PR.

- [ ] **Step 3: Verificar build + unit del frontend (gate de CI)**

Run: `cd frontend && npx vitest run && npx tsc --noEmit`
Expected: PASS (vitest no recoge `e2e/` por el `include` de `vitest.config.ts`).

- [ ] **Step 4: Correr el e2e contra el stack aislado (manual, no en el gate de CI)**

Run (en dos terminales, ver `e2e-playwright-harness`):
```bash
AUTH_JWT_SECRET=test-secret-test-secret-test-secret AUTH_TEST_BYPASS_ALLOWED=1 AUTH_TEST_BYPASS_ROLE=admin python scripts/e2e_backend.py
cd frontend && VITE_DEV_PORT=5174 VITE_API_PROXY_TARGET=http://localhost:8001 npm run dev
cd frontend && npx playwright test e2e/alt-season.spec.ts
```
Expected: la cabecera de régimen visible. (Necesita `data/alt_season.json`; si no existe en el stack de pruebas, la cabecera muestra "Cargando…/no disponible" — aceptable; el assert de la frase honesta sólo pasa con foto. Si hace falta, generar la foto corriendo `python -m tools.run_valley_screener` una vez, o mockear `/alt-season` en el test.)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/valles/ValleysFlow.tsx frontend/e2e/alt-season.spec.ts
git commit -m "feat(frontend): montar AltSeasonHeader en Valles + e2e de cabecera"
```

---

## Task 11: Sync de docs/scaffold + gate final

**Files:**
- Modify: `.mex/patterns/correr-screener-valles.md`
- Modify: `docs/superpowers/specs/es/2026-06-18-musikito-firma-estadistica-evidencia.md` (puntero)

- [ ] **Step 1: Actualizar el pattern del screener**

En `.mex/patterns/correr-screener-valles.md`, añadir una línea en la sección de outputs:
```markdown
- La misma pasada también escribe `data/alt_season.json` (régimen de mercado) y hace 1 llamada a CoinGecko `/global` para la dominancia (degradación elegante si falla). Lector: `GET /alt-season`.
```
> Si el archivo del pattern no existe (verificar con Glob antes), omitir este paso y anotarlo en el PR.

- [ ] **Step 2: Puntero desde el doc de evidencia**

En `docs/superpowers/specs/es/2026-06-18-musikito-firma-estadistica-evidencia.md`, al final de la sección §3 P2, añadir:
```markdown
> **Resolución de P2 (2026-06-18):** el subproyecto 1 (pieza de régimen "¿es alt-season?")
> implementa el eje de régimen. Ver `docs/superpowers/specs/es/2026-06-18-alt-season-regimen-design.md`.
```

- [ ] **Step 3: Correr el gate completo del repo**

Run:
```bash
python -m pytest tests/ -m "not network" -n auto -q
cd frontend && npx vitest run && npx tsc --noEmit
```
Expected: TODO verde. Ningún test nuevo lleva el marker `network` (la dominancia y las klines se mockean).

- [ ] **Step 4: `mex log` + commit**

```bash
mex log "feat: pieza de régimen alt-season (subproyecto 1 reorientación Valles) — /alt-season vivo con LiveSnapshot"
git add .mex docs/superpowers/specs/es/2026-06-18-musikito-firma-estadistica-evidencia.md
git commit -m "docs(valles): sync pattern del screener + puntero de evidencia a la pieza de régimen"
```

---

## Notas de ejecución

- **Doctrina (verificar en cada task de payload):** sin campo per-símbolo, sin lenguaje de
  veredicto/valencia. El test `test_payload_sin_lenguaje_de_veredicto_ni_per_simbolo` (Task 5) es
  el candado automático.
- **#8:** la pieza reusa el owner `screener_loop` (no thread nuevo); la fila del inventario (Task 7)
  es obligatoria antes de mergear.
- **CI:** todo corre bajo `-m "not network"`; la red (klines + CoinGecko) se mockea en todos los
  tests nuevos. El job de frontend corre vitest (que NO recoge `e2e/`).
- **Fuera de alcance (no implementar):** historia de dominancia / tendencia (v1.1), `breadth200`,
  calibración de umbrales (POST-SHIP), reframe del detector per-coin (subproyecto 2).
