import pytest


def test_valles_microprompt_exists_and_states_doctrine():
    from api.agent.prompts.surfaces import for_surface
    p = for_surface("valles")
    low = p.lower()
    assert "valle" in low
    assert "no" in low and ("veredicto" in low or "decid" in low)


def test_valles_system_blocks_build():
    from api.agent.prompts import build_system_blocks
    blocks = build_system_blocks("valles")
    assert blocks and any("valle" in b.lower() for b in blocks)


def test_valles_default_is_deepseek_chat():
    from api.agent.models import default_model_for_surface
    assert default_model_for_surface("valles") == "deepseek-chat"


def test_valles_forbids_reasoner():
    from api.agent.models import assert_model_allowed_for_surface
    with pytest.raises(ValueError):
        assert_model_allowed_for_surface("valles", "deepseek-reasoner")
    # un surface normal sí permite reasoner:
    assert_model_allowed_for_surface("kill_switch", "deepseek-reasoner")
