"""Tests del P&L net-of-v3w por entrada (puro, costo inyectado).

El costo v3w real (celda 4) se inyecta como callable para testear la lógica de
simulación sin tocar la calibración ni la DB."""
from tools.valle_calidad_probe.simulate import simulate_entry


def _bar(t, close):
    return {"open_time": t, "open": close, "high": close * 1.01,
            "low": close * 0.99, "close": close, "volume": 1.0,
            "quote_volume": 1_000_000.0}


def _cost_zero(*a, **k):
    return 0.0   # costo cero → net == gross, aísla la aritmética de retorno


def test_long_hold_20d_retorno_y_pnl():
    # Precio sube de 1.0 a 1.10 en 20 días → gross +10%.
    bars = [_bar(i * 86_400_000, 1.0 + 0.10 * (i / 20.0)) for i in range(40)]
    e = simulate_entry(bars, entry_idx=0, tipo="valle", episode_id=0,
                       median_dollar_vol=5_000_000.0, fill_cost=_cost_zero)
    assert abs(e["gross_ret"] - 0.10) < 1e-9
    assert abs(e["net_ret"] - 0.10) < 1e-9          # costo cero
    assert abs(e["pnl_usd"] - 100.0) < 1e-6          # 1000 × 0.10
    assert e["forced_close"] is False
    assert e["tipo"] == "valle" and e["episode_id"] == 0
    assert e["entry_ts"] == 0


def test_delisting_antes_de_H_fuerza_cierre():
    # Solo 10 barras tras la entrada (< 20) → cierre forzado al último precio.
    bars = [_bar(i * 86_400_000, 1.0 + 0.05 * (i / 10.0)) for i in range(11)]
    e = simulate_entry(bars, entry_idx=0, tipo="no_valle", episode_id=1,
                       median_dollar_vol=5_000_000.0, fill_cost=_cost_zero)
    assert e["forced_close"] is True
    assert abs(e["gross_ret"] - 0.05) < 1e-9          # salió al último (idx 10)


def test_costo_resta_del_neto():
    bars = [_bar(i * 86_400_000, 1.0) for i in range(40)]   # precio plano → gross 0
    # Costo $3 por fill → 2 fills (entrada+salida) = $6 → net_pnl = -6, net_ret = -0.006.
    e = simulate_entry(bars, entry_idx=0, tipo="valle", episode_id=0,
                       median_dollar_vol=5_000_000.0, fill_cost=lambda *a, **k: 3.0)
    assert abs(e["pnl_usd"] - (-6.0)) < 1e-6
    assert abs(e["net_ret"] - (-0.006)) < 1e-9
