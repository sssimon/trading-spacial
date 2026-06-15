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
