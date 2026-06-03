import os
import sqlite3
import statistics
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


def test_symbol_rate_is_intensive(tmp_path):
    """Pure mean(rate)*1095 — INVARIANT to window length at constant rate (the type-fix guard)."""
    import sqlite3
    from tools.funding_carry import shadow
    fdb = str(tmp_path / "f.db")
    with sqlite3.connect(fdb) as con:
        con.execute("CREATE TABLE funding(symbol TEXT, funding_time_ms INTEGER, funding_rate REAL,"
                    " PRIMARY KEY(symbol, funding_time_ms))")
        H8 = 8*3_600_000
        for i in range(6):   # 6 settlements, constant rate 0.0001
            con.execute("INSERT INTO funding VALUES('BTCUSDT', ?, 0.0001)", (i*H8,))
    r_short = shadow.symbol_rate("BTCUSDT", funding_db=fdb, start_ms=0, end_ms=2*H8)   # 3 settlements
    r_long = shadow.symbol_rate("BTCUSDT", funding_db=fdb, start_ms=0, end_ms=5*H8)    # 6 settlements
    # Constant rate -> same intensive value regardless of window length.
    assert abs(r_short - r_long) < 1e-12
    assert abs(r_short - 0.0001 * 1095) < 1e-9    # mean(rate) * INTERVALS_PER_YEAR


def test_pooled_rate_uses_gate_a(monkeypatch):
    from tools.funding_carry import shadow, evaluate
    monkeypatch.setattr(shadow, "symbol_rate", lambda s, **kw: {"A": 0.06, "B": 0.07}[s])
    out = shadow.pooled_rate(["A", "B"], funding_db="x", start_ms=0, end_ms=1)
    ref = evaluate.gate_a([0.06, 0.07])
    assert out["ci_lo"] == ref["ci_lo"] and out["ci_hi"] == ref["ci_hi"] and out["mean"] == ref["mean"]
    assert out["dropped"] == []


def test_block_start_non_overlapping():
    from tools.funding_carry.shadow import block_start
    WK = 7*24*3_600_000
    # Two timestamps in the same 1-week block -> same block_start.
    assert block_start(5*WK + 1000, 1) == block_start(5*WK + 99999, 1)
    # Crossing into the next week -> different block.
    assert block_start(5*WK, 1) != block_start(6*WK, 1)


def test_decay_state_anchors():
    from tools.funding_carry.shadow import decay_state
    from tools.funding_carry.constants import DECAY_KILL_N, R_FOSSIL_LO, T_FLOOR
    # ALIVE: ci_lo at/above fossil band.
    s = decay_state(ci_lo=R_FOSSIL_LO+0.001, ci_hi=R_FOSSIL_LO+0.02, r_mean=R_FOSSIL_LO+0.01,
                    blocks_below=0, r_fossil_lo=R_FOSSIL_LO, t_floor=T_FLOOR)
    assert s["decay_state"] == "ALIVE" and s["blocks_below"] == 0
    # THIN: below fossil band but ci_hi still above cost floor.
    s = decay_state(ci_lo=T_FLOOR+0.001, ci_hi=R_FOSSIL_LO-0.001, r_mean=R_FOSSIL_LO-0.005,
                    blocks_below=0, r_fossil_lo=R_FOSSIL_LO, t_floor=T_FLOOR)
    assert s["decay_state"] == "THIN" and s["blocks_below"] == 0
    # Below floor once: counter increments, not yet REFUTED (N>1).
    s = decay_state(ci_lo=-0.1, ci_hi=T_FLOOR-0.001, r_mean=-0.05,
                    blocks_below=0, r_fossil_lo=R_FOSSIL_LO, t_floor=T_FLOOR)
    assert s["blocks_below"] == 1 and s["decay_state"] == ("REFUTED" if DECAY_KILL_N<=1 else "THIN")
    # N consecutive below floor -> REFUTED.
    s = decay_state(ci_lo=-0.1, ci_hi=T_FLOOR-0.001, r_mean=-0.05,
                    blocks_below=DECAY_KILL_N-1, r_fossil_lo=R_FOSSIL_LO, t_floor=T_FLOOR)
    assert s["blocks_below"] == DECAY_KILL_N and s["decay_state"] == "REFUTED"
    # Recovery above floor resets counter.
    s = decay_state(ci_lo=R_FOSSIL_LO+0.001, ci_hi=R_FOSSIL_LO+0.02, r_mean=R_FOSSIL_LO+0.01,
                    blocks_below=3, r_fossil_lo=R_FOSSIL_LO, t_floor=T_FLOOR)
    assert s["blocks_below"] == 0 and s["decay_state"] == "ALIVE"


def test_window_complete_detects_gap():
    from tools.funding_carry.shadow import window_complete
    H8 = 8 * 3_600_000
    # Contiguous 8h settlements over the window -> complete.
    ts_ok = [0, H8, 2*H8, 3*H8]
    assert window_complete(ts_ok, start_ms=0, end_ms=3*H8, max_gap_ms=int(1.5*H8)) is True
    # A >1.5x8h hole -> incomplete.
    ts_gap = [0, H8, 5*H8]
    assert window_complete(ts_gap, start_ms=0, end_ms=5*H8, max_gap_ms=int(1.5*H8)) is False
    # Fewer than 2 points -> incomplete.
    assert window_complete([0], start_ms=0, end_ms=H8, max_gap_ms=H8) is False


def test_run_once_counter_advances_only_on_new_block(tmp_path, monkeypatch):
    import json
    from tools.funding_carry import shadow
    from tools.funding_carry.constants import T_FLOOR
    out_dir = tmp_path / "shadow"; WK = 7*24*3_600_000
    monkeypatch.setattr(shadow, "_ingest_funding", lambda symbols, db, limit: None)
    monkeypatch.setattr(shadow, "_cal_hash", lambda: "x")
    monkeypatch.setattr(shadow, "_window_settlement_times", lambda *a, **k: [0, 1])
    monkeypatch.setattr(shadow, "window_complete", lambda *a, **k: True)
    # Below-floor reading every run.
    monkeypatch.setattr(shadow, "pooled_rate", lambda *a, **k: {
        "mean": -0.05, "ci_lo": -0.1, "ci_hi": T_FLOOR-0.001, "n": 9, "dropped": [], "per_symbol": {}})
    # Run twice in the SAME block -> counter must advance only once.
    shadow.run_once(out_dir=str(out_dir), now_ms=5*WK+1000, w_weeks=1)
    shadow.run_once(out_dir=str(out_dir), now_ms=5*WK+2000, w_weeks=1)
    st = json.loads((out_dir / "funding_carry_state.json").read_text())
    assert st["blocks_below"] == 1     # same block, counted once
    # Run in the NEXT block -> counter advances to 2.
    shadow.run_once(out_dir=str(out_dir), now_ms=6*WK+1000, w_weeks=1)
    st = json.loads((out_dir / "funding_carry_state.json").read_text())
    assert st["blocks_below"] == 2
    # jsonl is append-only: 3 lines.
    lines = (out_dir / "funding_carry_signals.jsonl").read_text().strip().splitlines()
    assert len(lines) == 3


def test_run_once_failsoft_error(tmp_path, monkeypatch):
    import json
    from tools.funding_carry import shadow
    out_dir = tmp_path / "shadow"
    monkeypatch.setattr(shadow, "_ingest_funding", lambda symbols, db, limit: None)
    monkeypatch.setattr(shadow, "_cal_hash", lambda: "x")
    monkeypatch.setattr(shadow, "_window_settlement_times", lambda *a, **k: [0, 1])
    monkeypatch.setattr(shadow, "window_complete", lambda *a, **k: True)
    def boom(*a, **k): raise RuntimeError("db gone")
    monkeypatch.setattr(shadow, "pooled_rate", boom)
    res = shadow.run_once(out_dir=str(out_dir), now_ms=10_000_000_000, w_weeks=1)
    assert res["decay_state"] == "ERROR"
    st = json.loads((out_dir / "funding_carry_state.json").read_text())
    assert st["decay_state"] == "ERROR" and "db gone" in st["error"]


def test_min_window_weeks_monotone():
    from tools.funding_carry.power import min_window_weeks
    # SE shrinks ~1/sqrt(n); a tighter target band needs a larger window. Monotone & >=1.
    w_loose = min_window_weeks(per_symbol_settlements_per_week=21, n_symbols=9,
                               sigma_annual=0.05, target_half_band=0.0066)
    w_tight = min_window_weeks(per_symbol_settlements_per_week=21, n_symbols=9,
                               sigma_annual=0.05, target_half_band=0.0030)
    assert w_loose >= 1 and w_tight >= w_loose


# ---------------------------------------------------------------------------
# Power v2: fossil_rate_band + cost_floor (REV 5 anchors)
# ---------------------------------------------------------------------------

def _mk_fossil_db(path, sym_rates):
    """Build a tiny funding.db with known rates. sym_rates: {symbol: [rate, ...]}."""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE funding(symbol TEXT, funding_time_ms INTEGER,"
                " funding_rate REAL, PRIMARY KEY(symbol, funding_time_ms))")
    for sym, rates in sym_rates.items():
        for i, r in enumerate(rates):
            con.execute("INSERT INTO funding VALUES(?,?,?)", (sym, i * 28_800_000, r))
    con.commit(); con.close()


def test_fossil_rate_band_uses_gate_a(tmp_path):
    """fossil_rate_band must return gate_a's CI on the per-symbol mean rates (annualized ×1095).

    Build a tiny funding.db with 2 symbols and known rates.  For each symbol compute
    mean(rate)×1095 manually, run gate_a on those two values, and assert the function
    returns an identical dict."""
    from tools.funding_carry.power import fossil_rate_band
    from tools.funding_carry.evaluate import gate_a
    INTERVALS_PER_YEAR = 1095

    # Symbol A: two rates [0.0001, 0.0003]  -> mean = 0.0002, annualized = 0.219
    # Symbol B: four rates [0.0002, 0.0002, 0.0002, 0.0002] -> mean = 0.0002, annualized = 0.219
    sym_rates = {
        "ATOKEN": [0.0001, 0.0003],
        "BTOKEN": [0.0002, 0.0002, 0.0002, 0.0002],
    }
    db = str(tmp_path / "fossil.db")
    _mk_fossil_db(db, sym_rates)

    start_ms = 0
    # End beyond last settlement so all rows are included
    end_ms = 10 * 28_800_000

    result = fossil_rate_band(db, list(sym_rates.keys()), start_ms, end_ms)

    # Manually compute expected annualized means
    mean_a = (0.0001 + 0.0003) / 2 * INTERVALS_PER_YEAR
    mean_b = 0.0002 * INTERVALS_PER_YEAR
    ref = gate_a([mean_a, mean_b])

    # CI values must match exactly (same bootstrap seed, same inputs)
    assert result["ci_lo"] == pytest.approx(ref["ci_lo"])
    assert result["ci_hi"] == pytest.approx(ref["ci_hi"])
    assert result["mean"] == pytest.approx(ref["mean"])
    assert result["n"] == 2
    # Assert the annualization: mean must be ×1095 relative to raw per-settlement mean
    raw_mean = (mean_a / INTERVALS_PER_YEAR + mean_b / INTERVALS_PER_YEAR) / 2
    assert result["mean"] == pytest.approx(raw_mean * INTERVALS_PER_YEAR)


def test_fossil_rate_band_drops_symbol_with_no_settlements(tmp_path):
    """A symbol with 0 settlements in the window is dropped, not poisoning the pool."""
    from tools.funding_carry.power import fossil_rate_band

    sym_rates = {"GOOD": [0.0001, 0.0002]}
    db = str(tmp_path / "fossil.db")
    _mk_fossil_db(db, sym_rates)

    # Include a symbol that is NOT in the DB at all
    result = fossil_rate_band(db, ["GOOD", "GHOST"], start_ms=0, end_ms=10 * 28_800_000)
    assert result["n"] == 1   # only GOOD survived


def test_cost_floor_uses_median(tmp_path):
    """cost_floor must use the MEDIAN, not the mean, across per-symbol costs.

    With costs [30, 40, 50, 1254] / notional, the mean is dominated by the outlier;
    the median is (40+50)/2 = 45.  Assert the result matches the median path."""
    import json as _json
    from tools.funding_carry.power import cost_floor

    notional = 10_000.0
    h_ref_years = 2.0
    margin = 0.0
    costs_v3 = [30.0, 40.0, 50.0, 1254.0]   # PENDLE-like outlier at end

    records = [{"symbol": f"SYM{i}", "cost_v3": c} for i, c in enumerate(costs_v3)]
    json_path = str(tmp_path / "per_symbol.json")
    with open(json_path, "w") as fh:
        _json.dump(records, fh)

    result = cost_floor(json_path, notional=notional, h_ref_years=h_ref_years, margin=margin)

    # Median of [30/10000, 40/10000, 50/10000, 1254/10000]
    # = median([0.003, 0.004, 0.005, 0.1254]) = (0.004 + 0.005) / 2 = 0.0045
    expected_median = statistics.median(c / notional for c in costs_v3)
    expected = expected_median / h_ref_years + margin
    assert result == pytest.approx(expected)

    # Confirm the outlier does NOT dominate: mean would be >> median
    mean_cost = sum(c / notional for c in costs_v3) / len(costs_v3)
    assert mean_cost > expected * 5   # mean is >5× the median result

    # Also verify h_ref_years scaling and margin additive
    result_margin = cost_floor(json_path, notional=notional, h_ref_years=h_ref_years,
                               margin=0.01)
    assert result_margin == pytest.approx(expected + 0.01)

    result_h4 = cost_floor(json_path, notional=notional, h_ref_years=4.0, margin=0.0)
    assert result_h4 == pytest.approx(expected_median / 4.0)
