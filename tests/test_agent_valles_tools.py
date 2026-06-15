import pytest


def test_lens_schemas_require_symbol():
    from api.agent.tools.schemas import GetValleyEvalIn, GetLevelsIn, GetDossierIn
    for Cls in (GetValleyEvalIn, GetLevelsIn, GetDossierIn):
        with pytest.raises(Exception):
            Cls()                      # symbol es obligatorio
        ok = Cls(symbol="BTCUSDT")
        assert ok.symbol == "BTCUSDT"


def test_lens_schemas_registered():
    from api.agent.tools.schemas import TOOL_INPUT_SCHEMAS
    for name in ("get_valley_eval", "get_levels", "get_dossier"):
        assert name in TOOL_INPUT_SCHEMAS


def test_valles_surface_exposes_only_3_read_tools():
    from api.agent.tools.registry import tools_for_surface
    names = {t.name for t in tools_for_surface("valles")}
    assert names == {"get_valley_eval", "get_levels", "get_dossier"}


def test_no_propose_tool_touches_valles():
    from api.agent.tools.registry import TOOL_CATALOG
    for t in TOOL_CATALOG:
        if t.name.startswith("propose_"):
            assert "valles" not in t.surfaces, f"{t.name} no debe tocar valles"


def test_get_valley_eval_handler_passes_payload(monkeypatch):
    import api.agent.tools.handlers as h
    monkeypatch.setattr("api.valleys.get_valley_eval",
                        lambda s: {"symbol": s, "estado": "ok", "candidata": True,
                                   "frescura": {"estado": "fresco"}})
    out = h.get_valley_eval_lens(tenant_id=1, symbol="BTCUSDT")
    assert out["candidata"] is True
    assert out["frescura"]["estado"] == "fresco"


def test_get_levels_handler_no_disponible_passthrough(monkeypatch):
    import api.agent.tools.handlers as h
    monkeypatch.setattr("api.levels.get_levels",
                        lambda s: {"symbol": s, "estado": "no_disponible",
                                   "frescura": {"estado": "muerto"}})
    out = h.get_levels_lens(tenant_id=1, symbol="BTCUSDT")
    assert out["estado"] == "no_disponible"


def test_lens_handler_rejects_empty_symbol():
    import api.agent.tools.handlers as h
    out = h.get_dossier_lens(tenant_id=1, symbol="")
    assert out == {"error": "not_found"}


def test_lens_handlers_registered():
    from api.agent.tools.handlers import TOOL_HANDLERS
    for name in ("get_valley_eval", "get_levels", "get_dossier"):
        assert name in TOOL_HANDLERS
