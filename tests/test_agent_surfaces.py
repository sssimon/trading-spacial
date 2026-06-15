import pytest


def test_valles_default_is_deepseek_chat():
    from api.agent.models import default_model_for_surface
    assert default_model_for_surface("valles") == "deepseek-chat"


def test_valles_forbids_reasoner():
    from api.agent.models import assert_model_allowed_for_surface
    with pytest.raises(ValueError):
        assert_model_allowed_for_surface("valles", "deepseek-reasoner")
    # un surface normal sí permite reasoner:
    assert_model_allowed_for_surface("kill_switch", "deepseek-reasoner")
