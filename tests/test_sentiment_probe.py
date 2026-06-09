"""Tests offline de las funciones puras del sondeo C1 (F&G). Cero data real."""
from __future__ import annotations

import pandas as pd

from tools.sentiment_probe.probe import (
    assign_episodes,
    block_bootstrap_ci,
    cost_bps_for,
    gate,
    signal_for_fng,
    trade_net_ret,
    zone_for_fng,
)


def test_signal_y_zona():
    assert signal_for_fng(10) == 1 and zone_for_fng(10) == "F"   # extreme fear -> long
    assert signal_for_fng(25) == 1                                # borde fear inclusivo
    assert signal_for_fng(50) == 0 and zone_for_fng(50) == "N"
    assert signal_for_fng(75) == -1                               # borde greed inclusivo
    assert signal_for_fng(90) == -1 and zone_for_fng(90) == "G"   # extreme greed -> short


def test_cost_bps_por_tier_net_v3():
    # RT = 2*half_spread + 2*fee; funding = funding_8h * floor(120/8=15)
    rt, fund, tot = cost_bps_for("BTCUSDT")   # major: rt 13, fund 1*15=15
    assert (rt, fund, tot) == (13.0, 15.0, 28.0)
    rt, fund, tot = cost_bps_for("ADAUSDT")   # mid: rt 18, fund 2*15=30
    assert (rt, fund, tot) == (18.0, 30.0, 48.0)
    rt, fund, tot = cost_bps_for("PENDLEUSDT")  # small: rt 30, fund 5*15=75
    assert (rt, fund, tot) == (30.0, 75.0, 105.0)


def test_trade_net_ret_long_y_short():
    # long, +2% gross, costo 28 bps -> 0.02 - 0.0028 = 0.0172
    assert round(trade_net_ret(1, 0.02, 28.0), 6) == 0.0172
    # short, mercado SUBE +2% (malo para short) -> -0.02 - 0.0028 = -0.0228
    assert round(trade_net_ret(-1, 0.02, 28.0), 6) == -0.0228
    # short, mercado BAJA -2% (bueno para short) -> +0.02 - 0.0028 = 0.0172
    assert round(trade_net_ret(-1, -0.02, 28.0), 6) == 0.0172


def test_assign_episodes_runs_contiguos():
    dates = [pd.Timestamp(f"2021-01-0{i}") for i in range(1, 7)]
    zones = ["N", "F", "F", "N", "G", "F"]
    ep = assign_episodes(dates, zones)
    # d2,d3 = mismo episodio (F contiguo); d5 = nuevo (G); d6 = nuevo (F tras G)
    assert ep[dates[1]] == ep[dates[2]] == 1
    assert dates[3] not in ep            # neutral, sin episodio
    assert ep[dates[4]] == 2
    assert ep[dates[5]] == 3


def test_block_bootstrap_determinista_y_cuenta_episodios():
    values = [1.0, 1.1, 0.9, 5.0, 5.2]   # 2 episodios: {1,1,1} y {2,2}
    episodes = [1, 1, 1, 2, 2]
    a = block_bootstrap_ci(values, episodes, n_iter=2000, seed=42)
    b = block_bootstrap_ci(values, episodes, n_iter=2000, seed=42)
    assert a == b                         # mismo seed -> mismo CI (determinista)
    assert a[2] == 2                      # n_episodios = 2, no 5 trades
    assert a[0] <= a[1]


def test_gate_pre_registrado():
    assert gate(50.0, 10.0, 90.0, n_episodes=15) == "PASS"        # mean>0 y CI excluye cero
    assert gate(50.0, -5.0, 90.0, n_episodes=15) == "FAIL"        # CI incluye cero
    assert gate(-3.0, -20.0, 5.0, n_episodes=15) == "FAIL"        # mean<0
    assert gate(50.0, 10.0, 90.0, n_episodes=4) == "UNDERPOWERED"  # pocos episodios
