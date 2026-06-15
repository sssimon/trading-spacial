from unittest.mock import patch
import api.levels as levels_mod


def test_levels_ok_carries_frescura():
    bars = [{"open_time": 0, "open": 1.0, "high": 1.1, "low": 0.9,
             "close": 1.0, "volume": 10.0, "quote_volume": 10.0}]
    with patch.object(levels_mod, "_fetch_daily_bars", return_value=bars), \
         patch.object(levels_mod, "_fetch_live_price", return_value=1.0), \
         patch.object(levels_mod, "detect_levels", return_value=[]), \
         patch.object(levels_mod, "locate_price",
                      return_value={"dentro_de": None, "techo": None, "piso": None}):
        out = levels_mod.get_levels("BTCUSDT")
    assert out["estado"] == "ok"
    assert "frescura" in out
    assert out["frescura"]["estado"] == "fresco"
    assert out["price_live"] == 1.0


def test_levels_no_disponible_carries_frescura():
    with patch.object(levels_mod, "_fetch_daily_bars",
                      side_effect=levels_mod.BinanceUnavailable("down")):
        out = levels_mod.get_levels("BTCUSDT")
    assert out["estado"] == "no_disponible"
    assert out["frescura"]["estado"] == "muerto"
    assert out["zonas"] == []
    assert out.get("generated_at") is None   # campo top-level previo intacto (aditivo)
