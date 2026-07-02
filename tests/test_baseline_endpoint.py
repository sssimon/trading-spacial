from unittest.mock import patch
import api.baseline as baseline_mod
from scanner.baseline.ensemble import BaselineEnsemble


def _ensemble():
    uni = [f"S{i}" for i in range(60)]
    e = BaselineEnsemble(n_seeds=6)
    bars = {s: {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0} for s in uni}
    e.advance_day("2026-07-02", bars, uni)
    return e


def test_baseline_fresco_carries_frescura_no_picks():
    with patch.object(baseline_mod, "load",
                      return_value=(_ensemble(), "2026-07-02T00:00:00Z")):
        out = baseline_mod.get_baseline()
    assert "frescura" in out
    assert out["frescura"]["estado"] in ("fresco", "rancio")  # depende de la hora del run
    assert out["n_seeds"] == 6
    assert "nota" in out                      # copy anti-veredicto presente
    assert "symbol" not in str(out).lower()   # picks NO surfaceados


def test_baseline_muerto_when_no_state():
    with patch.object(baseline_mod, "load", return_value=(None, None)):
        out = baseline_mod.get_baseline()
    assert out["frescura"]["estado"] == "muerto"
