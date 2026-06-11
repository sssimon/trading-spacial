"""Tests del orquestador del screener (Vista Valles A §5.2, §6).

La red se mockea por completo: universo + fetch de klines."""
from unittest.mock import patch

from tools.run_valley_screener import build_snapshot


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
            return _kline_rows(150, 1.0, 2_000_000.0)   # viva + en rango (±3%)
        return _kline_rows(150, 1.0, 50_000.0)          # volumen bajo piso

    with patch("tools.run_valley_screener.list_live_usdt_spot", return_value=universo), \
         patch("tools.run_valley_screener._fetch_daily_klines", side_effect=fake_klines):
        snap = build_snapshot()

    syms = [c["symbol"] for c in snap["candidates"]]
    assert "LIVEUSDT" in syms
    assert "DEADUSDT" not in syms
    assert snap["coverage"]["universe"] == 2
    assert snap["coverage"]["evaluated"] == 2
    assert snap["coverage"]["complete"] is True
    assert "generated_at" in snap


def test_fallo_de_un_simbolo_no_tumba_el_run_y_marca_cobertura():
    universo = ["GOODUSDT", "BROKENUSDT"]

    def fake_klines(symbol, **kw):
        if symbol == "BROKENUSDT":
            raise RuntimeError("kline fetch boom")
        return _kline_rows(150, 1.0, 2_000_000.0)

    with patch("tools.run_valley_screener.list_live_usdt_spot", return_value=universo), \
         patch("tools.run_valley_screener._fetch_daily_klines", side_effect=fake_klines):
        snap = build_snapshot()

    assert snap["coverage"]["universe"] == 2
    assert snap["coverage"]["evaluated"] == 1        # BROKENUSDT omitida
    assert snap["coverage"]["complete"] is False     # cobertura incompleta, honesta
