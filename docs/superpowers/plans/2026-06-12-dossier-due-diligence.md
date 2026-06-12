# Dossier C — Due-Diligence de hechos citados: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sobre una candidata del screener A, traer hechos verificables citados del proyecto (equipo, presencia, actividad, financiación) vía Exa.ai + extracción estructurada con DeepSeek — esquema fijo, sin veredicto, cada hecho anclado a su URL fuente.

**Architecture:** Cliente fino de Exa (`research/exa_client.py`, read-only, fail-closed) → schema Pydantic estricto sin campo de opinión (`research/schemas.py`) → orquestador (`research/dossier.py`) que arma queries por dominio, llama Exa, pide a DeepSeek extraer al esquema, aplica el candado anti-alucinación y deriva `estado_general` → caché global con TTL en `project_dossiers` → API `GET /dossier/{symbol}` → botón + panel en el frontend. La red (Exa + DeepSeek) se inyecta como callables para tests; nunca corre dentro de una transacción.

**Tech Stack:** Python 3.12 / FastAPI / SQLite / Pydantic, React 18 + TS (Vite), pytest + vitest. Exa REST (`https://api.exa.ai`, header `x-api-key`), DeepSeek chat completions (`https://api.deepseek.com/v1`, `deepseek-chat`, JSON mode).

**Spec (leer COMPLETO):** `docs/superpowers/specs/es/2026-06-12-dossier-due-diligence-design.md`, especialmente §1 (frontera de Voronov), §2 (esquema), §3 (blindaje sin-veredicto + `opaco` vs `no_disponible`).

**Reglas no negociables:**
- **Sin veredicto:** el esquema NO tiene campo de opinión/potencial/score. El prompt de DeepSeek prohíbe opinar. Un test de frontera lo verifica.
- **Candado anti-alucinación:** la `fuente` de cada hecho DEBE ser una URL que Exa devolvió; si no, el hecho se descarta.
- **`opaco` ≠ `no_disponible`:** búsqueda exitosa sin hallazgos = `opaco`; fallo técnico (Exa/DeepSeek caído, sin key) = `no_disponible`. NUNCA se confunden.
- **Red fuera de tx:** Exa y DeepSeek se llaman FUERA de cualquier `transaction()`. La caché se escribe en una tx corta sin I/O.
- **`EXA_API_KEY` fail-closed:** sin credencial → `no_disponible`, no rompe.
- **No per-tenant:** el dossier es global (información pública del proyecto).
- Comentarios/docstrings en español. UI en español.

---

### Task 1: Cliente Exa (`research/exa_client.py`)

**Files:**
- Create: `research/__init__.py` (vacío)
- Create: `research/exa_client.py`
- Test: `tests/test_exa_client.py`

- [ ] **Step 1: Write the failing test**

Crear `tests/test_exa_client.py`:

```python
"""Tests del cliente fino de Exa (read-only, fail-closed).

La red se mockea en _http_post. Spec §4."""
from unittest.mock import patch

import pytest

from research.exa_client import ExaClient, ExaUnavailable


def _resp(status, payload):
    class _R:
        status_code = status
        def json(self):
            return payload
    return _R()


def test_search_with_contents_devuelve_bloques_con_url():
    payload = {"results": [
        {"title": "Cardano", "url": "https://cardano.org", "text": "Equipo: Charles Hoskinson..."},
        {"title": "IOHK", "url": "https://iohk.io", "text": "Fundada en 2015..."},
    ]}
    with patch("research.exa_client._http_post", return_value=_resp(200, payload)):
        c = ExaClient(api_key="K")
        out = c.search_with_contents("Cardano ADA team founders")
    assert len(out) == 2
    assert out[0]["url"] == "https://cardano.org"
    assert "Hoskinson" in out[0]["text"]


def test_sin_api_key_falla_closed():
    c = ExaClient(api_key="")
    with pytest.raises(ExaUnavailable):
        c.search_with_contents("cualquier query")


def test_rate_limit_levanta_unavailable():
    with patch("research.exa_client._http_post", return_value=_resp(429, {})):
        c = ExaClient(api_key="K")
        with pytest.raises(ExaUnavailable):
            c.search_with_contents("query")


def test_request_lleva_header_x_api_key():
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=20):
        captured["url"] = url
        captured["headers"] = headers
        return _resp(200, {"results": []})

    with patch("research.exa_client._http_post", side_effect=fake_post):
        ExaClient(api_key="SECRET").search_with_contents("q")
    assert "api.exa.ai" in captured["url"]
    assert captured["headers"]["x-api-key"] == "SECRET"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_exa_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'research'`

- [ ] **Step 3: Write the implementation**

Crear `research/__init__.py` vacío. Crear `research/exa_client.py`:

```python
"""Cliente fino read-only de Exa.ai (recolección de hechos para el dossier C).

Usa /search con contents en una llamada (embeddings-based search + parsed
HTML). NO usa /answer ni /research (esos sintetizan — reintroducirían el
juicio delegado; el dossier quiere hechos crudos citados y DeepSeek extrae).
Fail-closed: sin EXA_API_KEY o ante cualquier fallo de red/HTTP, levanta
ExaUnavailable (el orquestador lo traduce a estado 'no_disponible', NUNCA a
'opaco'). Spec §3.3, §4.
"""
from __future__ import annotations

import requests

_SEARCH_URL = "https://api.exa.ai/search"
_NUM_RESULTS = 8   # contents de hasta 10 resultados vienen gratis (free tier)


class ExaUnavailable(Exception):
    """Exa inalcanzable: sin key, rate-ban, timeout o HTTP no-200. Es un fallo
    técnico ('no pude buscar'), NUNCA un hallazgo ('busqué y no encontré')."""


def _http_post(url, json=None, headers=None, timeout=20):
    """Wrapper fino para mockear en tests."""
    return requests.post(url, json=json, headers=headers, timeout=timeout)


class ExaClient:
    def __init__(self, *, api_key: str):
        self._api_key = api_key

    def search_with_contents(self, query: str) -> list[dict]:
        """Devuelve [{title, url, text}] de los resultados de Exa para el query.
        Cada dict trae su URL fuente (el ancla del candado anti-alucinación).
        Levanta ExaUnavailable ante cualquier problema (fail-closed)."""
        if not self._api_key:
            raise ExaUnavailable("EXA_API_KEY ausente")
        body = {
            "query": query,
            "numResults": _NUM_RESULTS,
            "contents": {"text": True},
        }
        headers = {"x-api-key": self._api_key, "Content-Type": "application/json"}
        try:
            r = _http_post(_SEARCH_URL, json=body, headers=headers, timeout=20)
        except requests.RequestException as e:
            raise ExaUnavailable(type(e).__name__) from None
        if r.status_code in (429, 418):
            raise ExaUnavailable(f"rate banned HTTP {r.status_code}")
        if r.status_code != 200:
            raise ExaUnavailable(f"HTTP {r.status_code}")
        results = r.json().get("results", [])
        return [
            {"title": x.get("title", ""), "url": x.get("url", ""),
             "text": x.get("text", "")}
            for x in results if x.get("url")
        ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_exa_client.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add research/__init__.py research/exa_client.py tests/test_exa_client.py
git commit -m "feat(research): ExaClient — cliente fino read-only, fail-closed (dossier C §4)"
```

---

### Task 2: Schema del dossier (`research/schemas.py`)

**Files:**
- Create: `research/schemas.py`
- Test: `tests/test_dossier_schema.py`

- [ ] **Step 1: Write the failing tests**

Crear `tests/test_dossier_schema.py`:

```python
"""Tests del schema del dossier (estricto, sin campo de opinión). Spec §2."""
import pytest
from pydantic import ValidationError

from research.schemas import Dossier, MiembroEquipo, Canal, Cita, Hito


def test_dossier_minimo_valido():
    d = Dossier(symbol="ADAUSDT", estado_general="opaco")
    assert d.symbol == "ADAUSDT"
    assert d.equipo == []
    assert d.equipo_identificado is False
    assert d.no_encontrado_en == []


def test_estado_general_solo_acepta_los_tres_valores():
    for ok in ("rastreable", "opaco", "no_disponible"):
        Dossier(symbol="X", estado_general=ok)
    with pytest.raises(ValidationError):
        Dossier(symbol="X", estado_general="prometedor")   # no es un hallazgo válido


def test_schema_no_tiene_campo_de_opinion():
    # Frontera de Voronov: ningún campo de opinión/potencial/score/recomendación.
    campos = set(Dossier.model_fields)
    prohibidos = {"veredicto", "opinion", "potencial", "score", "recomendacion",
                  "rating", "calidad", "prediccion"}
    assert campos.isdisjoint(prohibidos)


def test_extra_forbid_rechaza_campos_no_declarados():
    with pytest.raises(ValidationError):
        Dossier(symbol="X", estado_general="opaco", veredicto="bueno")  # extra → rechazado


def test_miembro_equipo_y_canal_y_cita_y_hito():
    m = MiembroEquipo(nombre="Charles Hoskinson", rol="CEO",
                      enlaces=["https://x.com/IOHK_Charles"], fuente="https://iohk.io")
    assert m.rol == "CEO"
    c = Canal(url="https://cardano.org", activo="si", fuente="https://cardano.org")
    assert c.activo == "si"
    cita = Cita(valor="2026-05-01", fuente="https://github.com/...")
    assert cita.fuente.startswith("https://")
    h = Hito(descripcion="Mainnet launch", fecha="2017-09", fuente="https://...")
    assert h.descripcion
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_dossier_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'research.schemas'`

- [ ] **Step 3: Write the implementation**

Crear `research/schemas.py`:

```python
"""Schema del dossier de due-diligence (estricto, sin campo de opinión).

Cada hecho lleva su `fuente` (URL). `extra='forbid'` en todos los modelos:
un campo de opinión que el LLM intente meter → output rechazado (frontera de
Voronov por construcción). Spec §2."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class MiembroEquipo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nombre: str
    rol: str | None = None
    enlaces: list[str] = []
    fuente: str


class Canal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str | None = None
    activo: Literal["si", "no", "desconocido"] = "desconocido"
    fuente: str | None = None


class Cita(BaseModel):
    model_config = ConfigDict(extra="forbid")
    valor: str
    fuente: str


class Hito(BaseModel):
    model_config = ConfigDict(extra="forbid")
    descripcion: str
    fecha: str | None = None
    fuente: str


class Dossier(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    equipo: list[MiembroEquipo] = []
    equipo_identificado: bool = False
    # presencia keys esperadas: sitio_web, github, twitter, telegram_discord, whitepaper.
    presencia: dict[str, Canal] = {}
    # actividad keys esperadas: ultimo_commit_github, ultimo_release, ultimo_post_anuncio.
    actividad: dict[str, Cita] = {}
    financiacion: list[Hito] = []
    hitos: list[Hito] = []
    estado_general: Literal["rastreable", "opaco", "no_disponible"]
    # Qué se buscó y NO apareció (la ausencia es información — spec §2).
    no_encontrado_en: list[str] = []
    generated_at: str | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_dossier_schema.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add research/schemas.py tests/test_dossier_schema.py
git commit -m "feat(research): schema Dossier estricto sin campo de opinión (dossier C §2)"
```

---

### Task 3: Orquestador (`research/dossier.py`)

**Files:**
- Create: `research/dossier.py`
- Test: `tests/test_dossier_build.py`

- [ ] **Step 1: Write the failing tests**

Crear `tests/test_dossier_build.py`:

```python
"""Tests del orquestador del dossier (Exa + extracción inyectadas).

Cubre: extracción al esquema, candado anti-alucinación, opaco vs no_disponible,
prohibición de opinión en el prompt. Spec §3."""
import pytest

from research.dossier import build_dossier, EXTRACTION_PROMPT
from research.exa_client import ExaUnavailable


def _exa_ok(query):
    # Mismo set de URLs para todos los dominios; el candado las usa como ancla.
    return [
        {"title": "Cardano", "url": "https://cardano.org", "text": "CEO Charles Hoskinson"},
        {"title": "GitHub", "url": "https://github.com/input-output-hk", "text": "último commit 2026-05"},
    ]


def test_extrae_al_esquema_con_estado_rastreable():
    def extract(content, prompt):
        return {
            "equipo": [{"nombre": "Charles Hoskinson", "rol": "CEO",
                        "enlaces": [], "fuente": "https://cardano.org"}],
            "equipo_identificado": True,
            "presencia": {"sitio_web": {"url": "https://cardano.org", "activo": "si",
                                        "fuente": "https://cardano.org"}},
            "actividad": {"ultimo_commit_github": {"valor": "2026-05",
                          "fuente": "https://github.com/input-output-hk"}},
            "financiacion": [], "hitos": [],
        }
    d = build_dossier("ADAUSDT", exa_search=_exa_ok, extract_fn=extract)
    assert d.estado_general == "rastreable"
    assert d.equipo[0].nombre == "Charles Hoskinson"
    assert d.symbol == "ADAUSDT"


def test_candado_anti_alucinacion_descarta_cita_inventada():
    def extract(content, prompt):
        return {
            "equipo": [
                {"nombre": "Real", "fuente": "https://cardano.org"},          # ✓ en el set
                {"nombre": "Inventado", "fuente": "https://fake-no-existe.xyz"},  # ✗ alucinada
            ],
            "equipo_identificado": True, "presencia": {}, "actividad": {},
            "financiacion": [], "hitos": [],
        }
    d = build_dossier("ADAUSDT", exa_search=_exa_ok, extract_fn=extract)
    nombres = [m.nombre for m in d.equipo]
    assert "Real" in nombres
    assert "Inventado" not in nombres   # cita fuera del set de Exa → descartada


def test_exa_caido_es_no_disponible_no_opaco():
    def exa_falla(query):
        raise ExaUnavailable("rate banned")
    d = build_dossier("ADAUSDT", exa_search=exa_falla, extract_fn=lambda c, p: {})
    assert d.estado_general == "no_disponible"   # fallo técnico, NO opaco


def test_exa_vacio_es_opaco_legitimo():
    def exa_vacio(query):
        return []   # buscó y no encontró
    def extract(content, prompt):
        return {"equipo": [], "equipo_identificado": False, "presencia": {},
                "actividad": {}, "financiacion": [], "hitos": []}
    d = build_dossier("XYZUSDT", exa_search=exa_vacio, extract_fn=extract)
    assert d.estado_general == "opaco"
    assert d.no_encontrado_en   # lista de qué se buscó y no apareció


def test_extraccion_invalida_es_no_disponible():
    def extract(content, prompt):
        raise RuntimeError("deepseek timeout")
    d = build_dossier("ADAUSDT", exa_search=_exa_ok, extract_fn=extract)
    assert d.estado_general == "no_disponible"


def test_prompt_prohibe_opinar():
    p = EXTRACTION_PROMPT.lower()
    assert "prohibido" in p
    for verbo in ("opinar", "evaluar", "recomendar", "predecir", "calificar"):
        assert verbo in p
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_dossier_build.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'research.dossier'`

- [ ] **Step 3: Write the implementation**

Crear `research/dossier.py`:

```python
"""Orquestador del dossier de due-diligence (dossier C).

Arma queries por dominio, recolecta con Exa, pide a DeepSeek EXTRAER al
esquema fijo, aplica el candado anti-alucinación (cada fuente debe ser una URL
que Exa devolvió) y deriva estado_general. La red (exa_search, extract_fn) se
INYECTA → testeable sin red. Distingue 'opaco' (buscó, no encontró) de
'no_disponible' (fallo técnico). Spec §3."""
from __future__ import annotations

import json
import logging
import os

import requests

from .exa_client import ExaClient, ExaUnavailable
from .schemas import Canal, Cita, Dossier, Hito, MiembroEquipo

log = logging.getLogger("research.dossier")

# Dominios de búsqueda (spec §2): el query se arma sobre el ticker base.
_DOMINIOS = {
    "equipo": "{base} cryptocurrency project team founders who is behind",
    "presencia": "{base} crypto official website github twitter telegram whitepaper",
    "actividad": "{base} crypto latest github commit release announcement news",
    "financiacion": "{base} crypto funding round investors raised backers",
}

EXTRACTION_PROMPT = (
    "Sos un EXTRACTOR de hechos, no un analista. Te doy contenido web con sus "
    "URLs. Llená el JSON del esquema SOLO con hechos presentes en el contenido, "
    "y para cada hecho poné en `fuente` la URL exacta de donde lo sacaste. Si un "
    "hecho no está en el contenido, omitilo (no lo inventes). "
    "PROHIBIDO: opinar, evaluar, recomendar, predecir, calificar el proyecto, o "
    "agregar cualquier campo que no esté en el esquema. Devolvé SOLO el JSON."
)

DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"


def _base_asset(symbol: str) -> str:
    s = symbol.upper()
    for q in ("USDT", "USDC", "BUSD", "FDUSD"):
        if s.endswith(q):
            return s[: -len(q)]
    return s


def _http_post(url, json=None, headers=None, timeout=60):
    return requests.post(url, json=json, headers=headers, timeout=timeout)


def deepseek_extract(content: str, prompt: str) -> dict:
    """Llamada de EXTRACCIÓN estructurada a DeepSeek (JSON mode). Aislada para
    mockear. Levanta si falta la key o falla (el caller lo mapea a
    no_disponible). NO es conversacional — una sola completion estructurada."""
    api_key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY ausente")
    body = {
        "model": "deepseek-chat",
        "messages": [{"role": "system", "content": prompt},
                     {"role": "user", "content": content}],
        "response_format": {"type": "json_object"},
        "temperature": 0,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    r = _http_post(DEEPSEEK_URL, json=body, headers=headers, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"deepseek HTTP {r.status_code}")
    return json.loads(r.json()["choices"][0]["message"]["content"])


def _anchor_ok(fuente: str | None, url_set: set) -> bool:
    """Candado anti-alucinación: la fuente debe ser una URL que Exa devolvió."""
    return bool(fuente) and fuente in url_set


def build_dossier(symbol: str, *, exa_search, extract_fn) -> Dossier:
    """Construye el dossier. `exa_search(query) -> [{title,url,text}]` y
    `extract_fn(content, prompt) -> dict` se inyectan (prod = ExaClient +
    deepseek_extract). Cualquier fallo de red/extracción → no_disponible."""
    base = _base_asset(symbol)

    # ── Recolección (Exa) — FUERA de cualquier tx. ──
    bloques: list[dict] = []
    url_set: set = set()
    try:
        for plantilla in _DOMINIOS.values():
            for b in exa_search(plantilla.format(base=base)):
                bloques.append(b)
                if b.get("url"):
                    url_set.add(b["url"])
    except ExaUnavailable as e:
        log.warning("DOSSIER_NO_DISPONIBLE symbol=%s causa=exa:%s", symbol, e)
        return Dossier(symbol=symbol, estado_general="no_disponible")

    # ── Extracción (DeepSeek) ──
    contenido = "\n\n".join(f"URL: {b['url']}\n{b['text']}" for b in bloques)
    try:
        crudo = extract_fn(contenido, EXTRACTION_PROMPT)
    except Exception as e:  # noqa: BLE001 — cualquier fallo de extracción = no_disponible
        log.warning("DOSSIER_NO_DISPONIBLE symbol=%s causa=extract:%s", symbol, e)
        return Dossier(symbol=symbol, estado_general="no_disponible")

    # ── Candado anti-alucinación + construcción tipada ──
    equipo = [
        MiembroEquipo(**m) for m in crudo.get("equipo", [])
        if _anchor_ok(m.get("fuente"), url_set)
    ]
    presencia = {
        k: Canal(**v) for k, v in crudo.get("presencia", {}).items()
        if _anchor_ok(v.get("fuente"), url_set)
    }
    actividad = {
        k: Cita(**v) for k, v in crudo.get("actividad", {}).items()
        if _anchor_ok(v.get("fuente"), url_set)
    }
    financiacion = [
        Hito(**h) for h in crudo.get("financiacion", [])
        if _anchor_ok(h.get("fuente"), url_set)
    ]
    hitos = [
        Hito(**h) for h in crudo.get("hitos", [])
        if _anchor_ok(h.get("fuente"), url_set)
    ]

    # ── Estado derivado de hechos (rastreable vs opaco) ──
    no_encontrado: list[str] = []
    if not equipo:
        no_encontrado.append("equipo")
    if not presencia:
        no_encontrado.append("presencia")
    if not actividad:
        no_encontrado.append("actividad")
    if not financiacion:
        no_encontrado.append("financiacion")
    # opaco: equipo no identificado + sin presencia + sin actividad (spec §2).
    opaco = (not equipo) and (not presencia) and (not actividad)
    estado = "opaco" if opaco else "rastreable"

    return Dossier(
        symbol=symbol, equipo=equipo, equipo_identificado=bool(equipo),
        presencia=presencia, actividad=actividad, financiacion=financiacion,
        hitos=hitos, estado_general=estado, no_encontrado_en=no_encontrado,
    )


def build_dossier_live(symbol: str) -> Dossier:
    """Conveniencia de producción: arma el ExaClient real + deepseek_extract y
    construye el dossier. La red corre aquí, FUERA de toda transacción."""
    client = ExaClient(api_key=(os.environ.get("EXA_API_KEY") or "").strip())
    return build_dossier(symbol, exa_search=client.search_with_contents,
                         extract_fn=deepseek_extract)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_dossier_build.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add research/dossier.py tests/test_dossier_build.py
git commit -m "feat(research): build_dossier — extracción DeepSeek + candado anti-alucinación + opaco/no_disponible (dossier C §3)"
```

---

### Task 4: Migración `project_dossiers` (caché)

**Files:**
- Modify: `db/schema.py`
- Test: `tests/test_project_dossiers_migration.py`

- [ ] **Step 1: Write the failing test**

Crear `tests/test_project_dossiers_migration.py` (replicar la fixture `_fresh_db` de `tests/test_observed_orders.py` — `init_db` con `btc_api.DB_FILE` a tmp_path):

```python
"""Tests de la migración project_dossiers (caché global del dossier). Spec §4."""
import sqlite3


class TestMigracionProjectDossiers:
    def test_tabla_existe_y_es_global(self, fresh_db_con):
        con = fresh_db_con
        row = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='project_dossiers'"
        ).fetchone()
        assert row is not None
        # PK por symbol (global, no per-tenant): un upsert por símbolo.
        cols = {r[1] for r in con.execute("PRAGMA table_info(project_dossiers)")}
        assert {"symbol", "dossier_json", "generated_at"} <= cols
        assert "tenant_id" not in cols   # global por diseño

    def test_upsert_por_symbol(self, fresh_db_con):
        con = fresh_db_con
        con.execute("INSERT INTO project_dossiers(symbol, dossier_json, generated_at) "
                    "VALUES ('ADAUSDT', '{}', '2026-06-12T00:00:00+00:00')")
        con.execute("INSERT OR REPLACE INTO project_dossiers(symbol, dossier_json, generated_at) "
                    "VALUES ('ADAUSDT', '{\"x\":1}', '2026-06-12T01:00:00+00:00')")
        n = con.execute("SELECT COUNT(*) FROM project_dossiers WHERE symbol='ADAUSDT'").fetchone()[0]
        assert n == 1   # replace, no duplica

    def test_migracion_idempotente(self, tmp_path):
        from db.schema import init_db
        init_db()
        init_db()
```

(Usá los nombres reales de la fixture que copies de `test_observed_orders.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_project_dossiers_migration.py -v`
Expected: FAIL — tabla `project_dossiers` no existe.

- [ ] **Step 3: Write the migration**

En `db/schema.py`, añadir tras `_migrate_observed_orders` (final del archivo):

```python
def _migrate_project_dossiers(con: sqlite3.Connection) -> None:
    """Tabla project_dossiers — caché global del dossier de due-diligence (C).

    Global (NO per-tenant): el dossier de un proyecto es información pública,
    idéntica para todos. PK por symbol → INSERT OR REPLACE es el upsert natural.
    El TTL (7 días) se enforza en el read del endpoint (api/dossier.py), no en
    el schema. Idempotente: CREATE TABLE IF NOT EXISTS.

    Spec: docs/superpowers/specs/es/2026-06-12-dossier-due-diligence-design.md §4.
    """
    con.execute(
        """CREATE TABLE IF NOT EXISTS project_dossiers (
               symbol        TEXT PRIMARY KEY,
               dossier_json  TEXT NOT NULL,
               generated_at  TEXT NOT NULL
           )"""
    )
    log.info("_migrate_project_dossiers: project_dossiers table ensured.")
```

Wiring en `init_db`: en el bloque `with transaction() as con_bc:` que llama `_migrate_observed_orders` (línea ~453), añadir justo después:

```python
        _migrate_project_dossiers(con_bc)   # dossier C: caché global, mismo bloque
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_project_dossiers_migration.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add db/schema.py tests/test_project_dossiers_migration.py
git commit -m "feat(db): tabla project_dossiers — caché global del dossier (dossier C §4)"
```

---

### Task 5: API — `GET /dossier/{symbol}` (caché-or-generate)

**Files:**
- Create: `db/dossiers.py` (helpers SQL puros de caché)
- Create: `api/dossier.py`
- Modify: `btc_api.py` (registrar el router)
- Test: `tests/test_dossier_api.py`

- [ ] **Step 1: Write the failing tests**

Crear `tests/test_dossier_api.py`:

```python
"""Tests del endpoint GET /dossier/{symbol} (caché TTL, no per-tenant). Spec §4/§5."""
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.dossier import router, _TTL_SECONDS


def _app():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _fake_dossier(symbol):
    from research.schemas import Dossier
    return Dossier(symbol=symbol, estado_general="opaco", no_encontrado_en=["equipo"])


def test_genera_y_cachea_en_miss(monkeypatch, tmp_path):
    # DB fresca + sin caché → genera (build_dossier_live mockeado) y devuelve.
    import btc_api
    monkeypatch.setattr(btc_api, "DB_FILE", str(tmp_path / "d.db"))
    from db.schema import init_db
    init_db()
    with patch("api.dossier.build_dossier_live", side_effect=_fake_dossier) as gen:
        r = _app().get("/dossier/ADAUSDT")
    assert r.status_code == 200
    assert r.json()["estado_general"] == "opaco"
    assert gen.call_count == 1
    # Segunda llamada: caché-hit, NO regenera.
    with patch("api.dossier.build_dossier_live", side_effect=_fake_dossier) as gen2:
        r2 = _app().get("/dossier/ADAUSDT")
    assert r2.status_code == 200
    assert gen2.call_count == 0   # servido desde caché


def test_refresh_fuerza_regeneracion(monkeypatch, tmp_path):
    import btc_api
    monkeypatch.setattr(btc_api, "DB_FILE", str(tmp_path / "d.db"))
    from db.schema import init_db
    init_db()
    with patch("api.dossier.build_dossier_live", side_effect=_fake_dossier):
        _app().get("/dossier/ADAUSDT")
    with patch("api.dossier.build_dossier_live", side_effect=_fake_dossier) as gen:
        r = _app().get("/dossier/ADAUSDT?refresh=true")
    assert gen.call_count == 1   # refresh ignora la caché


def test_ttl_es_siete_dias():
    assert _TTL_SECONDS == 7 * 24 * 3600
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_dossier_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'api.dossier'`

- [ ] **Step 3: Write the implementation**

Crear `db/dossiers.py` (helpers SQL puros — reciben `con`, capa 1):

```python
"""Helpers SQL puros de la caché de dossiers (capa 1). Reciben `con`, corren
SQL, devuelven data. Sin transaction(), sin side-effects. Spec §4."""
from __future__ import annotations

import sqlite3


def db_get_dossier(con: sqlite3.Connection, symbol: str) -> dict | None:
    """Fila de caché {dossier_json, generated_at} para el símbolo, o None."""
    row = con.execute(
        "SELECT dossier_json, generated_at FROM project_dossiers WHERE symbol=?",
        (symbol,),
    ).fetchone()
    if row is None:
        return None
    return {"dossier_json": row[0], "generated_at": row[1]}


def db_put_dossier(con: sqlite3.Connection, *, symbol: str, dossier_json: str,
                   generated_at: str) -> None:
    """Upsert del dossier (global, PK por symbol)."""
    con.execute(
        "INSERT OR REPLACE INTO project_dossiers(symbol, dossier_json, generated_at) "
        "VALUES (?,?,?)", (symbol, dossier_json, generated_at),
    )
```

Crear `api/dossier.py`:

```python
"""API del dossier de due-diligence (C). GET /dossier/{symbol} con caché TTL.

Read-only respecto al estado del usuario, NO per-tenant (el dossier de un
proyecto es global). La generación (Exa + DeepSeek) corre FUERA de toda
transacción (red); solo el upsert de caché va en una tx corta. Spec §3.1, §4.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query

from db.dossiers import db_get_dossier, db_put_dossier
from db.transaction import snapshot_connection, transaction
from research.dossier import build_dossier_live

log = logging.getLogger("api.dossier")

router = APIRouter(tags=["dossier"])

_TTL_SECONDS = 7 * 24 * 3600   # 7 días (spec §5)


def _fresh(generated_at: str) -> bool:
    """¿La foto de caché sigue dentro del TTL?"""
    try:
        ts = datetime.fromisoformat(generated_at)
    except ValueError:
        return False
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    return age < _TTL_SECONDS


@router.get("/dossier/{symbol}", summary="Dossier de hechos citados de un proyecto")
def get_dossier(symbol: str, refresh: bool = Query(False)) -> dict:
    """Devuelve el dossier del símbolo. Caché-hit fresco → lo sirve; miss o
    `refresh=true` o caché stale → genera (Exa+DeepSeek), cachea y devuelve.
    El dossier NUNCA 500ea por fallo externo: build_dossier_live devuelve un
    Dossier con estado 'no_disponible' si Exa/DeepSeek fallan."""
    symbol = symbol.upper()

    if not refresh:
        with snapshot_connection() as con:
            cached = db_get_dossier(con, symbol)
        if cached is not None and _fresh(cached["generated_at"]):
            return json.loads(cached["dossier_json"])

    # ── Generación: red FUERA de la tx. ──
    dossier = build_dossier_live(symbol)
    generated_at = datetime.now(timezone.utc).isoformat()
    dossier.generated_at = generated_at
    payload = dossier.model_dump()

    # ── Caché: tx corta, sin I/O. NO cachear los 'no_disponible' (fallo
    #    técnico transitorio — que el próximo intento reintente). ──
    if dossier.estado_general != "no_disponible":
        with transaction() as con:
            db_put_dossier(con, symbol=symbol,
                           dossier_json=json.dumps(payload, ensure_ascii=False),
                           generated_at=generated_at)
    return payload
```

En `btc_api.py`: registrar el router junto a los demás (seguir el estilo de `from api.valleys import router as valleys_router` + `app.include_router(valleys_router)`):

```python
from api.dossier import router as dossier_router
...
app.include_router(dossier_router)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_dossier_api.py -v` y `python -c "import btc_api"`
Expected: PASS + import sano.

- [ ] **Step 5: Commit**

```bash
git add db/dossiers.py api/dossier.py btc_api.py tests/test_dossier_api.py
git commit -m "feat(api): GET /dossier/{symbol} — caché TTL 7d, red fuera de tx, no-tenant (dossier C §4)"
```

---

### Task 6: Frontend — panel del dossier + botón en Valles

**Files:**
- Modify: `frontend/src/types.ts` (interfaces del dossier)
- Modify: `frontend/src/api.ts` (`getDossier`)
- Create: `frontend/src/components/ProjectDossier.tsx`, `.module.css`, `.test.tsx`
- Modify: `frontend/src/components/ValleysView.tsx` (botón "Dossier" por fila + estado)

- [ ] **Step 1: Write the failing test**

Crear `frontend/src/components/ProjectDossier.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ProjectDossier } from './ProjectDossier';
import type { Dossier } from '../types';

const rastreable: Dossier = {
  symbol: 'ADAUSDT', estado_general: 'rastreable', equipo_identificado: true,
  equipo: [{ nombre: 'Charles Hoskinson', rol: 'CEO', enlaces: [], fuente: 'https://cardano.org' }],
  presencia: { sitio_web: { url: 'https://cardano.org', activo: 'si', fuente: 'https://cardano.org' } },
  actividad: {}, financiacion: [], hitos: [], no_encontrado_en: [],
  generated_at: '2026-06-12T00:00:00+00:00',
};

describe('ProjectDossier', () => {
  it('lista los hechos con su enlace fuente', () => {
    render(<ProjectDossier dossier={rastreable} loading={false} />);
    expect(screen.getByText('Charles Hoskinson')).toBeInTheDocument();
    const link = screen.getAllByRole('link').find((a) => a.getAttribute('href') === 'https://cardano.org');
    expect(link).toBeTruthy();   // cada hecho ancla a su fuente
  });

  it('muestra badge "opaco" con lo que no se encontró', () => {
    const opaco: Dossier = {
      symbol: 'XYZUSDT', estado_general: 'opaco', equipo_identificado: false,
      equipo: [], presencia: {}, actividad: {}, financiacion: [], hitos: [],
      no_encontrado_en: ['equipo', 'presencia', 'actividad'],
      generated_at: '2026-06-12T00:00:00+00:00',
    };
    render(<ProjectDossier dossier={opaco} loading={false} />);
    expect(screen.getByText(/opaco/i)).toBeInTheDocument();
    expect(screen.getByText(/equipo/)).toBeInTheDocument();   // qué se buscó y faltó
  });

  it('distingue "no disponible" de "opaco"', () => {
    const nd: Dossier = {
      symbol: 'XYZUSDT', estado_general: 'no_disponible', equipo_identificado: false,
      equipo: [], presencia: {}, actividad: {}, financiacion: [], hitos: [],
      no_encontrado_en: [], generated_at: null,
    };
    render(<ProjectDossier dossier={nd} loading={false} />);
    expect(screen.getByText(/no disponible|no se pudo/i)).toBeInTheDocument();
  });

  it('no muestra ningún texto de recomendación / score', () => {
    const { container } = render(<ProjectDossier dossier={rastreable} loading={false} />);
    expect(/recomend|comprar|potencial|score|veredicto/i.test(container.textContent ?? '')).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/ProjectDossier.test.tsx`
Expected: FAIL — módulo no existe.

- [ ] **Step 3: Write the implementation**

En `frontend/src/types.ts` (al final):

```ts
// Dossier C — hechos citados de un proyecto (sin veredicto). Spec §2.
export interface DossierMiembro { nombre: string; rol: string | null; enlaces: string[]; fuente: string; }
export interface DossierCanal { url: string | null; activo: 'si' | 'no' | 'desconocido'; fuente: string | null; }
export interface DossierCita { valor: string; fuente: string; }
export interface DossierHito { descripcion: string; fecha: string | null; fuente: string; }
export interface Dossier {
  symbol:              string;
  equipo:              DossierMiembro[];
  equipo_identificado: boolean;
  presencia:           Record<string, DossierCanal>;
  actividad:           Record<string, DossierCita>;
  financiacion:        DossierHito[];
  hitos:               DossierHito[];
  estado_general:      'rastreable' | 'opaco' | 'no_disponible';
  no_encontrado_en:    string[];
  generated_at:        string | null;
}
```

En `frontend/src/api.ts` (estilo de `getValleyCandidates`):

```ts
export function getDossier(symbol: string, refresh = false) {
  const q = refresh ? '?refresh=true' : '';
  return request<import('./types').Dossier>(`/dossier/${symbol}${q}`);
}
```

Crear `frontend/src/components/ProjectDossier.tsx`:

```tsx
import React from 'react';
import type { Dossier } from '../types';
import styles from './ProjectDossier.module.css';

// Dossier C — panel de HECHOS citados, sin veredicto. Cada hecho ancla a su
// fuente; "opaco" y "no disponible" son estados distintos (spec §2/§3).
const Fuente: React.FC<{ url: string | null }> = ({ url }) =>
  url ? <a className={styles.src} href={url} target="_blank" rel="noreferrer">fuente</a> : null;

export const ProjectDossier: React.FC<{ dossier: Dossier; loading: boolean }> = ({ dossier, loading }) => {
  if (loading) return <div className={styles.empty}>Investigando…</div>;
  const d = dossier;
  if (d.estado_general === 'no_disponible') {
    return <div className={styles.empty}>No se pudo investigar ahora (búsqueda no disponible). Probá refrescar.</div>;
  }
  return (
    <div className={styles.wrap}>
      <header className={styles.head}>
        <span className={styles.sym}>{d.symbol.replace('USDT', '')}</span>
        {d.estado_general === 'opaco' && (
          <span className={`${styles.badge} ${styles.badgeOpaco}`}>opaco</span>
        )}
        {d.generated_at && (
          <span className={`${styles.ts} prose`}>{new Date(d.generated_at).toLocaleDateString('es-ES')}</span>
        )}
      </header>

      {d.estado_general === 'opaco' && d.no_encontrado_en.length > 0 && (
        <p className={`${styles.gap} prose`}>No se encontró: {d.no_encontrado_en.join(', ')}.</p>
      )}

      {d.equipo.length > 0 && (
        <section className={styles.sec}>
          <h4>Equipo</h4>
          {d.equipo.map((m, i) => (
            <div key={i} className={styles.row}>
              <span>{m.nombre}{m.rol ? ` — ${m.rol}` : ''}</span> <Fuente url={m.fuente} />
            </div>
          ))}
        </section>
      )}

      {Object.keys(d.presencia).length > 0 && (
        <section className={styles.sec}>
          <h4>Presencia</h4>
          {Object.entries(d.presencia).map(([k, c]) => (
            <div key={k} className={styles.row}>
              <span>{k.replace(/_/g, ' ')}: {c.activo}</span> <Fuente url={c.url ?? c.fuente} />
            </div>
          ))}
        </section>
      )}

      {Object.keys(d.actividad).length > 0 && (
        <section className={styles.sec}>
          <h4>Actividad</h4>
          {Object.entries(d.actividad).map(([k, c]) => (
            <div key={k} className={styles.row}>
              <span>{k.replace(/_/g, ' ')}: {c.valor}</span> <Fuente url={c.fuente} />
            </div>
          ))}
        </section>
      )}

      {d.financiacion.length > 0 && (
        <section className={styles.sec}>
          <h4>Financiación</h4>
          {d.financiacion.map((h, i) => (
            <div key={i} className={styles.row}>
              <span>{h.descripcion}{h.fecha ? ` (${h.fecha})` : ''}</span> <Fuente url={h.fuente} />
            </div>
          ))}
        </section>
      )}
    </div>
  );
};
```

Crear `frontend/src/components/ProjectDossier.module.css` (tokens neutros del repo — inspeccioná `ValleysView.module.css` y reusá `--nbc-fg-muted`/`--nbc-border-dim`/`--warn`):

```css
.wrap { padding: 8px 0; font-size: 13px; }
.head { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.sym { font-weight: 700; }
.badge { font-size: 11px; padding: 2px 8px; border-radius: 4px; }
.badgeOpaco { color: var(--warn); border: 1px dashed var(--warn); }
.ts { font-size: 12px; color: var(--nbc-fg-muted); margin-left: auto; }
.gap { color: var(--warn); font-size: 12px; }
.sec { margin: 10px 0; }
.sec h4 { margin: 0 0 4px; color: var(--nbc-fg-muted); font-weight: 500; font-size: 12px; }
.row { display: flex; justify-content: space-between; gap: 12px; padding: 3px 0; }
.src { font-size: 11px; }
.empty { padding: 24px; text-align: center; color: var(--nbc-fg-muted); }
```

En `frontend/src/components/ValleysView.tsx`: añadir un botón "Dossier" por fila que cargue el dossier y lo muestre. Importar `getDossier` y `ProjectDossier`, añadir estado local `const [dossier, setDossier] = useState<Dossier | null>(null); const [dossierLoading, setDossierLoading] = useState(false);` y una columna/acción por fila con un botón que haga:

```tsx
              <button
                className={styles.dossierBtn}
                onClick={() => {
                  setDossier(null); setDossierLoading(true);
                  getDossier(c.symbol).then(setDossier).finally(() => setDossierLoading(false));
                }}
              >Dossier</button>
```

Y debajo de la tabla, renderizar condicionalmente:

```tsx
      {(dossier || dossierLoading) && <ProjectDossier dossier={dossier!} loading={dossierLoading} />}
```

(Añadir `.dossierBtn` a `ValleysView.module.css` con estilo de botón neutro existente; importar `Dossier` de `../types`. Mantener la vista sin colores de señal.)

- [ ] **Step 4: Run tests + build**

Run: `cd frontend && npx vitest run src/components/ProjectDossier.test.tsx && npm run build`
Expected: 4 tests PASS; build sin errores nuevos (baseline `useAgentStream` no cuenta).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/api.ts \
        frontend/src/components/ProjectDossier.tsx frontend/src/components/ProjectDossier.module.css \
        frontend/src/components/ProjectDossier.test.tsx frontend/src/components/ValleysView.tsx \
        frontend/src/components/ValleysView.module.css
git commit -m "feat(frontend): panel ProjectDossier + botón en Valles — hechos citados, sin veredicto (dossier C §4)"
```

---

### Task 7: Gate final + GROW

**Files:**
- Modify: `.mex/ROUTER.md` (Current Project State)
- Create: `.mex/patterns/generar-un-dossier.md` + fila en `.mex/patterns/INDEX.md`

- [ ] **Step 1: Suite del dossier verde**

Run: `python -m pytest tests/test_exa_client.py tests/test_dossier_schema.py tests/test_dossier_build.py tests/test_project_dossiers_migration.py tests/test_dossier_api.py -q`
Expected: todo verde.

- [ ] **Step 2: Gate rápido global**

Run: `python -m pytest tests/ -m "not network" -q`
Expected: verde. Si algo ajeno falla, verificá que no toque `research/`, `api/dossier.py`, `db/dossiers.py` ni el bloque `con_bc` de schema; si es flake preexistente, DONE_WITH_CONCERNS con el nombre.

- [ ] **Step 3: Frontend build**

Run: `cd frontend && npm run build`
Expected: limpio salvo la baseline conocida.

- [ ] **Step 4: GROW — pattern + ROUTER + log**

Crear `.mex/patterns/generar-un-dossier.md` con el formato de los patterns existentes (Purpose / When / Steps / Gotchas / Verify Checklist), en español. Gotchas clave: (1) es observabilidad de hechos externos, NO juicio — sin veredicto, el esquema no tiene casillero de opinión; (2) `opaco` (buscó, no encontró) ≠ `no_disponible` (fallo técnico) — nunca confundir; (3) candado anti-alucinación: cada cita ancla a una URL que Exa devolvió; (4) la red (Exa+DeepSeek) corre FUERA de la tx; (5) `EXA_API_KEY` fail-closed; (6) no se cachean los `no_disponible`. Añadir fila a `.mex/patterns/INDEX.md`: `| Generar un dossier de due-diligence de un proyecto | [generar-un-dossier.md](generar-un-dossier.md) |`.

En `.mex/ROUTER.md` §Working añadir: `- Dossier C: due-diligence de hechos citados (Exa + DeepSeek extracción), sin veredicto, caché TTL 7d, botón en Valles.`

Run: `mex log "Dossier C: hechos citados Exa+DeepSeek con candado anti-alucinación + opaco/no_disponible; observabilidad externa sin veredicto (frontera Voronov)"` (si `mex` no está en PATH, anexar la línea JSONL equivalente a `.mex/events/decisions.jsonl`, timestamp literal "2026-06-12T00:00:00Z").

- [ ] **Step 5: Commit**

```bash
git add .mex/
git commit -m "docs(mex): pattern generar-un-dossier + estado dossier C en ROUTER"
```

---

## Configuración + validación (manual, post-merge)

```bash
# 1. EXA_API_KEY en el .env LOCAL para desarrollo (NO commitear el .env).
# 2. En prod (trading.sdar.dev), al desplegar: añadir EXA_API_KEY al EnvironmentFile
#    del servicio trading-spacial.service, restart, y validar vía /proc/<pid>/environ.
# 3. Validación end-to-end con la key real:
curl -s "http://localhost:8000/dossier/ADAUSDT" | python -m json.tool   # genera + cachea
curl -s "http://localhost:8000/dossier/ADAUSDT?refresh=true" | python -m json.tool  # regenera
# Revisar: hechos con sus fuentes (URLs reales), un símbolo opaco muestra estado_general=opaco
# con no_encontrado_en, y Exa caído da no_disponible (no opaco). En el dashboard, abrir
# Valles → botón Dossier sobre una candidata → panel con hechos citados, sin texto de recomendación.
```
