import os
import sqlite3
import pytest
from tools.funding_carry import ingest
from tools.funding_carry import simulate
from tools.funding_carry.constants import NOTIONAL

def test_parse_funding_rows_maps_known_schemas():
    # Binance Vision fundingRate CSV header variant: calc_time,funding_interval_hours,last_funding_rate
    header = ["calc_time", "funding_interval_hours", "last_funding_rate"]
    rows = [["1704067200000", "8", "0.0001"], ["1704096000000", "8", "-0.0002"]]
    out = ingest.parse_funding_rows(header, rows)
    assert out == [(1704067200000, 0.0001), (1704096000000, -0.0002)]

def test_parse_funding_rows_api_schema():
    # fapi JSON-derived rows: fundingTime, fundingRate, markPrice
    header = ["fundingTime", "fundingRate", "markPrice"]
    rows = [["1704067200000", "0.0001", "42000.0"]]
    out = ingest.parse_funding_rows(header, rows)
    assert out == [(1704067200000, 0.0001)]


def test_funding_accrual_short_receives_when_positive():
    # short perp RECEIVES funding when rate>0. units=u, mark=const M.
    # funding_pnl = sum(rate_i * M * u)
    funding = [(0, 0.0001), (28_800_000, -0.0002), (57_600_000, 0.0003)]  # 8h apart
    u, mark = 2.0, 100.0
    pnl = simulate.funding_pnl(funding, units=u, mark_price=mark)
    assert pnl == pytest.approx((0.0001 - 0.0002 + 0.0003) * 100.0 * 2.0)

def test_basis_pnl_short_perp_long_spot():
    # basis = perp - spot. delta-neutral pnl = -u*(basis_exit - basis_entry).
    pnl = simulate.basis_pnl(spot_entry=100.0, perp_entry=101.0,
                             spot_exit=100.0, perp_exit=100.5, units=3.0)
    # basis_entry=1.0, basis_exit=0.5 -> -3*(0.5-1.0)=+1.5 (convergence favorable)
    assert pnl == pytest.approx(1.5)


def test_recost_four_legs_positive():
    cost = simulate.recost_four_legs(symbol="BTCUSDT", units=0.25, spot_price=40_000.0,
                                     perp_price=40_050.0, liq=5_000_000.0, holding_hours=720.0)
    assert cost > 0.0          # 4 fills each charged v3
    assert cost < NOTIONAL

def test_carry_for_symbol_assembles_net():
    # a synthetic symbol: constant +0.01%/8h funding for ~30 days, flat basis.
    funding = [(i * 28_800_000, 0.0001) for i in range(90)]  # 90 * 8h = 30 days
    rec = simulate.carry_for_symbol(
        symbol="BTCUSDT",
        funding=funding, spot_entry=40_000.0, spot_exit=40_000.0,
        perp_entry=40_000.0, perp_exit=40_000.0, liq=5_000_000.0)
    assert set(rec) >= {"symbol", "funding_pnl", "basis_pnl", "cost_v3", "net",
                        "net_return", "n_funding", "window_hours"}
    # pinned: 90 events * 0.0001 * mark(40000) * units(10000/40000=0.25) = 90.0; flat basis = 0.
    assert rec["funding_pnl"] == pytest.approx(90.0)
    assert rec["basis_pnl"] == pytest.approx(0.0)
    assert rec["net"] == pytest.approx(rec["funding_pnl"] + rec["basis_pnl"] - rec["cost_v3"])

def test_carry_for_symbol_raises_on_nan_price():
    funding = [(0, 0.0001), (28_800_000, 0.0001)]
    with pytest.raises(ValueError):
        simulate.carry_for_symbol(
            symbol="BTCUSDT", funding=funding, spot_entry=float("nan"),
            spot_exit=40_000.0, perp_entry=40_000.0, perp_exit=40_000.0, liq=5e6)


def _mk_funding_db(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE funding(symbol TEXT, funding_time_ms INTEGER, funding_rate REAL)")
    con.execute("CREATE TABLE perp_klines(symbol TEXT, open_time INTEGER, close REAL)")
    for i in range(10):
        con.execute("INSERT INTO funding VALUES('BTCUSDT', ?, 0.0001)", (i * 28_800_000,))
        con.execute("INSERT INTO perp_klines VALUES('BTCUSDT', ?, 100.0)", (i * 3_600_000,))
    con.commit(); con.close()

def test_load_funding_window(tmp_path):
    db = str(tmp_path / "f.db"); _mk_funding_db(db)
    rows = simulate.load_funding(db, "BTCUSDT", 0, 9 * 28_800_000)
    assert len(rows) == 10
    assert rows[0] == (0, 0.0001)

def test_perp_price_at(tmp_path):
    db = str(tmp_path / "f.db"); _mk_funding_db(db)
    assert simulate.perp_price_at(db, "BTCUSDT", 5 * 3_600_000) == pytest.approx(100.0)
