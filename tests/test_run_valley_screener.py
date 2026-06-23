"""Tests del orquestador del screener (Vista Valles A §5.2, §6).

La red se mockea por completo: universo + fetch de klines."""
import json
from unittest.mock import MagicMock, patch

import tools.run_valley_screener as rvs
from tools.run_valley_screener import build_snapshot


def _fake_resp(status, payload):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = payload
    return m


def _kline_rows(n, close, quote_vol):
    """Filas crudas de Binance: [open_time, o, h, l, c, vol, close_time,
    quote_vol, ...]. La barra diaria usa índices 0,1,2,3,4,5,7."""
    rows = []
    for i in range(n):
        rows.append([
            i * 86_400_000, str(close), str(close * 1.03), str(close * 0.97),
            str(close), str(quote_vol / close), 0, str(quote_vol),
            0, "0", "0", "0",
        ])
    return rows


def test_snapshot_incluye_candidata_viva_y_omite_muerta():
    universo = ["LIVEUSDT", "DEADUSDT"]

    def fake_klines(symbol, **kw):
        if symbol == "LIVEUSDT":
            # viva + en la PARTE BAJA de su rango: alto y estable, cae al piso al final.
            rows = []
            for i in range(150):
                c = 1.20 if i < 120 else 1.20 - 0.28 * ((i - 120) / 29.0)
                rows.append([i * 86_400_000, str(c), str(c * 1.005), str(c * 0.995),
                             str(c), str(2_000_000.0 / c), 0, str(2_000_000.0),
                             0, "0", "0", "0"])
            return rows
        return _kline_rows(150, 1.0, 50_000.0)

    with patch("tools.run_valley_screener.list_live_usdt_spot", return_value=universo), \
         patch("tools.run_valley_screener._fetch_daily_klines", side_effect=fake_klines), \
         patch("tools.run_valley_screener._fetch_dominance", return_value=0.55):
        cand_snap, _regime_snap = build_snapshot()

    syms = [c["symbol"] for c in cand_snap["candidates"]]
    assert "LIVEUSDT" in syms
    assert "DEADUSDT" not in syms
    assert cand_snap["coverage"]["universe"] == 2
    assert cand_snap["coverage"]["evaluated"] == 2
    assert cand_snap["coverage"]["complete"] is True
    assert "generated_at" in cand_snap


def test_fallo_de_un_simbolo_no_tumba_el_run_y_marca_cobertura():
    universo = ["GOODUSDT", "BROKENUSDT"]

    def fake_klines(symbol, **kw):
        if symbol == "BROKENUSDT":
            raise RuntimeError("kline fetch boom")
        return _kline_rows(150, 1.0, 2_000_000.0)

    with patch("tools.run_valley_screener.list_live_usdt_spot", return_value=universo), \
         patch("tools.run_valley_screener._fetch_daily_klines", side_effect=fake_klines), \
         patch("tools.run_valley_screener._fetch_dominance", return_value=0.55):
        cand_snap, _regime_snap = build_snapshot()

    assert cand_snap["coverage"]["universe"] == 2
    assert cand_snap["coverage"]["evaluated"] == 1
    assert cand_snap["coverage"]["complete"] is False


def test_build_snapshot_acumula_regimen_y_excluye_btc():
    universo = ["BTCUSDT", "ALT1USDT", "ALT2USDT"]

    def fake_klines(symbol, **kw):
        # BTC plano; alts suben fuerte (above_sma50, ret alto) → outperf alts.
        if symbol == "BTCUSDT":
            return _kline_rows(60, 1.0, 5_000_000.0)
        return _kline_rows(60, 1.0, 2_000_000.0)[:-1] + [
            [59 * 86_400_000, "1.30", "1.34", "1.26", "1.30", "1538461.0", 0,
             "2000000.0", 0, "0", "0", "0"]]

    with patch("tools.run_valley_screener.list_live_usdt_spot", return_value=universo), \
         patch("tools.run_valley_screener._fetch_daily_klines", side_effect=fake_klines), \
         patch("tools.run_valley_screener._fetch_dominance", return_value=0.45):
        _cand_snap, regime_snap = build_snapshot()

    reg = regime_snap["regime"]
    assert regime_snap["dominancia_fetch"]["ok"] is True
    assert reg["n_alts_evaluadas"] == 2                      # BTC excluido
    assert reg["componentes"]["dominancia_btc"]["estado"] == "fresco"
    assert reg["votos"]["vivos"] == 3
    assert regime_snap["generated_at"] == _cand_snap["generated_at"]   # mismo cierre


def test_regenerate_escribe_alt_season_atomicamente(tmp_path, monkeypatch):
    monkeypatch.setattr(rvs, "_OUTPUT", str(tmp_path / "cand.json"))
    monkeypatch.setattr(rvs, "_ALT_SEASON_OUTPUT", str(tmp_path / "alt_season.json"))
    universo = ["BTCUSDT", "ALT1USDT"]

    with patch("tools.run_valley_screener.list_live_usdt_spot", return_value=universo), \
         patch("tools.run_valley_screener._fetch_daily_klines",
               side_effect=lambda s, **k: _kline_rows(60, 1.0, 2_000_000.0)), \
         patch("tools.run_valley_screener._fetch_dominance", return_value=0.50):
        cand_snap, regime_snap = rvs.regenerate(pause_s=0.0)

    assert (tmp_path / "cand.json").exists()
    assert (tmp_path / "alt_season.json").exists()
    written = json.loads((tmp_path / "alt_season.json").read_text(encoding="utf-8"))
    assert written["regime"]["estado"] in ("alts", "mixto", "btc")
    assert "dominancia_fetch" in written


def test_fetch_dominance_ok():
    payload = {"data": {"market_cap_percentage": {"btc": 53.9}}}
    with patch("tools.run_valley_screener.requests.get", return_value=_fake_resp(200, payload)):
        assert abs(rvs._fetch_dominance() - 0.539) < 1e-9


def test_fetch_dominance_shape_inesperado_es_none():
    with patch("tools.run_valley_screener.requests.get", return_value=_fake_resp(200, {"data": {}})):
        assert rvs._fetch_dominance() is None


def test_fetch_dominance_fuera_de_rango_es_none():
    payload = {"data": {"market_cap_percentage": {"btc": 150.0}}}  # >100% → 1.5
    with patch("tools.run_valley_screener.requests.get", return_value=_fake_resp(200, payload)):
        assert rvs._fetch_dominance() is None


def test_fetch_dominance_error_de_red_es_none():
    import requests
    with patch("tools.run_valley_screener.requests.get", side_effect=requests.RequestException("boom")):
        assert rvs._fetch_dominance() is None


# ---------------------------------------------------------------------------
# Tests de aplicar_gate_candidatas (Task 5)
# ---------------------------------------------------------------------------

def _candidatas():
    """2 candidatas alt de juguete."""
    return [{"symbol": "ADAUSDT", "price": 1.0}, {"symbol": "DOGEUSDT", "price": 0.1}]


def test_disabled_byte_identico(monkeypatch):
    monkeypatch.setattr(rvs, "load_config", lambda: {"regime_gate": {"enabled": False}})
    snap = rvs.aplicar_gate_candidatas(_candidatas(), estado="btc", votos_vivos=3)
    assert snap["candidates"] == _candidatas()      # sin tocar
    assert "candidatas_ocultas" not in snap          # sin campos nuevos


def test_enabled_btc_esconde(monkeypatch):
    monkeypatch.setattr(rvs, "load_config",
                        lambda: {"regime_gate": {"enabled": True, "umbral_overrides": {}}})
    snap = rvs.aplicar_gate_candidatas(_candidatas(), estado="btc", votos_vivos=3)
    assert snap["candidates"] == []                  # todas escondidas
    assert len(snap["candidatas_ocultas"]) == 2


def test_enabled_mixto_empate_atenua(monkeypatch):
    monkeypatch.setattr(rvs, "load_config",
                        lambda: {"regime_gate": {"enabled": True, "umbral_overrides": {}}})
    snap = rvs.aplicar_gate_candidatas(_candidatas(), estado="mixto", votos_vivos=3)
    assert len(snap["candidates"]) == 2 and all(c["clima_ambiguo"] for c in snap["candidates"])
    assert snap.get("candidatas_ocultas", []) == []
