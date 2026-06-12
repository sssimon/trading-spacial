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
