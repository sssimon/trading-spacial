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
