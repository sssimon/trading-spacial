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
