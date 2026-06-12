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
from pydantic import ValidationError

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
    "URLs. Devolvé UN JSON con EXACTAMENTE estas claves (sin agregar otras):\n"
    '{"equipo": [{"nombre": "", "rol": "", "enlaces": [], "fuente": "URL"}], '
    '"equipo_identificado": true, '
    '"presencia": {"sitio_web": {"url": "", "activo": "si|no|desconocido", "fuente": "URL"}, '
    '"github": {...}, "twitter": {...}, "telegram_discord": {...}, "whitepaper": {...}}, '
    '"actividad": {"ultimo_commit_github": {"valor": "", "fuente": "URL"}, '
    '"ultimo_release": {...}, "ultimo_post_anuncio": {...}}, '
    '"financiacion": [{"descripcion": "ronda/monto/inversores", "fecha": "", "fuente": "URL"}], '
    '"hitos": [{"descripcion": "", "fecha": "", "fuente": "URL"}]}\n'
    "Para cada hecho, `fuente` DEBE ser la URL EXACTA, copiada tal cual, de uno de "
    "los bloques `URL:` que te di. Si un hecho no está en el contenido, omitilo (no "
    "lo inventes); omití también las claves de presencia/actividad que no encuentres. "
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
    choices = r.json().get("choices") or []
    if not choices:
        raise RuntimeError("deepseek vacío: choices ausente o vacío")
    return json.loads(choices[0]["message"]["content"])


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
                url = b.get("url")
                if not url or url in url_set:
                    continue
                url_set.add(url)
                bloques.append(b)
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
    try:
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

        # ── Fix #4: observabilidad del candado (cuántos hechos fueron descartados) ──
        crudo_total = (len(crudo.get("equipo", [])) + len(crudo.get("presencia", {}))
                       + len(crudo.get("actividad", {})) + len(crudo.get("financiacion", []))
                       + len(crudo.get("hitos", [])))
        kept = len(equipo) + len(presencia) + len(actividad) + len(financiacion) + len(hitos)
        if crudo_total > kept:
            log.debug("DOSSIER_ANCLA_DESCARTO symbol=%s descartados=%d", symbol, crudo_total - kept)

    except (ValidationError, TypeError, AttributeError, KeyError) as e:
        log.warning("DOSSIER_NO_DISPONIBLE symbol=%s causa=malformado:%s", symbol, e)
        return Dossier(symbol=symbol, estado_general="no_disponible")

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
