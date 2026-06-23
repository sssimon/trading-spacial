# Gate de exposición por régimen (alt-season) — Plan de Implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hacer que el régimen alt-season module la exposición a alts (esconde en mal clima) en el screener de Valles y el scanner, vía una política pura compartida, con fail-open sobre clima rancio y calibración sobre la marcha.

**Architecture:** Una política pura (`regime/exposure_gate.py`) decide `pasa|atenua|suprime` desde el estado del régimen + su frescura. Un lector compartido (`regime/alt_season_read.py`) extrae el snapshot de `data/alt_season.json` y computa frescura (resuelve que el scanner hoy no tiene reader). Dos orquestadores (screener `build_snapshot`, scanner `scan`) consultan la política y, solo si `cfg.regime_gate.enabled`, esconden + auditan. Flag default off = byte-idéntico.

**Tech Stack:** Python 3 (stdlib: dataclasses, hashlib, json, datetime), SQLite (vía `db/transaction.py`), pytest. Frontend: React/TypeScript (Valles). Sin dependencias nuevas.

**Spec:** `docs/superpowers/specs/es/2026-06-23-regimen-al-trade-gate-design.md` (commit `fa72c66`).

## Global Constraints

(Aplican a TODAS las tareas — requisitos del proyecto, valores verbatim del spec / no-negociables.)

- **#4 — `RISK_PER_TRADE = 0.01` es fijo.** El gate es filtro de selección/visibilidad. NO toca `RISK_PER_TRADE`, `size_mult`, ni ningún cómputo de sizing. Cero multiplicadores de riesgo.
- **#8 — Freshness owner + LiveSnapshot.** El lector del scanner computa frescura vía `freshness.LiveSnapshot`; nunca esconde sobre clima `rancio`/`muerto` (fail-open). La frescura se COMPUTA en el orquestador, NO se lee de un campo del snapshot.
- **#6 — Specs autoritativas mandan.** La enmienda a `2026-06-18-alt-season-regimen-design.md` (Task 8) es bloqueante del merge.
- **Byte-idéntico con `cfg.regime_gate.enabled=false`:** sin campos nuevos en `valley_candidates.json`, sin filas de auditoría, señal del scanner idéntica. Cada task que toca un orquestador prueba esto.
- **No se toca `strategy/regime.py`** (régimen macro-BTC, eje distinto). No se toca `RISK_PER_TRADE`, `PositionClosure`, ni el holdout.
- **Copy en español venezolano** (tuteo) en strings visibles al operador.
- **Gate de tests del repo (CI Backend):** `python -m pytest tests/ -m "not network" -n auto -q`. El gate corre en el path rápido — no requiere `ohlcv.db` ni red.

---

## File Structure

| Archivo | Responsabilidad | Task |
|---|---|---|
| `regime/alt_season.py` (mod) | `effective_thresholds(overrides)` + `compose_regime` acepta umbrales (default = constantes) | 1 |
| `regime/exposure_gate.py` (new) | `GateDecision`, `evaluar_gate(...)`, `umbral_version(cfg)` — política pura | 2 |
| `config.defaults.json` (mod) | bloque `regime_gate` | 2 |
| `regime/alt_season_read.py` (new) | `leer_regimen(umbral_seg) -> RegimenVivo` — lector compartido del snapshot | 3 |
| `api/alt_season.py` (mod) | usar el lector compartido (DRY) | 3 |
| `db/schema.py` (mod) + `db/regime_gate_audit.py` (new) | tabla `regime_gate_audit` + insert batch | 4 |
| `tools/run_valley_screener.py` (mod) | hook screener + atomic write de `valley_candidates.json` + pasar overrides a compose_regime | 5 |
| `btc_scanner.py` (mod) | hook scanner (leer régimen 1×/ciclo, suprimir señal alt, auditar) | 6 |
| `frontend/src/components/valles/*` (mod) | válvula "ver ocultas" | 7 |
| `docs/.../2026-06-18-alt-season-regimen-design.md` (mod) | enmienda de doctrina | 8 |

**Orden por dependencias:** 1 → 2 → 3 → 4 (fundacionales) → 5, 6 (hooks) → 7 (frontend) → 8 (doc).

---

## Task 1: `effective_thresholds` + `compose_regime` parametrizable

**Por qué primero:** los `umbral_overrides` (calibración sin deploy) exigen que el estado del régimen se compute con los umbrales efectivos. Hoy `compose_regime` usa constantes de módulo. Lo hacemos parametrizable SIN cambiar el comportamiento por defecto (overrides vacío → byte-idéntico), y exponemos un helper que tanto el screener como `umbral_version` comparten (DRY).

**Files:**
- Modify: `regime/alt_season.py`
- Test: `tests/test_alt_season.py` (crear si no existe; si existe, añadir)

**Interfaces:**
- Produces: `effective_thresholds(overrides: dict | None) -> dict` con claves exactas `{"BREADTH_ALT","BREADTH_BEAR","OUTPERF_ALT","OUTPERF_BEAR","DOM_ALT","DOM_BTC","COVERAGE_MIN","MIN_LIVE_VOTERS"}`. Y `compose_regime(alt_contribs, btc_ret_30d, btc_dominance, coverage_ratio, thresholds: dict | None = None)` — cuando `thresholds is None` usa las constantes de módulo (comportamiento de hoy).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_alt_season.py
from regime.alt_season import effective_thresholds, compose_regime

def test_effective_thresholds_defaults_match_constants():
    import regime.alt_season as m
    t = effective_thresholds(None)
    assert t["BREADTH_ALT"] == m.BREADTH_ALT
    assert t["OUTPERF_ALT"] == m.OUTPERF_ALT
    assert t["DOM_BTC"] == m.DOM_BTC
    assert t["COVERAGE_MIN"] == m.COVERAGE_MIN
    assert t["MIN_LIVE_VOTERS"] == m.MIN_LIVE_VOTERS

def test_effective_thresholds_overrides_win():
    t = effective_thresholds({"BREADTH_ALT": 0.7})
    assert t["BREADTH_ALT"] == 0.7
    import regime.alt_season as m
    assert t["OUTPERF_ALT"] == m.OUTPERF_ALT  # los no-pisados quedan

def test_compose_regime_thresholds_none_is_default():
    # Una pasada donde breadth=1.0 (todos sobre SMA50) y BTC ret bajo → 'alts'.
    contribs = [{"above_sma50": True, "ret_30d": 0.20} for _ in range(5)]
    out = compose_regime(contribs, btc_ret_30d=0.0, btc_dominance=0.45,
                         coverage_ratio=1.0, thresholds=None)
    assert out["estado"] == "alts"

def test_compose_regime_override_flips_state():
    contribs = [{"above_sma50": True, "ret_30d": 0.01} for _ in range(5)]
    # breadth=1.0; con BREADTH_ALT=0.6 por defecto vota 'alts'. Subiendo a 1.1
    # (imposible de alcanzar) el voto de breadth deja de ser 'alts'.
    out = compose_regime(contribs, btc_ret_30d=0.0, btc_dominance=0.55,
                         coverage_ratio=1.0, thresholds=effective_thresholds({"BREADTH_ALT": 1.1}))
    assert out["estado"] != "alts"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_alt_season.py -v`
Expected: FAIL con `ImportError: cannot import name 'effective_thresholds'`.

- [ ] **Step 3: Implement**

En `regime/alt_season.py`, tras las constantes (línea ~29) añade:

```python
def effective_thresholds(overrides: dict | None) -> dict:
    """Umbrales efectivos: constantes de módulo pisadas por `overrides` (calibración
    sin deploy). Claves fijas — única fuente para compose_regime y umbral_version."""
    base = {
        "BREADTH_ALT": BREADTH_ALT, "BREADTH_BEAR": BREADTH_BEAR,
        "OUTPERF_ALT": OUTPERF_ALT, "OUTPERF_BEAR": OUTPERF_BEAR,
        "DOM_ALT": DOM_ALT, "DOM_BTC": DOM_BTC,
        "COVERAGE_MIN": COVERAGE_MIN, "MIN_LIVE_VOTERS": MIN_LIVE_VOTERS,
    }
    if overrides:
        for k, v in overrides.items():
            if k in base:
                base[k] = v
    return base
```

Y cambia la firma de `compose_regime` (línea 62) para aceptar `thresholds`:

```python
def compose_regime(alt_contribs: list[dict], btc_ret_30d: float | None,
                   btc_dominance: float | None, coverage_ratio: float,
                   thresholds: dict | None = None) -> dict:
    """... (docstring existente). Si thresholds is None usa effective_thresholds(None)."""
    t = thresholds if thresholds is not None else effective_thresholds(None)
```

Dentro de `compose_regime`, reemplaza cada constante de módulo por su entrada en `t`:
- `_lean_higher_alt(breadth50, BREADTH_ALT, BREADTH_BEAR)` → `_lean_higher_alt(breadth50, t["BREADTH_ALT"], t["BREADTH_BEAR"])`
- `coverage_ratio >= COVERAGE_MIN` → `coverage_ratio >= t["COVERAGE_MIN"]`
- `_lean_higher_alt(outperf, OUTPERF_ALT, OUTPERF_BEAR)` → usar `t[...]`
- `_lean_lower_alt(btc_dominance, DOM_ALT, DOM_BTC)` → usar `t[...]`
- `if n_live < MIN_LIVE_VOTERS:` → `if n_live < t["MIN_LIVE_VOTERS"]:`

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_alt_season.py -v`
Expected: PASS (5 tests). Además: `python -m pytest tests/ -m "not network" -n auto -q -k "alt_season or screener"` no rompe nada (compose_regime con `thresholds=None` es idéntico).

- [ ] **Step 5: Commit**

```bash
git add regime/alt_season.py tests/test_alt_season.py
git commit -m "feat(regime): compose_regime parametrizable + effective_thresholds (overrides sin deploy)"
```

---

## Task 2: Política pura `exposure_gate.py` + bloque de config

**Files:**
- Create: `regime/exposure_gate.py`
- Modify: `config.defaults.json`
- Test: `tests/test_exposure_gate.py`

**Interfaces:**
- Consumes: `regime.alt_season.effective_thresholds` (Task 1).
- Produces:
  - `@dataclass(frozen=True) GateDecision` con campos `nivel: str, estado_regimen: str, es_alt: bool, regime_frescura: str, votos_vivos: int, razon: str, enforced: bool, umbral_version: str`.
  - `evaluar_gate(estado: str, frescura: str, votos_vivos: int, es_alt: bool, cfg: dict) -> GateDecision`.
  - `umbral_version(cfg: dict) -> str` (sha1 corto de los umbrales efectivos).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_exposure_gate.py
from regime.exposure_gate import evaluar_gate, umbral_version, GateDecision

_ON = {"regime_gate": {"enabled": True, "umbral_overrides": {}}}
_OFF = {"regime_gate": {"enabled": False, "umbral_overrides": {}}}

def _gate(estado, frescura, votos, es_alt, cfg=_ON):
    return evaluar_gate(estado, frescura, votos, es_alt, cfg)

def test_btc_fresco_alt_enabled_suprime():
    assert _gate("btc", "fresco", 3, True).nivel == "suprime"

def test_btc_rancio_pasa_failopen():
    d = _gate("btc", "rancio", 3, True)
    assert d.nivel == "pasa" and d.enforced is False

def test_btc_muerto_pasa_failopen():
    assert _gate("btc", "muerto", 3, True).nivel == "pasa"

def test_btc_disabled_pasa():
    d = _gate("btc", "fresco", 3, True, cfg=_OFF)
    assert d.nivel == "pasa" and d.enforced is False

def test_btc_no_alt_pasa():
    assert _gate("btc", "fresco", 3, False).nivel == "pasa"  # BTC nunca se gatea

def test_alts_pasa():
    assert _gate("alts", "fresco", 3, True).nivel == "pasa"

def test_mixto_empate_atenua():
    assert _gate("mixto", "fresco", 3, True).nivel == "atenua"  # votos>=2 = empate genuino

def test_mixto_datos_degradados_pasa():
    assert _gate("mixto", "fresco", 1, True).nivel == "pasa"   # votos<2 = ausencia de señal

def test_estado_inesperado_pasa():
    assert _gate("ZZZ", "fresco", 3, True).nivel == "pasa"

def test_decision_carries_context():
    d = _gate("btc", "fresco", 3, True)
    assert d.estado_regimen == "btc" and d.es_alt is True and d.regime_frescura == "fresco"
    assert isinstance(d.umbral_version, str) and len(d.umbral_version) >= 6

def test_umbral_version_changes_with_overrides():
    base = umbral_version({"regime_gate": {"umbral_overrides": {}}})
    moved = umbral_version({"regime_gate": {"umbral_overrides": {"BREADTH_ALT": 0.7}}})
    assert base != moved
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_exposure_gate.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'regime.exposure_gate'`.

- [ ] **Step 3: Implement**

```python
# regime/exposure_gate.py
"""Gate de exposición por régimen — política PURA (sin red, sin DB). Espejo
estructural de regime/alt_season.py: solo funciones puras, cero I/O.

Decide pasa|atenua|suprime desde el ESTADO del régimen + su FRESCURA. Un hecho
de exposición de mercado, NO un veredicto per-coin. Spec 2026-06-23."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from regime.alt_season import effective_thresholds


@dataclass(frozen=True)
class GateDecision:
    nivel: str            # "pasa" | "atenua" | "suprime"
    estado_regimen: str   # "alts" | "mixto" | "btc"
    es_alt: bool
    regime_frescura: str  # "fresco" | "rancio" | "muerto" (COMPUTADA por el orquestador)
    votos_vivos: int
    razon: str
    enforced: bool        # = cfg.regime_gate.enabled AND regime_frescura == "fresco"
    umbral_version: str


def umbral_version(cfg: dict) -> str:
    """Sello determinista de los umbrales EFECTIVOS (6 de lean + 2 de gobierno de
    evidencia + overrides). Ata cada fila de auditoría a la calibración exacta."""
    overrides = (cfg.get("regime_gate") or {}).get("umbral_overrides") or {}
    eff = effective_thresholds(overrides)
    blob = json.dumps(eff, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def evaluar_gate(estado: str, frescura: str, votos_vivos: int,
                 es_alt: bool, cfg: dict) -> GateDecision:
    """Política graduada. Fail-open sobre clima rancio/muerto y sobre flag off."""
    rg = cfg.get("regime_gate") or {}
    enforced = bool(rg.get("enabled", False)) and frescura == "fresco"
    eff = effective_thresholds(rg.get("umbral_overrides") or {})
    min_live = eff["MIN_LIVE_VOTERS"]

    if not enforced:
        nivel, razon = "pasa", "gate inactivo (flag off o régimen no fresco)"
    elif not es_alt:
        nivel, razon = "pasa", "símbolo no-alt — el gate es sobre exposición a alts"
    elif estado == "alts":
        nivel, razon = "pasa", "régimen 'alts' — el viento acompaña"
    elif estado == "btc":
        nivel, razon = "suprime", "régimen 'btc' — el viento no acompaña a las alts"
    elif estado == "mixto":
        if votos_vivos < min_live:
            nivel, razon = "pasa", "mixto por datos degradados — ausencia de señal, no clima"
        else:
            nivel, razon = "atenua", "régimen 'mixto' — clima ambiguo"
    else:
        nivel, razon = "pasa", f"estado inesperado '{estado}' — fail-open"

    return GateDecision(
        nivel=nivel, estado_regimen=estado, es_alt=es_alt,
        regime_frescura=frescura, votos_vivos=votos_vivos, razon=razon,
        enforced=enforced, umbral_version=umbral_version(cfg),
    )
```

En `config.defaults.json`, tras el bloque `regime_allocation` (línea 56), añade:

```json
  "regime_gate": {
    "enabled": false,
    "frescura_umbral_seg": 27000,
    "umbral_overrides": {}
  },
```

(coma al final; va antes de `"symbol_overrides"`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_exposure_gate.py -v`
Expected: PASS (11 tests).
Run: `python -c "import json; json.load(open('config.defaults.json'))"` → sin error (JSON válido).

- [ ] **Step 5: Commit**

```bash
git add regime/exposure_gate.py config.defaults.json tests/test_exposure_gate.py
git commit -m "feat(regime): política pura del gate de exposición + bloque cfg.regime_gate"
```

---

## Task 3: Lector compartido `alt_season_read.py` + refactor de la API

**Por qué:** el scanner NO tiene hoy cómo leer el régimen alt-season (el único reader vive en `api/alt_season.py`, dentro de un request HTTP). Esta task crea el lector reusable y refactoriza la API para usarlo (DRY: un solo guard de ausencia/corrupción).

**Files:**
- Create: `regime/alt_season_read.py`
- Modify: `api/alt_season.py`
- Test: `tests/test_alt_season_read.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) RegimenVivo` con `estado: str, frescura: str, votos_vivos: int, generated_at: str | None, snapshot: dict`.
  - `leer_regimen(umbral_seg: float, ruta: str | None = None) -> RegimenVivo`.
- Consumes: `freshness.LiveSnapshot`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_alt_season_read.py
import json
from datetime import datetime, timezone, timedelta
from regime.alt_season_read import leer_regimen, RegimenVivo

def _write(tmp_path, generated_at, estado="btc", vivos=3):
    p = tmp_path / "alt_season.json"
    p.write_text(json.dumps({
        "generated_at": generated_at,
        "regime": {"estado": estado, "votos": {"vivos": vivos}},
    }), encoding="utf-8")
    return str(p)

def test_ausente_es_muerto(tmp_path):
    r = leer_regimen(27000, ruta=str(tmp_path / "no_existe.json"))
    assert r.frescura == "muerto"

def test_corrupto_es_muerto(tmp_path):
    p = tmp_path / "alt_season.json"; p.write_text("{ not json", encoding="utf-8")
    r = leer_regimen(27000, ruta=str(p))
    assert r.frescura == "muerto"

def test_generated_at_viejo_es_rancio(tmp_path):
    viejo = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
    r = leer_regimen(27000, ruta=_write(tmp_path, viejo))  # 27000s = 7.5h < 10h
    assert r.frescura == "rancio"

def test_generated_at_reciente_es_fresco(tmp_path):
    reciente = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    r = leer_regimen(27000, ruta=_write(tmp_path, reciente, estado="alts", vivos=2))
    assert r.frescura == "fresco" and r.estado == "alts" and r.votos_vivos == 2

def test_failopen_combinado_con_gate(tmp_path):
    # El test que ATRAPA el fail-open silencioso: clima 'btc' viejo → 'rancio' →
    # el gate NO debe esconder (enforced=False).
    from regime.exposure_gate import evaluar_gate
    viejo = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
    r = leer_regimen(27000, ruta=_write(tmp_path, viejo, estado="btc"))
    d = evaluar_gate(r.estado, r.frescura, r.votos_vivos, True,
                     {"regime_gate": {"enabled": True, "umbral_overrides": {}}})
    assert d.enforced is False and d.nivel == "pasa"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_alt_season_read.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'regime.alt_season_read'`.

- [ ] **Step 3: Implement**

```python
# regime/alt_season_read.py
"""Lector compartido del snapshot de régimen alt-season. ÚNICO path de lectura
de data/alt_season.json (lo usan la API y el hook del scanner). Computa la
frescura vía freshness.LiveSnapshot — la frescura NO está persistida en el JSON.
Maneja archivo ausente / corrupto → 'muerto' (fail-open). Spec 2026-06-23 §1."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

from freshness import LiveSnapshot

log = logging.getLogger("regime.alt_season_read")

_DEFAULT_RUTA = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                             "data", "alt_season.json")

_EMPTY = {
    "generated_at": None,
    "coverage": {"universe": 0, "evaluated": 0, "complete": False},
    "dominancia_fetch": {"ok": False, "fetched_at": None, "source": "coingecko/global"},
    "regime": {"estado": "mixto", "componentes": {},
               "votos": {"alts": 0, "neutral": 0, "btc": 0, "vivos": 0},
               "n_alts_evaluadas": 0},
}


@dataclass(frozen=True)
class RegimenVivo:
    estado: str
    frescura: str          # "fresco" | "rancio" | "muerto"
    votos_vivos: int
    generated_at: str | None
    snapshot: dict


def _leer_snapshot(ruta: str) -> dict:
    """Lee el JSON con guard de ausencia/corrupción → _EMPTY (frescura derivará 'muerto')."""
    if not os.path.exists(ruta):
        return dict(_EMPTY)
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.error("ALT_SEASON_SNAPSHOT_UNREADABLE causa=%s", e)
        return dict(_EMPTY)


def leer_regimen(umbral_seg: float, ruta: str | None = None) -> RegimenVivo:
    """Estado + frescura del régimen. La frescura se COMPUTA (generated_at vs
    umbral_seg), no se lee de un campo. umbral_seg lo pasa el llamador (la API usa
    el de la UI; el gate del scanner usa el suyo, más estricto)."""
    snap = _leer_snapshot(ruta or _DEFAULT_RUTA)
    generated_at = snap.get("generated_at")
    frescura = LiveSnapshot(payload={}, generated_at=generated_at, umbral_seg=umbral_seg).estado
    regime = snap.get("regime") or {}
    estado = regime.get("estado", "mixto")
    votos_vivos = int((regime.get("votos") or {}).get("vivos", 0))
    return RegimenVivo(estado=estado, frescura=frescura, votos_vivos=votos_vivos,
                       generated_at=generated_at, snapshot=snap)
```

Refactoriza `api/alt_season.py` para usar el lector (DRY). Reemplaza el cuerpo de `get_alt_season` (líneas 41-51) por:

```python
    from regime.alt_season_read import leer_regimen
    rv = leer_regimen(FRESCURA_VALLES_SEG)
    # El snapshot ya trae generated_at; LiveSnapshot.to_response re-inyecta la frescura
    # con el MISMO umbral, preservando el contrato actual de /alt-season.
    return LiveSnapshot(payload=rv.snapshot, generated_at=rv.generated_at,
                        umbral_seg=FRESCURA_VALLES_SEG).to_response()
```

(El `_EMPTY` y el guard quedan dentro de `alt_season_read`; `api/alt_season.py` puede borrar su `_EMPTY` local y el `os.path.exists`/`json.load` — quedan en el lector.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_alt_season_read.py tests/test_api.py -v -k "alt_season or read"`
Expected: PASS. El contrato de `GET /alt-season` (frescura en el payload, ausente→muerto) se preserva.

- [ ] **Step 5: Commit**

```bash
git add regime/alt_season_read.py api/alt_season.py tests/test_alt_season_read.py
git commit -m "feat(regime): lector compartido del snapshot alt-season + refactor DRY de /alt-season"
```

---

## Task 4: Tabla `regime_gate_audit` + insert batch

**Files:**
- Modify: `db/schema.py` (añadir `CREATE TABLE IF NOT EXISTS` en `init_db`)
- Create: `db/regime_gate_audit.py`
- Test: `tests/test_regime_gate_audit.py`

**Interfaces:**
- Produces: `registrar_decisiones(filas: list[dict]) -> int` (inserta TODAS las filas en UNA transacción; devuelve cuántas). Cada fila: `{motor, symbol, estado_regimen, nivel, es_alt, regime_frescura, votos_vivos, enforced, umbral_version, tenant_id}` (`tenant_id` puede ser `None`). Y `purgar_antiguos(dias: int) -> int`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_regime_gate_audit.py
from db.regime_gate_audit import registrar_decisiones, _query_all  # _query_all: helper de test
from db.schema import init_db

def _fila(**kw):
    base = dict(motor="valles", symbol="ADAUSDT", estado_regimen="btc",
                nivel="suprime", es_alt=True, regime_frescura="fresco",
                votos_vivos=3, enforced=True, umbral_version="abc123def456",
                tenant_id=None)
    base.update(kw); return base

def test_registra_batch(tmp_db):  # tmp_db: fixture que apunta la DB a tmp + init_db
    n = registrar_decisiones([_fila(), _fila(symbol="DOGEUSDT")])
    assert n == 2
    rows = _query_all()
    assert len(rows) == 2
    assert rows[0]["tenant_id"] is None      # universo global
    assert rows[0]["umbral_version"] == "abc123def456"
    assert rows[0]["enforced"] == 1          # bool → int

def test_batch_vacio_no_escribe(tmp_db):
    assert registrar_decisiones([]) == 0
    assert _query_all() == []
```

(Si no existe una fixture `tmp_db`, añade en `tests/conftest.py` una que apunte `db.connection` a un sqlite temporal y llame `init_db()`. Mira cómo `tests/test_api.py` aísla la DB — reusa ese patrón.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_regime_gate_audit.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'db.regime_gate_audit'`.

- [ ] **Step 3: Implement**

En `db/schema.py`, dentro de `init_db()` (junto a los otros `CREATE TABLE IF NOT EXISTS`, p.ej. tras `kill_switch_decisions` ~línea 292), añade:

```python
        con.execute("""
            CREATE TABLE IF NOT EXISTS regime_gate_audit (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                ts              TEXT NOT NULL,
                motor           TEXT NOT NULL,          -- 'valles' | 'scanner'
                symbol          TEXT NOT NULL,
                estado_regimen  TEXT NOT NULL,          -- 'alts' | 'mixto' | 'btc'
                nivel           TEXT NOT NULL,          -- 'pasa' | 'atenua' | 'suprime'
                es_alt          INTEGER NOT NULL,
                regime_frescura TEXT NOT NULL,          -- 'fresco' | 'rancio' | 'muerto'
                votos_vivos     INTEGER NOT NULL,
                enforced        INTEGER NOT NULL,
                umbral_version  TEXT NOT NULL,
                tenant_id       INTEGER                 -- NULLABLE: decisión de mercado global
            )
        """)
        con.execute("""
            CREATE INDEX IF NOT EXISTS idx_regime_gate_audit_ts
                ON regime_gate_audit(ts)
        """)
        con.execute("""
            CREATE INDEX IF NOT EXISTS idx_regime_gate_audit_motor_ts
                ON regime_gate_audit(motor, ts)
        """)
```

```python
# db/regime_gate_audit.py
"""Auditoría append-only del gate de exposición — feed de calibración + rastro de
honestidad. Escritura en BATCH (una transacción por ciclo, NO N BEGIN IMMEDIATE)
para no ensanchar el burst de writes que ya causó contención de locks (2026-05-29).
tenant_id NULLABLE: la decisión es un hecho de mercado global. Spec §5."""
from __future__ import annotations

from datetime import datetime, timezone

from db.transaction import transaction

_COLS = ("motor", "symbol", "estado_regimen", "nivel", "es_alt",
         "regime_frescura", "votos_vivos", "enforced", "umbral_version", "tenant_id")


def registrar_decisiones(filas: list[dict]) -> int:
    """Inserta TODAS las filas del ciclo en UNA sola transacción. No-op si vacío."""
    if not filas:
        return 0
    ts = datetime.now(timezone.utc).isoformat()
    params = [
        (ts, f["motor"], f["symbol"], f["estado_regimen"], f["nivel"],
         int(bool(f["es_alt"])), f["regime_frescura"], int(f["votos_vivos"]),
         int(bool(f["enforced"])), f["umbral_version"], f.get("tenant_id"))
        for f in filas
    ]
    with transaction() as con:
        con.executemany(
            """INSERT INTO regime_gate_audit
               (ts, motor, symbol, estado_regimen, nivel, es_alt,
                regime_frescura, votos_vivos, enforced, umbral_version, tenant_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            params,
        )
    return len(params)


def purgar_antiguos(dias: int) -> int:
    """Retención: borra filas con más de `dias` días. Devuelve cuántas borró."""
    corte = (datetime.now(timezone.utc) - __import__("datetime").timedelta(days=dias)).isoformat()
    with transaction() as con:
        cur = con.execute("DELETE FROM regime_gate_audit WHERE ts < ?", (corte,))
        return cur.rowcount


def _query_all() -> list[dict]:
    """Helper de test: todas las filas como dicts."""
    with transaction() as con:
        rows = con.execute(
            "SELECT ts, " + ", ".join(_COLS) + " FROM regime_gate_audit ORDER BY id"
        ).fetchall()
    return [dict(zip(("ts", *_COLS), r)) for r in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_regime_gate_audit.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add db/schema.py db/regime_gate_audit.py tests/test_regime_gate_audit.py
git commit -m "feat(db): tabla regime_gate_audit (append-only, batch, tenant global NULL)"
```

---

## Task 5: Hook en el screener de Valles

**Files:**
- Modify: `tools/run_valley_screener.py` (`build_snapshot` + `regenerate`)
- Test: `tests/test_run_valley_screener.py` (crear/extender)

**Interfaces:**
- Consumes: `evaluar_gate`, `GateDecision`, `umbral_version` (Task 2); `effective_thresholds` (Task 1); `registrar_decisiones` (Task 4); `api.config.load_config`.
- Produces: `cand_snap` con campos nuevos SOLO si `enabled`: `candidates[i]["clima_ambiguo"]` (bool) y top-level `cand_snap["candidatas_ocultas"]` (list[dict]).

**Notas de diseño resueltas:**
- El régimen está EN MANO en `build_snapshot` (línea 141) → `frescura="fresco"` trivial (edad ~0). No necesita el lector.
- `compose_regime` recibe los overrides efectivos (Task 1) para que la calibración sin deploy afecte el estado.
- `valley_candidates.json` migra a `_atomic_write_json` (hoy `regenerate` línea 157-158 es no-atómico).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_run_valley_screener.py
from tools import run_valley_screener as rvs

def _candidatas():  # 2 candidatas alt de juguete
    return [{"symbol": "ADAUSDT", "price": 1.0}, {"symbol": "DOGEUSDT", "price": 0.1}]

def test_disabled_byte_identico(monkeypatch):
    monkeypatch.setattr(rvs, "load_config", lambda: {"regime_gate": {"enabled": False}})
    snap = rvs.aplicar_gate_candidatas(_candidatas(), estado="btc", votos_vivos=3)
    assert snap["candidates"] == _candidatas()      # sin tocar
    assert "candidatas_ocultas" not in snap          # sin campos nuevos

def test_enabled_btc_esconde(monkeypatch):
    monkeypatch.setattr(rvs, "load_config",
                        lambda: {"regime_gate": {"enabled": True, "umbral_overrides": {}}})
    snap = rvs.aplicar_gate_candidatas(_candidatas(), estado="btc", votos_vivos=3)
    assert snap["candidates"] == []                  # todas escondidas
    assert len(snap["candidatas_ocultas"]) == 2

def test_enabled_mixto_empate_atenua(monkeypatch):
    monkeypatch.setattr(rvs, "load_config",
                        lambda: {"regime_gate": {"enabled": True, "umbral_overrides": {}}})
    snap = rvs.aplicar_gate_candidatas(_candidatas(), estado="mixto", votos_vivos=3)
    assert len(snap["candidates"]) == 2 and all(c["clima_ambiguo"] for c in snap["candidates"])
    assert snap.get("candidatas_ocultas", []) == []
```

(`aplicar_gate_candidatas` es una función pura nueva, fácil de testear sin red. La integración con `build_snapshot` se prueba con el test de byte-identidad de abajo.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_run_valley_screener.py -v`
Expected: FAIL con `AttributeError: module ... has no attribute 'aplicar_gate_candidatas'`.

- [ ] **Step 3: Implement**

En `tools/run_valley_screener.py`, añade el import y la función pura:

```python
from api.config import load_config
from regime.exposure_gate import evaluar_gate

def aplicar_gate_candidatas(candidatas: list[dict], *, estado: str, votos_vivos: int) -> dict:
    """Aplica el gate (motor 'valles') a las candidatas. Devuelve un dict con
    'candidates' (las que pasan/atenúan) y, SOLO si enabled, 'candidatas_ocultas'.
    Frescura='fresco' trivial: el régimen se computó en esta misma pasada."""
    cfg = load_config()
    if not (cfg.get("regime_gate") or {}).get("enabled", False):
        return {"candidates": candidatas}            # byte-idéntico: sin campos nuevos
    visibles: list[dict] = []
    ocultas: list[dict] = []
    filas: list[dict] = []
    for c in candidatas:
        d = evaluar_gate(estado, "fresco", votos_vivos, es_alt=True, cfg=cfg)
        filas.append({"motor": "valles", "symbol": c["symbol"], "estado_regimen": d.estado_regimen,
                      "nivel": d.nivel, "es_alt": True, "regime_frescura": d.regime_frescura,
                      "votos_vivos": d.votos_vivos, "enforced": d.enforced,
                      "umbral_version": d.umbral_version, "tenant_id": None})
        if d.nivel == "suprime":
            ocultas.append({**c, "clima": d.razon})
        elif d.nivel == "atenua":
            visibles.append({**c, "clima_ambiguo": True})
        else:
            visibles.append(c)
    from db.regime_gate_audit import registrar_decisiones
    try:
        registrar_decisiones(filas)
    except Exception:
        log.warning("regime_gate_audit (valles) falló — fail-open", exc_info=True)
    return {"candidates": visibles, "candidatas_ocultas": ocultas}
```

En `build_snapshot`, tras computar `regime` (línea 141) y antes de armar `cand_snap`, aplica el gate al resultado de `order_neutral`:

```python
    gate_out = aplicar_gate_candidatas(order_neutral(candidatas),
                                       estado=regime["estado"],
                                       votos_vivos=regime["votos"]["vivos"])
    cand_snap = {"generated_at": ts, "coverage": coverage, **gate_out}
```

(Reemplaza la línea actual `cand_snap = {... "candidates": order_neutral(candidatas)}`.)

Pasa los overrides a `compose_regime` (línea 141):

```python
    from regime.alt_season import effective_thresholds
    _overrides = (load_config().get("regime_gate") or {}).get("umbral_overrides") or {}
    regime = compose_regime(alt_contribs, btc_ret_30d, dominance, coverage_ratio,
                            thresholds=effective_thresholds(_overrides))
```

Migra `regenerate` a escritura atómica (líneas 156-158):

```python
    _atomic_write_json(_OUTPUT, cand_snap)            # antes: open(...,"w")+json.dump no-atómico
    _atomic_write_json(_ALT_SEASON_OUTPUT, alt_season_snap)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_run_valley_screener.py -v`
Expected: PASS (3 tests).
Run: `python -m pytest tests/ -m "not network" -n auto -q` → sin regresiones (con `enabled=false` el snapshot no gana campos).

- [ ] **Step 5: Commit**

```bash
git add tools/run_valley_screener.py tests/test_run_valley_screener.py
git commit -m "feat(valles): hook del gate en el screener + atomic write de valley_candidates"
```

---

## Task 6: Hook en el scanner

**Files:**
- Modify: `btc_scanner.py` (`scan`)
- Test: `tests/test_scanner.py` (extender)

**Interfaces:**
- Consumes: `regime.alt_season_read.leer_regimen` (Task 3); `regime.exposure_gate.evaluar_gate` (Task 2); `db.regime_gate_audit.registrar_decisiones` (Task 4).

**Notas de diseño resueltas (sitio exacto del hook):**
- **Leer régimen 1× por scan** (no por símbolo×tenant): justo tras cargar `_cfg` (línea ~247), vía `leer_regimen(cfg.regime_gate.frescura_umbral_seg)`. `es_alt = symbol != "BTCUSDT"`.
- **Aplicar la supresión** en el path de emisión real: tras el bloque "Veredicto" que fija `señal` (líneas ~700-713) y ANTES de `rep.update(...)` (línea 716) — espeja cómo el participation-cap bloquea `señal_activa`. `try/except` PROPIO (no el del v2_shadow).
- **Auditar** 1 fila por símbolo (tenant_id=None), batch.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scanner.py  (añadir)
import btc_scanner
from regime.alt_season_read import RegimenVivo

_RV_BTC = RegimenVivo(estado="btc", frescura="fresco", votos_vivos=3,
                      generated_at="2026-06-23T00:00:00+00:00", snapshot={})
_ON = {"regime_gate": {"enabled": True, "umbral_overrides": {}}}

def test_gate_suprime_alt_en_btc():
    # enabled + régimen 'btc' fresco + un símbolo alt con señal → señal suprimida.
    señal, estado, fila = btc_scanner.aplicar_gate_scanner(
        symbol="ADAUSDT", señal=True, estado_actual="✅ SEÑAL LONG", rv=_RV_BTC, cfg=_ON)
    assert señal is False and "alt-season" in estado.lower() and fila["nivel"] == "suprime"

def test_gate_no_toca_btc():
    señal, estado, fila = btc_scanner.aplicar_gate_scanner(
        symbol="BTCUSDT", señal=True, estado_actual="✅ SEÑAL LONG", rv=_RV_BTC, cfg=_ON)
    assert señal is True and fila["nivel"] == "pasa"   # BTC no es alt → pasa

def test_gate_disabled_no_toca():
    señal, estado, fila = btc_scanner.aplicar_gate_scanner(
        symbol="ADAUSDT", señal=True, estado_actual="✅ SEÑAL LONG", rv=_RV_BTC,
        cfg={"regime_gate": {"enabled": False}})
    assert señal is True and fila is None             # disabled: no toca señal, no audita
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_scanner.py -v -k gate`
Expected: FAIL con `AttributeError: module 'btc_scanner' has no attribute 'aplicar_gate_scanner'`.

- [ ] **Step 3: Implement**

En `btc_scanner.py`, añade imports (arriba) y la función pura:

```python
from regime.alt_season_read import leer_regimen, RegimenVivo
from regime.exposure_gate import evaluar_gate

def aplicar_gate_scanner(*, symbol: str, señal: bool, estado_actual: str,
                         rv: RegimenVivo, cfg: dict):
    """Aplica el gate (motor 'scanner'). Devuelve (señal, estado, fila_auditoria|None).
    fila=None cuando el gate está off (no se audita con enabled=false → byte-idéntico)."""
    if not (cfg.get("regime_gate") or {}).get("enabled", False):
        return señal, estado_actual, None
    es_alt = symbol != "BTCUSDT"
    d = evaluar_gate(rv.estado, rv.frescura, rv.votos_vivos, es_alt, cfg)
    fila = {"motor": "scanner", "symbol": symbol, "estado_regimen": d.estado_regimen,
            "nivel": d.nivel, "es_alt": es_alt, "regime_frescura": d.regime_frescura,
            "votos_vivos": d.votos_vivos, "enforced": d.enforced,
            "umbral_version": d.umbral_version, "tenant_id": None}
    if señal and d.nivel == "suprime":
        return False, f"🌧️  SEÑAL {symbol} suprimida — fuera de alt-season (clima '{d.estado_regimen}')", fila
    return señal, estado_actual, fila
```

En `scan()`, tras cargar `_cfg` (línea ~247), lee el régimen 1× (fail-open):

```python
    # Gate de exposición por régimen (#alt-season): leer el régimen UNA vez por scan.
    _gate_rv = None
    try:
        _frescura_umbral = float((_cfg.get("regime_gate") or {}).get("frescura_umbral_seg", 27000))
        _gate_rv = leer_regimen(_frescura_umbral)
    except Exception as _gate_read_err:
        log.warning("regime_gate: lectura de régimen falló para %s — fail-open: %s", symbol, _gate_read_err)
```

Justo ANTES de `rep.update({...})` (línea 716), tras el bloque que fija `señal`:

```python
    # Gate de exposición: esconde señales alt en mal clima (fail-open propio).
    _gate_fila = None
    if _gate_rv is not None:
        try:
            señal, estado, _gate_fila = aplicar_gate_scanner(
                symbol=symbol, señal=señal, estado_actual=estado, rv=_gate_rv, cfg=_cfg)
        except Exception as _gate_err:
            log.warning("regime_gate: aplicar falló para %s — fail-open: %s", symbol, _gate_err)
    if _gate_fila is not None:
        try:
            from db.regime_gate_audit import registrar_decisiones
            registrar_decisiones([_gate_fila])
        except Exception:
            log.warning("regime_gate_audit (scanner) falló — fail-open", exc_info=True)
```

(`señal` y `estado` ya son variables locales en ese punto — el bloque veredicto las definió.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_scanner.py -v -k gate`
Expected: PASS (3 tests).
Run: `python -m pytest tests/ -m "not network" -n auto -q` → sin regresiones (con `enabled=false`, `aplicar_gate_scanner` devuelve la señal intacta y `fila=None`).

- [ ] **Step 5: Commit**

```bash
git add btc_scanner.py tests/test_scanner.py
git commit -m "feat(scanner): hook del gate de exposición (suprime señal alt en mal clima, fail-open)"
```

---

## Task 7: Válvula "ver ocultas" en Valles (frontend)

**Files:**
- Modify: el consumidor de `/valley-candidates` en `frontend/src/components/valles/` (PickScreen / la lista de candidatas) + su tipo en `types.ts`.
- Test: extender el e2e existente o un test de componente si hay (ver `frontend/e2e/`).

**Interfaces:**
- Consumes: el payload de `GET /valley-candidates` ahora con `candidatas_ocultas: ValleyCandidate[]` (presente solo cuando el gate está enabled) y, por candidata atenuada, `clima_ambiguo?: boolean`.

- [ ] **Step 1: Localizar el consumidor**

Run: `grep -rn "valley-candidates\|candidates" frontend/src/components/valles/`
Identifica el componente que renderiza la lista (PickScreen o useValleyBundle). Lee su tipo de candidata.

- [ ] **Step 2: Extender el tipo**

En `frontend/src/components/valles/types.ts` (o donde viva `ValleyCandidate`), añade:

```typescript
export interface ValleyCandidatesResponse {
  generated_at: string | null;
  candidates: ValleyCandidate[];
  candidatas_ocultas?: (ValleyCandidate & { clima: string })[];   // solo si el gate enforça
  frescura?: Frescura;
}
// y en ValleyCandidate:
//   clima_ambiguo?: boolean;
```

- [ ] **Step 3: Renderizar la válvula (colapsada por defecto)**

En el componente de la lista, bajo las candidatas visibles, si `candidatas_ocultas?.length`:

```tsx
{ocultas.length > 0 && (
  <button className={styles.verOcultas} onClick={() => setMostrarOcultas(v => !v)}>
    {mostrarOcultas
      ? 'Ocultar'
      : `${ocultas.length} alts fuera de alt-season — ver`}
  </button>
)}
{mostrarOcultas && ocultas.map(c => (
  <CandidatasRow key={c.symbol} c={c} atenuada nota={c.clima} />
))}
```

Y para las visibles atenuadas (`clima_ambiguo`), un marcador discreto ("clima ambiguo") en su fila. Copy en venezolano, sin imperativos (doctrina anti-veredicto: el clima es un hecho, no un veredicto sobre la coin).

- [ ] **Step 4: Verificar**

Run: `cd frontend && npm run build` (tsc + build sin errores).
Si hay e2e relevante: `cd frontend && npx playwright test` con el harness existente (ver memoria `e2e-playwright-harness`). Verifica que con candidatas ocultas aparece la línea "N alts fuera de alt-season — ver" y al click se expanden.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/valles/
git commit -m "feat(valles-ui): válvula 'ver ocultas' para alts fuera de alt-season"
```

---

## Task 8: Enmienda de doctrina (BLOQUEANTE del merge, #6)

**Files:**
- Modify: `docs/superpowers/specs/es/2026-06-18-alt-season-regimen-design.md`

- [ ] **Step 1: Localizar la sección**

Run: `grep -n "modulación per-coin\|ENMARCA\|no modula" docs/superpowers/specs/es/2026-06-18-alt-season-regimen-design.md`

- [ ] **Step 2: Añadir el apuntador**

En la sección "Sin modulación per-coin por régimen", añade una nota (sin borrar el texto histórico):

```markdown
> **Enmienda 2026-06-23:** esta regla queda SUPERADA para el eje de EXPOSICIÓN.
> El régimen ahora gatea qué alts afloran (esconde en clima 'btc', atenúa en
> 'mixto' por empate). NO es scoring per-coin: es un filtro de exposición sobre un
> hecho de mercado, igual para todas las alts; el veredicto per-coin sigue
> prohibido. Ver `docs/superpowers/specs/es/2026-06-23-regimen-al-trade-gate-design.md`.
```

- [ ] **Step 3: Verificar coherencia**

Lee el bloque editado: las dos specs ya no se contradicen (la de 2026-06-18 apunta a la enmienda).

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/es/2026-06-18-alt-season-regimen-design.md
git commit -m "docs(regime): enmienda de doctrina — el régimen modula exposición (apuntador al spec 06-23)"
```

---

## Verificación final (tras todas las tareas)

- [ ] `python -m pytest tests/ -m "not network" -n auto -q` — todo verde, sin regresiones.
- [ ] `cd frontend && npm run build` — sin errores tsc.
- [ ] **Byte-identidad:** con `cfg.regime_gate.enabled=false` (default), `valley_candidates.json` no tiene `candidatas_ocultas`/`clima_ambiguo`, no hay filas en `regime_gate_audit`, y la señal del scanner es idéntica. (Cubierto por los tests de Task 5 y 6.)
- [ ] **Fail-open:** test de Task 3 (`test_failopen_combinado_con_gate`) confirma que clima 'btc' rancio → `enforced=False` → no esconde.
- [ ] Considerar (no bloqueante) correr `edge_study.py` vs panel 2020-2025 para sembrar `umbral_overrides` mejores antes de poner `enabled=true` en prod.

## Items diferidos (NO en este plan)

- Activar `enabled=true` en prod (cambio de config operacional, no de código).
- Afordancia de destape en el scanner (hoy solo auditable).
- Dashboard/endpoint de lectura de `regime_gate_audit` + alarma de tasa de supresión.
- Llamar `purgar_antiguos` desde un loop (retención automática) — la función existe; cablearla a `scanner/runtime.py` es un follow-up con su propio freshness owner.
- Conectar al épico regime-allocation #338 / modulación de sizing (choca con #4).
