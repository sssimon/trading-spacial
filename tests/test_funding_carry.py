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

def test_funding_increments_stream():
    funding = [(0, 0.0001), (28_800_000, 0.0001)]
    incs = simulate.funding_increments(funding, units=2.0, mark_price=100.0)
    assert incs == [(0, pytest.approx(0.02)), (28_800_000, pytest.approx(0.02))]

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


from tools.funding_carry import evaluate

def test_gate_a_bootstrap_deterministic():
    rets = [0.05, 0.08, -0.02, 0.06, 0.04, 0.09, 0.01, 0.07]
    a = evaluate.gate_a(rets)
    b = evaluate.gate_a(rets)
    assert a["ci_lo"] == b["ci_lo"]                # seeded
    assert a["ci_lo"] <= a["mean"] <= a["ci_hi"]
    assert "pass_a" in a and "loo_min_mean" in a

def test_gate_a_pass_only_if_ci_excludes_zero():
    strong = [0.10] * 9
    weak = [0.02, -0.05, 0.03, -0.04, 0.01, 0.02, -0.03, 0.04, -0.02]
    assert evaluate.gate_a(strong)["pass_a"] is True
    assert evaluate.gate_a(weak)["pass_a"] is False

def test_gate_b_max_drawdown():
    interval_pnls = [10.0, -6.0, 8.0]   # equity 0->10->4->12; max DD = 6
    b1 = evaluate.gate_b1(interval_pnls)
    assert b1["max_drawdown"] == pytest.approx(6.0)
    assert b1["worst_interval"] == pytest.approx(-6.0)

def test_gate_b_synthetic_shock_kills_thin_carry():
    thin = evaluate.gate_b2(mean_net_return=0.03)   # bleed 5*3*0.005=0.075 -> 0.03-0.075<0
    assert thin["pass_b2"] is False
    fat = evaluate.gate_b2(mean_net_return=0.20)
    assert fat["pass_b2"] is True
    assert fat["shock_bleed"] == pytest.approx(0.075)

def test_verdict_requires_both_gates():
    a_pass = {"pass_a": True}; a_fail = {"pass_a": False}
    b_pass = {"pass_b2": True}; b_fail = {"pass_b2": False}
    assert evaluate.verdict(a_pass, b_pass)["verdict"] == "PASS"
    assert evaluate.verdict(a_fail, b_pass)["verdict"] == "FAIL"
    assert evaluate.verdict(a_pass, b_fail)["verdict"] == "FAIL"

def test_required_artifact_keys():
    from tools.funding_carry import run
    rec = {"symbol": "BTCUSDT", "net_return_annual": 0.1, "net": 1000.0,
           "funding_pnl": 1200.0, "basis_pnl": 0.0, "cost_v3": 200.0}
    assert run.REQUIRED_SYMBOL_KEYS <= set(rec.keys())

def test_perp_mark_series_lookup(tmp_path):
    db = str(tmp_path / "f.db"); _mk_funding_db(db)   # perp close=100 at each hour
    marks = simulate.perp_mark_series(db, "BTCUSDT", [0, 28_800_000, 57_600_000])
    assert marks == [pytest.approx(100.0)] * 3

def test_funding_pnl_per_interval_uses_each_mark():
    funding = [(0, 0.0001), (1, -0.0002)]
    marks = [100.0, 200.0]    # mark doubles on the 2nd settlement
    pnl = simulate.funding_pnl_per_interval(funding, marks=marks, units=2.0)
    assert pnl == pytest.approx(0.0001 * 100.0 * 2.0 + (-0.0002) * 200.0 * 2.0)

from tools.funding_carry import kill_rule

def test_simulate_no_kill_one_tramo():
    funding = [(i, 0.0001) for i in range(10)]
    marks = [100.0] * 10
    r = kill_rule.simulate_no_kill(funding, marks=marks, units=2.0, rt_cost=5.0)
    assert r["n_tramos"] == 1
    assert r["churn_cost"] == pytest.approx(5.0)
    assert r["net"] == pytest.approx(sum(0.0001 * 100.0 * 2.0 for _ in range(10)) - 5.0)
    assert len(r["equity_curve"]) == 10

def test_simulate_with_kill_exits_on_K_negatives():
    funding = [(i, 0.0001) for i in range(3)] + [(i + 3, -0.0002) for i in range(3)] \
              + [(i + 6, 0.0001) for i in range(2)]
    marks = [100.0] * 8
    r = kill_rule.simulate_with_kill(funding, marks=marks, units=2.0, rt_cost=5.0, k=3)
    assert r["n_tramos"] == 2
    assert r["n_kills"] == 1
    assert r["churn_cost"] == pytest.approx(10.0)
    assert len(r["equity_curve"]) == 8                 # one point per settlement, kills included
    # 3 pos (+0.06) + 3 neg accrued through the kill (-0.12) + 1 post-reentry pos (+0.02) = -0.04
    # gross; the re-entry tick (i=6) is NOT collected; net = gross - churn 10.0
    assert r["net"] == pytest.approx(-0.04 - 10.0)

def test_with_kill_no_negatives_equals_no_kill():
    funding = [(i, 0.0001) for i in range(10)]
    marks = [100.0] * 10
    wk = kill_rule.simulate_with_kill(funding, marks=marks, units=2.0, rt_cost=5.0, k=3)
    nk = kill_rule.simulate_no_kill(funding, marks=marks, units=2.0, rt_cost=5.0)
    assert wk["net"] == pytest.approx(nk["net"])


def test_kill_vs_nokill_bootstrap_deterministic():
    wk = [0.06, 0.05, 0.07, 0.04, 0.08, 0.05, 0.06, 0.05, 0.07]
    nk = [0.05, 0.05, 0.06, 0.04, 0.07, 0.05, 0.05, 0.05, 0.06]
    a = evaluate.kill_vs_nokill(wk, nk)
    b = evaluate.kill_vs_nokill(wk, nk)
    assert a["ci_lo"] == b["ci_lo"]
    assert a["mean_delta"] == pytest.approx(sum(w - n for w, n in zip(wk, nk)) / len(wk))

def test_inject_shocks_subtracts_worst_points():
    eq = [1.0, 2.0, 3.0, 4.0, 5.0]
    final = evaluate.inject_shocks(eq, n_shocks=2, shock_loss=3.0)
    assert final == pytest.approx(5.0 - 2 * 3.0)

def test_gate_tail_requires_both():
    g = evaluate.gate_tail(with_kill_net_pooled=0.10, post_shock_net_pooled=0.02)
    assert g["pass_g1"] and g["pass_g2"] and g["verdict"] == "PASS"
    g2 = evaluate.gate_tail(with_kill_net_pooled=0.10, post_shock_net_pooled=-0.01)
    assert g2["verdict"] == "FAIL"
    g3 = evaluate.gate_tail(with_kill_net_pooled=-0.01, post_shock_net_pooled=0.02)
    assert g3["verdict"] == "FAIL"

def test_run_kill_required_keys():
    from tools.funding_carry import run_kill
    rec = {"symbol": "BTCUSDT", "net_with_kill": 0.06, "net_no_kill": 0.05,
           "n_kills": 1, "max_dd": 100.0, "churn_cost": 50.0}
    assert run_kill.REQUIRED_KILL_KEYS <= set(rec.keys())

def test_shadow_constants_frozen():
    from tools.funding_carry import constants as C
    # The 9-symbol universe is exactly the verdict's symbols_used (LINK/SOL dropped).
    assert C.SHADOW_SYMBOLS == (
        "BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT",
        "UNIUSDT", "XLMUSDT", "RUNEUSDT", "PENDLEUSDT",
    )
    assert "LINKUSDT" not in C.SHADOW_SYMBOLS and "SOLUSDT" not in C.SHADOW_SYMBOLS
    assert C.DECAY_CI_LO == 0.0502           # backtest gate_a ci_lo (in-sample anchor)
    assert C.SHADOW_VERSION == "v0.1"
    assert C.FAPI_MARK_KLINES.startswith("https://fapi.binance.com")
    assert C.FAPI_SPOT.startswith("https://")
    assert C.DECAY_WEEKS_W >= 1 and C.DECAY_KILL_N >= 1
    assert C.FUNDING_FETCH_LIMIT >= 100


def test_parse_fapi_funding_rows():
    from tools.funding_carry.live_ingest import parse_fapi_funding
    # FAPI /fapi/v1/fundingRate returns a JSON list of dicts.
    payload = [
        {"symbol": "BTCUSDT", "fundingTime": 1700000000000, "fundingRate": "0.0001"},
        {"symbol": "BTCUSDT", "fundingTime": 1700028800000, "fundingRate": "-0.00005"},
    ]
    rows = parse_fapi_funding(payload)
    assert rows == [(1700000000000, 0.0001), (1700028800000, -0.00005)]


def test_fetch_recent_funding_failsoft(monkeypatch):
    from tools.funding_carry import live_ingest
    def boom(url, **kw):
        raise OSError("network down")
    monkeypatch.setattr(live_ingest, "_get_json", boom)
    # Fail-soft: a down symbol returns [] (logged), never raises.
    assert live_ingest.fetch_recent_funding("BTCUSDT", limit=10) == []


def test_parse_mark_klines():
    from tools.funding_carry.live_ingest import parse_mark_klines
    # FAPI markPriceKlines: [openTime, open, high, low, close, ...]; we keep (openTime, close).
    payload = [
        [1700000000000, "100.0", "101.0", "99.0", "100.5", "0", 0, "0", 0, "0", "0", "0"],
        [1700003600000, "100.5", "102.0", "100.0", "101.2", "0", 0, "0", 0, "0", "0", "0"],
    ]
    assert parse_mark_klines(payload) == [(1700000000000, 100.5), (1700003600000, 101.2)]


def test_append_perp_klines_idempotent(tmp_path):
    from tools.funding_carry import live_ingest
    db = str(tmp_path / "f.db")
    import sqlite3
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE perp_klines(symbol TEXT, open_time INTEGER, close REAL,"
                    " PRIMARY KEY(symbol, open_time))")
    rows = [(1700000000000, 100.5), (1700003600000, 101.2)]
    live_ingest.append_perp_klines(db, "BTCUSDT", rows)
    live_ingest.append_perp_klines(db, "BTCUSDT", rows)   # second call must not double-count
    with sqlite3.connect(db) as con:
        n = con.execute("SELECT COUNT(*) FROM perp_klines WHERE symbol='BTCUSDT'").fetchone()[0]
    assert n == 2


def test_fetch_spot_failsoft(monkeypatch):
    from tools.funding_carry import live_ingest
    monkeypatch.setattr(live_ingest, "_get_json",
                        lambda url, **kw: {"symbol": "BTCUSDT", "price": "42000.5"})
    assert live_ingest.fetch_spot("BTCUSDT") == 42000.5
    def boom(url, **kw):
        raise OSError("down")
    monkeypatch.setattr(live_ingest, "_get_json", boom)
    import math
    assert math.isnan(live_ingest.fetch_spot("BTCUSDT"))


def test_ingest_live_appends_all(tmp_path, monkeypatch):
    from tools.funding_carry import live_ingest
    db = str(tmp_path / "f.db")
    import sqlite3
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE funding(symbol TEXT, funding_time_ms INTEGER,"
                    " funding_rate REAL, PRIMARY KEY(symbol, funding_time_ms))")
        con.execute("CREATE TABLE perp_klines(symbol TEXT, open_time INTEGER, close REAL,"
                    " PRIMARY KEY(symbol, open_time))")
    monkeypatch.setattr(live_ingest, "fetch_recent_funding",
                        lambda s, limit: [(1700000000000, 0.0001)])
    monkeypatch.setattr(live_ingest, "fetch_mark_klines",
                        lambda s, interval="1h", limit=1000: [(1700000000000, 100.0)])
    summary = live_ingest.ingest_live(["BTCUSDT", "ETHUSDT"], db_path=db, limit=10)
    assert summary["BTCUSDT"]["funding"] == 1 and summary["BTCUSDT"]["klines"] == 1
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM funding").fetchone()[0] == 2


def test_decay_statistic_matches_carry_for_symbol(tmp_path):
    """The shadow statistic over a window MUST equal carry_for_symbol on that window —
    identical method, span-annualized (NOT settlement-count annualized). Guards N1."""
    import sqlite3
    from tools.funding_carry import shadow, simulate
    fdb = str(tmp_path / "f.db"); odb = str(tmp_path / "o.db")
    # Build a tiny funding.db + ohlcv.db with a known, GAPPED settlement series.
    with sqlite3.connect(fdb) as con:
        con.execute("CREATE TABLE funding(symbol TEXT, funding_time_ms INTEGER, funding_rate REAL,"
                    " PRIMARY KEY(symbol, funding_time_ms))")
        con.execute("CREATE TABLE perp_klines(symbol TEXT, open_time INTEGER, close REAL,"
                    " PRIMARY KEY(symbol, open_time))")
        H = 3_600_000
        # 4 settlements but with a GAP (skip one 8h slot) so count != span.
        times = [0, 8*H, 16*H, 40*H]
        for t in times:
            con.execute("INSERT INTO funding VALUES('BTCUSDT', ?, 0.0001)", (t,))
            con.execute("INSERT INTO perp_klines VALUES('BTCUSDT', ?, 100.0)", (t,))
    with sqlite3.connect(odb) as con:
        con.execute("CREATE TABLE ohlcv(symbol TEXT, timeframe TEXT, open_time INTEGER,"
                    " close REAL, volume REAL)")
        for t in [0, 8*3_600_000, 16*3_600_000, 40*3_600_000]:
            con.execute("INSERT INTO ohlcv VALUES('BTCUSDT','1h',?,100.0,1000.0)", (t,))
    # Reference: carry_for_symbol directly over the same window.
    funding = simulate.load_funding(fdb, "BTCUSDT", 0, 40*3_600_000)
    ref = simulate.carry_for_symbol(
        symbol="BTCUSDT", funding=funding,
        spot_entry=100.0, spot_exit=100.0, perp_entry=100.0, perp_exit=100.0,
        liq=float("nan"))
    got = shadow.symbol_window_return(
        "BTCUSDT", funding_db=fdb, ohlcv_db=odb, start_ms=0, end_ms=40*3_600_000)
    assert abs(got - ref["net_return_annual"]) < 1e-12


def test_pooled_decay_uses_gate_a(tmp_path, monkeypatch):
    from tools.funding_carry import shadow, evaluate
    monkeypatch.setattr(shadow, "symbol_window_return",
                        lambda s, **kw: {"BTCUSDT": 0.06, "ETHUSDT": 0.07}[s])
    out = shadow.pooled_decay(["BTCUSDT", "ETHUSDT"], funding_db="x", ohlcv_db="y",
                              start_ms=0, end_ms=1)
    ref = evaluate.gate_a([0.06, 0.07])
    assert out["ci_lo"] == ref["ci_lo"] and out["ci_hi"] == ref["ci_hi"]
    assert out["mean"] == ref["mean"] and out["n"] == 2


def test_decay_state_three_states():
    from tools.funding_carry.shadow import decay_state
    from tools.funding_carry.constants import DECAY_CI_LO, DECAY_KILL_N
    # ALIVE: CI sits at/above the band.
    s = decay_state(ci_lo=0.05, ci_hi=0.08, weeks_below=0)
    assert s["decay_state"] == "ALIVE" and s["weeks_below"] == 0
    # THIN: CI overlaps [0.0502, 0.0633] (ci_hi >= threshold but ci_lo below the headline).
    s = decay_state(ci_lo=0.04, ci_hi=0.06, weeks_below=0)
    assert s["decay_state"] == "THIN" and s["weeks_below"] == 0
    # Below threshold once: counter increments, not yet REFUTED.
    s = decay_state(ci_lo=0.01, ci_hi=DECAY_CI_LO - 0.001, weeks_below=0)
    assert s["weeks_below"] == 1
    assert s["decay_state"] == ("REFUTED" if DECAY_KILL_N <= 1 else "THIN")
    # N consecutive below -> REFUTED.
    s = decay_state(ci_lo=0.01, ci_hi=DECAY_CI_LO - 0.001, weeks_below=DECAY_KILL_N - 1)
    assert s["weeks_below"] == DECAY_KILL_N and s["decay_state"] == "REFUTED"


def test_decay_state_resets_counter_on_recovery():
    from tools.funding_carry.shadow import decay_state
    from tools.funding_carry.constants import DECAY_CI_LO
    s = decay_state(ci_lo=0.06, ci_hi=0.08, weeks_below=3)   # recovered above threshold
    assert s["weeks_below"] == 0 and s["decay_state"] == "ALIVE"
