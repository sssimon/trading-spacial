"""Candado + tests de la celda 4 (stat-arb). Grupo 1: constants + v3w costs.

Grupos posteriores extienden este archivo. Determinista, sin red, fixtures sólo
(jamás lee data/program_ohlcv.db ni data/holdout/).

Spec: docs/superpowers/specs/2026-06-05-celda4-stat-arb-falsification-design.md
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backtest_costs import _TIER_BY_SYMBOL, load_calibration, tier_for_symbol
from tools.celda4_stat_arb import constants
from tools.celda4_stat_arb.costs import (
    derive_tier_cutoffs,
    tier_for_volume,
    v3w_fill_cost,
)

PKG_DIR = Path(__file__).resolve().parent.parent / "tools" / "celda4_stat_arb"

# Volúmenes diarios objetivo por símbolo, con separación CLARA entre tiers
# (large >> mid >> small) para que los puntos medios geométricos los separen.
_TARGET_DAILY_DOLLAR_VOL = {
    # large (v3 "major")
    "BTCUSDT": 5.0e9,
    "ETHUSDT": 2.0e9,
    # mid
    "ADAUSDT": 5.0e7,
    "AVAXUSDT": 4.0e7,
    "DOGEUSDT": 3.0e7,
    "UNIUSDT": 2.0e7,
    "XLMUSDT": 1.0e7,
    # small
    "PENDLEUSDT": 5.0e6,
    "JUPUSDT": 3.0e6,
    "RUNEUSDT": 2.0e6,
}

_HOUR_MS = 3_600_000
_DAY_MS = 86_400_000


def _to_ms(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _make_db(path: Path, daily_vols: dict[str, float], *, n_days: int = 5) -> None:
    """Crea un perp_klines con n_days días de barras 1h por símbolo dentro de la
    ventana de referencia, con dollar-volume diario == daily_vols[symbol] EXACTO.

    Cada día tiene 24 barras; close=100 constante; volume por barra =
    daily_vol / 24 / close → Σ_barras(volume*close) = daily_vol (mediana = misma).
    """
    start_ms = _to_ms(constants.V3W_REFERENCE_WINDOW[0])
    close = 100.0
    with sqlite3.connect(path) as con:
        con.execute(
            "CREATE TABLE perp_klines("
            "symbol TEXT NOT NULL, open_time INTEGER NOT NULL,"
            "open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,"
            "close REAL NOT NULL, volume REAL NOT NULL,"
            "PRIMARY KEY(symbol, open_time))"
        )
        rows = []
        for symbol, daily_vol in daily_vols.items():
            vol_per_bar = daily_vol / 24.0 / close
            for d in range(n_days):
                day_start = start_ms + d * _DAY_MS
                for h in range(24):
                    ot = day_start + h * _HOUR_MS
                    rows.append((symbol, ot, close, close, close, close, vol_per_bar))
        con.executemany(
            "INSERT INTO perp_klines VALUES (?,?,?,?,?,?,?)", rows
        )


@pytest.fixture
def good_db(tmp_path):
    """db donde los 10 curados reproducen su tier v3 con separación clara."""
    p = tmp_path / "good.db"
    _make_db(p, _TARGET_DAILY_DOLLAR_VOL)
    return str(p)


@pytest.fixture
def broken_db(tmp_path):
    """db donde un símbolo 'small' tiene MÁS volumen que uno 'large' → solape."""
    vols = dict(_TARGET_DAILY_DOLLAR_VOL)
    vols["RUNEUSDT"] = 1.0e10   # small con volumen mayor que cualquier large
    p = tmp_path / "broken.db"
    _make_db(p, vols)
    return str(p)


# ---------------------------------------------------------------------------
# derive_tier_cutoffs
# ---------------------------------------------------------------------------

def test_derive_cutoffs_mapea_todos_a_su_tier_v3(good_db):
    out = derive_tier_cutoffs(good_db)
    cutoffs = {"cutoff_large": out["cutoff_large"], "cutoff_mid": out["cutoff_mid"]}
    for symbol in _TIER_BY_SYMBOL:
        mv = out["derivation"][symbol]["median_dollar_vol"]
        mapped = tier_for_volume(mv, cutoffs)
        expected_v3w = {"major": "large", "mid": "mid", "small": "small"}[
            tier_for_symbol(symbol)
        ]
        assert mapped == expected_v3w, f"{symbol}: {mapped} != {expected_v3w}"


def test_derive_cutoffs_monotono(good_db):
    out = derive_tier_cutoffs(good_db)
    assert out["cutoff_large"] > out["cutoff_mid"] > 0.0


def test_derive_cutoffs_hard_fail_en_solape(broken_db):
    with pytest.raises(ValueError):
        derive_tier_cutoffs(broken_db)


# ---------------------------------------------------------------------------
# tier_for_volume — fronteras
# ---------------------------------------------------------------------------

def test_tier_for_volume_fronteras():
    cutoffs = {"cutoff_large": 1.0e8, "cutoff_mid": 1.0e7}
    assert tier_for_volume(5.0e8, cutoffs) == "large"
    assert tier_for_volume(1.0e8, cutoffs) == "large"          # >= cutoff_large
    assert tier_for_volume(9.9e7, cutoffs) == "mid"
    assert tier_for_volume(1.0e7, cutoffs) == "mid"            # >= cutoff_mid
    assert tier_for_volume(9.9e6, cutoffs) == "small"
    assert tier_for_volume(0.0, cutoffs) == "small"


# ---------------------------------------------------------------------------
# v3w_fill_cost
# ---------------------------------------------------------------------------

def test_v3w_fill_cost_monotono_por_tier():
    cal = load_calibration()
    notional = constants.NOTIONAL_PER_LEG
    mdv = 5_000_000.0
    large = v3w_fill_cost(notional, "large", cal, median_daily_dollar_vol=mdv)
    mid = v3w_fill_cost(notional, "mid", cal, median_daily_dollar_vol=mdv)
    small = v3w_fill_cost(notional, "small", cal, median_daily_dollar_vol=mdv)
    assert small > mid > large > 0.0


def test_v3w_fill_cost_forced_close_fuerza_small():
    cal = load_calibration()
    notional = constants.NOTIONAL_PER_LEG
    mdv = 5_000_000.0
    small = v3w_fill_cost(notional, "small", cal, median_daily_dollar_vol=mdv)
    forced_from_large = v3w_fill_cost(
        notional, "large", cal, median_daily_dollar_vol=mdv, forced_close=True)
    assert forced_from_large == small


def test_v3w_fill_cost_reusa_leg_completo_de_v3():
    """El costo v3w de un fill == leg COMPLETO de v3 (floor + tail), reusando
    directamente _v3_leg_cost — NO solo el floor."""
    from backtest_costs import _v3_leg_cost
    cal = load_calibration()
    notional = 10_000.0
    mdv = 5_000_000.0
    liq = mdv / 1440.0
    for v3w_tier, v3_key in (("large", "major"), ("mid", "mid"), ("small", "small")):
        tp = cal.tiers[v3_key]
        leg_bps, _, _ = _v3_leg_cost(notional, liq, tp, cal.global_)
        expected = leg_bps * notional / 10_000.0
        got = v3w_fill_cost(notional, v3w_tier, cal, median_daily_dollar_vol=mdv)
        assert got == pytest.approx(expected)
        # El tail está presente: el leg completo > floor leg puro.
        floor_only = tp.stress_mult * (tp.half_spread_bps + tp.fee_bps_per_side)
        assert leg_bps > floor_only


def test_v3w_fill_cost_tail_responde_a_liquidez():
    """Menor liquidez (median_daily_dollar_vol) → estrictamente MÁS costo de fill
    al mismo notional/tier: el tail Almgren-Chriss responde a la liquidez."""
    cal = load_calibration()
    notional = constants.NOTIONAL_PER_LEG
    high_liq = v3w_fill_cost(notional, "mid", cal, median_daily_dollar_vol=50_000_000.0)
    low_liq = v3w_fill_cost(notional, "mid", cal, median_daily_dollar_vol=2_000_000.0)
    assert low_liq > high_liq


# ---------------------------------------------------------------------------
# constants — literales irrevocables
# ---------------------------------------------------------------------------

def test_constants_study_end_literal():
    assert constants.STUDY_END == "2025-04-30"   # frontera del holdout (spec NV-A)


def test_constants_seed_literal():
    assert constants.SEED == 20260605


# ---------------------------------------------------------------------------
# pureza del holdout — ningún módulo del paquete toca el holdout
# ---------------------------------------------------------------------------

def test_paquete_no_referencia_holdout():
    forbidden = ("data/holdout", "open_holdout")
    for py in PKG_DIR.glob("*.py"):
        text = py.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text, f"{py.name} contiene {needle!r} (prohibido §3)"


# ===========================================================================
# GRUPO 2 — pairs.py (formación) + simulate.py (señal+ejecución+costos)
# ===========================================================================

import math

import numpy as np

from tools.celda4_stat_arb import pairs as pairs_mod
from tools.celda4_stat_arb import simulate as sim_mod
from tools.celda4_stat_arb.pairs import (
    eligible_symbols,
    expected_bars,
    form_pairs,
)
from tools.celda4_stat_arb.simulate import (
    assert_within_study_bounds,
    simulate_window,
    trading_windows,
)


def _klines_db(path, bars_by_symbol):
    """Create a perp_klines (+ empty perp_funding) db from a dict
    {symbol: list[(open_time_ms, close, volume)]}."""
    with sqlite3.connect(path) as con:
        con.execute(
            "CREATE TABLE perp_klines("
            "symbol TEXT NOT NULL, open_time INTEGER NOT NULL,"
            "open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,"
            "close REAL NOT NULL, volume REAL NOT NULL,"
            "PRIMARY KEY(symbol, open_time))"
        )
        con.execute(
            "CREATE TABLE perp_funding("
            "symbol TEXT NOT NULL, funding_time_ms INTEGER NOT NULL,"
            "funding_rate REAL NOT NULL,"
            "PRIMARY KEY(symbol, funding_time_ms))"
        )
        rows = []
        for sym, bars in bars_by_symbol.items():
            for ot, close, vol in bars:
                rows.append((sym, ot, close, close, close, close, vol))
        con.executemany("INSERT INTO perp_klines VALUES (?,?,?,?,?,?,?)", rows)


def _add_funding(path, funding_rows):
    """funding_rows: list[(symbol, funding_time_ms, rate)]."""
    with sqlite3.connect(path) as con:
        con.executemany(
            "INSERT INTO perp_funding VALUES (?,?,?)", funding_rows
        )


def _ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


# ---------------------------------------------------------------------------
# expected_bars
# ---------------------------------------------------------------------------

def test_expected_bars_180d():
    start = _to_ms(constants.STUDY_START)
    end = start + constants.FORMATION_DAYS * _DAY_MS
    assert expected_bars(start, end) == constants.FORMATION_DAYS * 24 == 4320


# ---------------------------------------------------------------------------
# eligible_symbols
# ---------------------------------------------------------------------------

def _full_formation_bars(start_ms, n_days, *, close, vol):
    bars = []
    for d in range(n_days):
        for h in range(24):
            bars.append((start_ms + d * _DAY_MS + h * _HOUR_MS, close, vol))
    return bars


def test_eligible_coverage_filter(tmp_path):
    """A symbol below MIN_COVERAGE of expected formation bars is excluded."""
    fstart = _to_ms("2021-01-01")
    n_days = 10
    fend = fstart + n_days * _DAY_MS
    close = 100.0
    # daily $vol comfortably above $1M: vol_per_bar*close*24 = $2M/day
    vol = 2_000_000.0 / 24.0 / close
    full = _full_formation_bars(fstart, n_days, close=close, vol=vol)
    # SPARSE: only 50% of bars present -> coverage 0.5 < 0.95
    sparse = full[::2]
    p = tmp_path / "elig.db"
    _klines_db(p, {"AAAUSDT": full, "BBBUSDT": sparse})
    with _ro(p) as con:
        elig = eligible_symbols(con, fstart, fend)
    assert "AAAUSDT" in elig
    assert "BBBUSDT" not in elig


def test_eligible_dollar_vol_filter(tmp_path):
    """A symbol with full coverage but median daily $vol < $1M is excluded."""
    fstart = _to_ms("2021-01-01")
    n_days = 10
    fend = fstart + n_days * _DAY_MS
    close = 100.0
    rich = _full_formation_bars(
        fstart, n_days, close=close, vol=2_000_000.0 / 24.0 / close)
    poor = _full_formation_bars(
        fstart, n_days, close=close, vol=500_000.0 / 24.0 / close)  # $0.5M/day
    p = tmp_path / "vol.db"
    _klines_db(p, {"RICHUSDT": rich, "POORUSDT": poor})
    with _ro(p) as con:
        elig = eligible_symbols(con, fstart, fend)
    assert "RICHUSDT" in elig
    assert "POORUSDT" not in elig
    assert elig["RICHUSDT"]["median_daily_dollar_vol"] == pytest.approx(2_000_000.0)


def test_eligible_anti_survivorship_post_formation_delist(tmp_path):
    """F3: a symbol that delists 1 day AFTER formation_end is STILL eligible.
    Its post-formation absence must not affect eligibility (pure function of
    formation bars)."""
    fstart = _to_ms("2021-01-01")
    n_days = 10
    fend = fstart + n_days * _DAY_MS
    close = 100.0
    vol = 2_000_000.0 / 24.0 / close
    # Eligible during formation (full coverage, rich vol)...
    formation = _full_formation_bars(fstart, n_days, close=close, vol=vol)
    # ...plus exactly 1 day of post-formation bars, then nothing (delists).
    post = [(fend + h * _HOUR_MS, close, vol) for h in range(24)]
    p = tmp_path / "delist.db"
    _klines_db(p, {"DIEUSDT": formation + post, "LIVEUSDT": formation})
    with _ro(p) as con:
        elig = eligible_symbols(con, fstart, fend)
    # The dying symbol is eligible on formation evidence alone.
    assert "DIEUSDT" in elig
    assert "LIVEUSDT" in elig


def test_eligible_never_queries_beyond_formation_end(tmp_path):
    """Eligibility must be a pure function of bars with open_time < formation_end.
    Post-formation bars (even huge volume) must NOT rescue an ineligible symbol."""
    fstart = _to_ms("2021-01-01")
    n_days = 10
    fend = fstart + n_days * _DAY_MS
    close = 100.0
    # POOR during formation ($0.5M/day) but a flood of volume AFTER formation_end.
    poor = _full_formation_bars(
        fstart, n_days, close=close, vol=500_000.0 / 24.0 / close)
    flood = [(fend + h * _HOUR_MS, close, 1.0e9) for h in range(24)]
    p = tmp_path / "beyond.db"
    _klines_db(p, {"POORUSDT": poor + flood})
    with _ro(p) as con:
        elig = eligible_symbols(con, fstart, fend)
    assert "POORUSDT" not in elig


# ---------------------------------------------------------------------------
# form_pairs
# ---------------------------------------------------------------------------

def _cointegrated_prices(seed, n, *, alpha, beta):
    """X = exp(random walk); Y = exp(alpha + beta*log(X) + stationary AR noise).
    log(Y) = alpha + beta*log(X) + noise → ADF on residuals rejects unit root."""
    rng = np.random.default_rng(seed)
    logx = np.cumsum(rng.normal(0, 0.02, n)) + 5.0   # random walk, base near e^5
    # Stationary AR(1) noise (mean-reverting residual).
    noise = np.zeros(n)
    for i in range(1, n):
        noise[i] = 0.5 * noise[i - 1] + rng.normal(0, 0.01)
    logy = alpha + beta * logx + noise
    return np.exp(logx), np.exp(logy)


def _independent_walks(seed, n):
    rng = np.random.default_rng(seed)
    logx = np.cumsum(rng.normal(0, 0.02, n)) + 5.0
    logy = np.cumsum(rng.normal(0, 0.02, n)) + 5.0
    return np.exp(logx), np.exp(logy)


def _bars_from_prices(start_ms, prices, vol):
    return [(start_ms + i * _HOUR_MS, float(p), vol) for i, p in enumerate(prices)]


def test_form_pairs_cointegrated_passes_orientation_and_beta(tmp_path):
    fstart = _to_ms("2021-01-01")
    n = 1500
    fend = fstart + n * _HOUR_MS
    alpha, beta = 0.7, 1.3
    px, py = _cointegrated_prices(seed=42, n=n, alpha=alpha, beta=beta)
    vol = 5_000_000.0 / 24.0 / 100.0
    # Lexicographic: "AAA" < "BBB" → X=AAA, Y=BBB. Give the cointegrated Y to BBB.
    _klines_db(
        tmp_path / "coint.db",
        {
            "AAAUSDT": _bars_from_prices(fstart, px, vol),
            "BBBUSDT": _bars_from_prices(fstart, py, vol),
        },
    )
    p = str(tmp_path / "coint.db")
    with _ro(p) as con:
        elig = eligible_symbols(con, fstart, fend)
        formed = form_pairs(con, elig, fstart, fend)
    assert len(formed) == 1
    pair = formed[0]
    assert pair["x"] == "AAAUSDT" and pair["y"] == "BBBUSDT"   # lexicographic
    assert pair["adf_p"] < constants.ADF_P
    # Regression was Y-on-X: recovered beta ≈ constructed beta.
    assert pair["beta"] == pytest.approx(beta, abs=0.1)
    assert pair["alpha"] == pytest.approx(alpha, abs=0.3)
    # mu ≈ 0 with intercept; sigma > 0.
    assert abs(pair["mu"]) < 1e-9 or abs(pair["mu"]) < 0.01
    assert pair["sigma"] > 0.0
    assert pair["formation_start_ms"] == fstart
    assert pair["formation_end_ms"] == fend


def test_form_pairs_independent_walks_excluded(tmp_path):
    fstart = _to_ms("2021-01-01")
    n = 1500
    fend = fstart + n * _HOUR_MS
    px, py = _independent_walks(seed=7, n=n)
    vol = 5_000_000.0 / 24.0 / 100.0
    _klines_db(
        tmp_path / "indep.db",
        {
            "AAAUSDT": _bars_from_prices(fstart, px, vol),
            "BBBUSDT": _bars_from_prices(fstart, py, vol),
        },
    )
    p = str(tmp_path / "indep.db")
    with _ro(p) as con:
        elig = eligible_symbols(con, fstart, fend)
        formed = form_pairs(con, elig, fstart, fend)
    assert formed == []


def test_form_pairs_sigma_guard(tmp_path):
    """A pair whose formation-spread σ < SIGMA_GUARD is excluded."""
    fstart = _to_ms("2021-01-01")
    n = 1500
    fend = fstart + n * _HOUR_MS
    # Y is an EXACT affine function of log(X): residuals ≈ 0 → σ < 1e-6.
    rng = np.random.default_rng(3)
    logx = np.cumsum(rng.normal(0, 0.02, n)) + 5.0
    px = np.exp(logx)
    py = np.exp(0.5 + 1.1 * logx)   # zero residual
    vol = 5_000_000.0 / 24.0 / 100.0
    _klines_db(
        tmp_path / "guard.db",
        {
            "AAAUSDT": _bars_from_prices(fstart, px, vol),
            "BBBUSDT": _bars_from_prices(fstart, py, vol),
        },
    )
    p = str(tmp_path / "guard.db")
    with _ro(p) as con:
        elig = eligible_symbols(con, fstart, fend)
        formed = form_pairs(con, elig, fstart, fend)
    assert formed == []


def test_form_pairs_greedy_cap_one_per_symbol(tmp_path):
    """A symbol cannot appear in two formed pairs (MAX_PAIRS_PER_SYMBOL=1)."""
    fstart = _to_ms("2021-01-01")
    n = 1500
    fend = fstart + n * _HOUR_MS
    vol = 5_000_000.0 / 24.0 / 100.0
    # AAA cointegrates with BOTH BBB and CCC. Greedy takes AAA's best, then must
    # skip the other AAA-pair.
    px, py = _cointegrated_prices(seed=42, n=n, alpha=0.7, beta=1.3)
    _, pz = _cointegrated_prices(seed=99, n=n, alpha=0.4, beta=0.9)
    # Make CCC cointegrate with AAA by building it off AAA's logx.
    rng = np.random.default_rng(5)
    logx = np.log(px)
    noise = np.zeros(n)
    for i in range(1, n):
        noise[i] = 0.5 * noise[i - 1] + rng.normal(0, 0.01)
    pc = np.exp(0.4 + 0.9 * logx + noise)
    _klines_db(
        tmp_path / "cap.db",
        {
            "AAAUSDT": _bars_from_prices(fstart, px, vol),
            "BBBUSDT": _bars_from_prices(fstart, py, vol),
            "CCCUSDT": _bars_from_prices(fstart, pc, vol),
        },
    )
    p = str(tmp_path / "cap.db")
    with _ro(p) as con:
        elig = eligible_symbols(con, fstart, fend)
        formed = form_pairs(con, elig, fstart, fend)
    used = []
    for pr in formed:
        used.append(pr["x"])
        used.append(pr["y"])
    assert len(used) == len(set(used)), f"a symbol appears twice: {used}"


# ---------------------------------------------------------------------------
# trading_windows
# ---------------------------------------------------------------------------

def test_trading_windows_first_starts_after_180d():
    wins = trading_windows()
    assert wins, "expected at least one trading window"
    study_start = _to_ms(constants.STUDY_START)
    first_trade_start = wins[0][2]
    assert first_trade_start == study_start + constants.FORMATION_DAYS * _DAY_MS


def test_trading_windows_all_full_30d_and_within_study_end():
    wins = trading_windows()
    study_end = _to_ms(constants.STUDY_END)
    span = constants.TRADING_DAYS * _DAY_MS
    prev_end = None
    for fstart, fend, tstart, tend in wins:
        # formation = the 180d immediately before trading start.
        assert fend == tstart
        assert fstart == tstart - constants.FORMATION_DAYS * _DAY_MS
        # full 30-day trading window.
        assert tend - tstart == span
        # never crosses STUDY_END.
        assert tend <= study_end
        # non-overlapping & contiguous (rolling).
        if prev_end is not None:
            assert tstart == prev_end
        prev_end = tend


# ---------------------------------------------------------------------------
# assert_within_study_bounds — hard boundary helper
# ---------------------------------------------------------------------------

def test_study_bound_helper_fires_at_or_beyond_study_end():
    study_end = _to_ms(constants.STUDY_END)
    assert_within_study_bounds(study_end - 1)         # OK: before the frontier
    with pytest.raises(AssertionError):
        assert_within_study_bounds(study_end)          # == STUDY_END forbidden
    with pytest.raises(AssertionError):
        assert_within_study_bounds(study_end + _HOUR_MS)


# ---------------------------------------------------------------------------
# simulate_window — deterministic tiny fixtures, hand-computed
# ---------------------------------------------------------------------------

_CUTOFFS = {"cutoff_large": 1.0e8, "cutoff_mid": 1.0e7}


def _calibration():
    return load_calibration()


def _make_pair(x="XXXUSDT", y="YYYUSDT", *, alpha, beta, mu, sigma,
               fstart, fend, xmdv=5.0e6, ymdv=5.0e6):
    return {
        "x": x, "y": y, "alpha": alpha, "beta": beta, "mu": mu, "sigma": sigma,
        "adf_p": 0.001,
        "x_median_dollar_vol": xmdv, "y_median_dollar_vol": ymdv,
        "formation_start_ms": fstart, "formation_end_ms": fend,
    }


def _z_of(y_close, x_close, pair):
    return (math.log(y_close) - pair["alpha"] - pair["beta"] * math.log(x_close)
            - pair["mu"]) / pair["sigma"]


def test_simulate_entry_fills_next_bar_lag(tmp_path):
    """Signal at bar t -> fill at close of bar t+1 (lag 1). Verify entry_time."""
    tstart = _to_ms("2023-06-01")
    tend = tstart + 30 * _DAY_MS
    # Build closes so z is computable simply: alpha=mu=0, beta=1, sigma=1 in log.
    # z = log(y) - log(x). With x=1 always (log=0): z = log(y).
    # bar0: y=1 -> z=0 (no entry). bar1: y=e^-2.5 -> z=-2.5 (entry signal, long-spread)
    # fill at NEXT bar (bar2) close. bar2..: y back so z crosses 0 -> exit next bar.
    x = 1.0
    closes_y = [1.0, math.exp(-2.5), 1.0, 1.0, 1.0, 1.0]
    bars_x = [(tstart + i * _HOUR_MS, x, 1.0) for i in range(len(closes_y))]
    bars_y = [(tstart + i * _HOUR_MS, cy, 1.0) for i, cy in enumerate(closes_y)]
    p = tmp_path / "lag.db"
    _klines_db(p, {"XXXUSDT": bars_x, "YYYUSDT": bars_y})
    pair = _make_pair(alpha=0.0, beta=1.0, mu=0.0, sigma=1.0,
                      fstart=tstart - 180 * _DAY_MS, fend=tstart)
    with _ro(p) as con:
        positions = simulate_window(con, [pair], tstart, tend, _CUTOFFS, _calibration())
    assert len(positions) == 1
    pos = positions[0]
    # signal at bar1 (z=-2.5) -> entry fill at bar2 close time.
    assert pos["entry_time_ms"] == tstart + 2 * _HOUR_MS
    assert pos["side"] == "long_spread"   # z <= -2


def test_simulate_funding_boundary_off_by_one(tmp_path):
    """F13: a settlement exactly at entry_fill_time is EXCLUDED; one exactly at
    exit_fill_time is INCLUDED. (entry_fill_time < t <= exit_fill_time]."""
    tstart = _to_ms("2023-06-01")
    tend = tstart + 30 * _DAY_MS
    # z path: bar0 z=0; bar1 z=-2.5 (signal); entry fill bar2.
    # then keep z<0 for a while, cross 0 at bar5 (signal) -> exit fill bar6.
    closes_y = [1.0, math.exp(-2.5), math.exp(-2.5), math.exp(-2.5),
                math.exp(-2.5), 1.0, 1.0, 1.0]
    x = 1.0
    bars_x = [(tstart + i * _HOUR_MS, x, 1.0) for i in range(len(closes_y))]
    bars_y = [(tstart + i * _HOUR_MS, cy, 1.0) for i, cy in enumerate(closes_y)]
    p = tmp_path / "fund.db"
    _klines_db(p, {"XXXUSDT": bars_x, "YYYUSDT": bars_y})
    entry_fill = tstart + 2 * _HOUR_MS
    exit_fill = tstart + 6 * _HOUR_MS
    # Settlements: one AT entry_fill (excluded), one AT exit_fill (included),
    # one strictly inside (included).
    _add_funding(p, [
        ("XXXUSDT", entry_fill, 0.01), ("YYYUSDT", entry_fill, 0.01),     # excluded
        ("XXXUSDT", entry_fill + _HOUR_MS, 0.01),                          # inside
        ("YYYUSDT", entry_fill + _HOUR_MS, 0.01),
        ("XXXUSDT", exit_fill, 0.01), ("YYYUSDT", exit_fill, 0.01),        # included
    ])
    pair = _make_pair(alpha=0.0, beta=1.0, mu=0.0, sigma=1.0,
                      fstart=tstart - 180 * _DAY_MS, fend=tstart)
    with _ro(p) as con:
        positions = simulate_window(con, [pair], tstart, tend, _CUTOFFS, _calibration())
    assert len(positions) == 1
    pos = positions[0]
    assert pos["entry_time_ms"] == entry_fill
    assert pos["exit_time_ms"] == exit_fill
    # Boundary (entry_fill, exit_fill]: the AT-ENTRY settlement is EXCLUDED, the
    # AT-EXIT and the strictly-inside ones are INCLUDED → exactly 2 per leg.
    # long_spread: long Y (-rate*mark*units_y), short X (+rate*mark*units_x).
    # mark = close at each settlement bar. inside settlement (entry_fill+1h = bar3):
    #   x close=1, y close=e^-2.5. at-exit settlement (exit_fill = bar6): x=1, y=1.
    entry_y = closes_y[2]                       # fill at bar2 close
    units_y = constants.NOTIONAL_PER_LEG / entry_y
    units_x = constants.NOTIONAL_PER_LEG / x
    rate = 0.01
    # inside settlement marks (bar3): y=e^-2.5, x=1.
    inside = (-rate * closes_y[3] * units_y) + (+rate * x * units_x)
    # at-exit settlement marks (bar6): y=1, x=1.
    at_exit = (-rate * closes_y[6] * units_y) + (+rate * x * units_x)
    assert pos["funding"] == pytest.approx(inside + at_exit)


def test_simulate_funding_sign_convention(tmp_path):
    """Long position pays positive funding (-rate*mark*units); short receives
    (+rate*mark*units). Verify per-leg signs for a long_spread (long Y, short X)."""
    tstart = _to_ms("2023-06-01")
    tend = tstart + 30 * _DAY_MS
    closes_y = [1.0, math.exp(-2.5), math.exp(-2.5), 1.0, 1.0]
    x = 1.0
    bars_x = [(tstart + i * _HOUR_MS, x, 1.0) for i in range(len(closes_y))]
    bars_y = [(tstart + i * _HOUR_MS, cy, 1.0) for i, cy in enumerate(closes_y)]
    p = tmp_path / "sign.db"
    _klines_db(p, {"XXXUSDT": bars_x, "YYYUSDT": bars_y})
    entry_fill = tstart + 2 * _HOUR_MS
    # one settlement strictly inside (entry < t <= exit). Positive rate.
    _add_funding(p, [
        ("XXXUSDT", entry_fill + _HOUR_MS, 0.01),
        ("YYYUSDT", entry_fill + _HOUR_MS, 0.01),
    ])
    pair = _make_pair(alpha=0.0, beta=1.0, mu=0.0, sigma=1.0,
                      fstart=tstart - 180 * _DAY_MS, fend=tstart)
    with _ro(p) as con:
        positions = simulate_window(con, [pair], tstart, tend, _CUTOFFS, _calibration())
    pos = positions[0]
    assert pos["side"] == "long_spread"
    # long_spread: long Y (pays: -rate*mark*units_y), short X (receives: +rate*mark*units_x).
    # mark ≈ close at settlement bar (bar3): x close=1, y close=1.
    units_y = constants.NOTIONAL_PER_LEG / closes_y[2]   # fill at bar2 close (=e^-2.5)
    units_x = constants.NOTIONAL_PER_LEG / x
    rate, mark = 0.01, 1.0
    expected = (-rate * mark * units_y) + (+rate * mark * units_x)
    assert pos["funding"] == pytest.approx(expected)


def test_simulate_stop_no_reentry(tmp_path):
    """|z|>=3 -> stop close; no re-entry for the rest of the window even if a new
    signal appears."""
    tstart = _to_ms("2023-06-01")
    tend = tstart + 30 * _DAY_MS
    # bar1 z=-2.5 signal -> entry bar2. bar3 z=-3.5 -> stop signal -> stop fill bar4.
    # bar5 z=-2.5 again (would re-enter) -> must be ignored.
    closes_y = [1.0, math.exp(-2.5), math.exp(-2.5), math.exp(-3.5),
                math.exp(-3.5), math.exp(-2.5), math.exp(-2.5), 1.0]
    x = 1.0
    bars_x = [(tstart + i * _HOUR_MS, x, 1.0) for i in range(len(closes_y))]
    bars_y = [(tstart + i * _HOUR_MS, cy, 1.0) for i, cy in enumerate(closes_y)]
    p = tmp_path / "stop.db"
    _klines_db(p, {"XXXUSDT": bars_x, "YYYUSDT": bars_y})
    pair = _make_pair(alpha=0.0, beta=1.0, mu=0.0, sigma=1.0,
                      fstart=tstart - 180 * _DAY_MS, fend=tstart)
    with _ro(p) as con:
        positions = simulate_window(con, [pair], tstart, tend, _CUTOFFS, _calibration())
    assert len(positions) == 1
    pos = positions[0]
    assert pos["exit_reason"] == "stop"


def test_simulate_forced_close_window_end(tmp_path):
    """An open position at window end is forcibly closed at the last bar close,
    no lag."""
    tstart = _to_ms("2023-06-01")
    # tiny window: only a few bars, then tend cuts off.
    closes_y = [1.0, math.exp(-2.5), math.exp(-2.5), math.exp(-2.5)]
    x = 1.0
    bars_x = [(tstart + i * _HOUR_MS, x, 1.0) for i in range(len(closes_y))]
    bars_y = [(tstart + i * _HOUR_MS, cy, 1.0) for i, cy in enumerate(closes_y)]
    tend = tstart + len(closes_y) * _HOUR_MS   # window ends right after last bar
    p = tmp_path / "we.db"
    _klines_db(p, {"XXXUSDT": bars_x, "YYYUSDT": bars_y})
    pair = _make_pair(alpha=0.0, beta=1.0, mu=0.0, sigma=1.0,
                      fstart=tstart - 180 * _DAY_MS, fend=tstart)
    with _ro(p) as con:
        positions = simulate_window(con, [pair], tstart, tend, _CUTOFFS, _calibration())
    assert len(positions) == 1
    pos = positions[0]
    assert pos["exit_reason"] == "window_end"
    # forced close at LAST bar close time (no lag).
    assert pos["exit_time_ms"] == tstart + (len(closes_y) - 1) * _HOUR_MS


def test_simulate_delisting_forced_close(tmp_path):
    """One leg loses bars mid-window -> forced close at that leg's last available
    bar; forced-close fills priced with forced_close=True (small tier)."""
    tstart = _to_ms("2023-06-01")
    tend = tstart + 30 * _DAY_MS
    # XXX delists after bar3; YYY continues. Position opened at bar2.
    closes_y = [1.0, math.exp(-2.5), math.exp(-2.5), math.exp(-2.5),
                math.exp(-2.5), math.exp(-2.5)]
    x = 1.0
    bars_x = [(tstart + i * _HOUR_MS, x, 1.0) for i in range(4)]   # delists after bar3
    bars_y = [(tstart + i * _HOUR_MS, cy, 1.0) for i, cy in enumerate(closes_y)]
    p = tmp_path / "delist_sim.db"
    _klines_db(p, {"XXXUSDT": bars_x, "YYYUSDT": bars_y})
    # Both legs MID tier (mdv between cutoff_mid 1e7 and cutoff_large 1e8) so the
    # forced->small exit pricing is OBSERVABLY different from the formation tier.
    mid_mdv = 5.0e7
    pair = _make_pair(alpha=0.0, beta=1.0, mu=0.0, sigma=1.0,
                      fstart=tstart - 180 * _DAY_MS, fend=tstart,
                      xmdv=mid_mdv, ymdv=mid_mdv)
    cal = _calibration()
    with _ro(p) as con:
        positions = simulate_window(con, [pair], tstart, tend, _CUTOFFS, cal)
    assert len(positions) == 1
    pos = positions[0]
    assert pos["exit_reason"] == "delisting"
    # forced close at XXX's last available bar (bar3).
    assert pos["exit_time_ms"] == tstart + 3 * _HOUR_MS
    # Hand-compute the four fills. Entry fill bar2: x=1, y=e^-2.5; exit bar3 same.
    entry_y, entry_x = closes_y[2], 1.0
    exit_y, exit_x = closes_y[3], 1.0
    units_y = constants.NOTIONAL_PER_LEG / entry_y
    units_x = constants.NOTIONAL_PER_LEG / entry_x
    # Entry fills use the MID formation tier; exit fills FORCED to small (delisting).
    expected_costs = (
        v3w_fill_cost(units_y * entry_y, "mid", cal, median_daily_dollar_vol=mid_mdv)
        + v3w_fill_cost(units_x * entry_x, "mid", cal, median_daily_dollar_vol=mid_mdv)
        + v3w_fill_cost(units_y * exit_y, "mid", cal, median_daily_dollar_vol=mid_mdv,
                        forced_close=True)
        + v3w_fill_cost(units_x * exit_x, "mid", cal, median_daily_dollar_vol=mid_mdv,
                        forced_close=True)
    )
    assert pos["costs"] == pytest.approx(expected_costs)
    # And the forced (small) exit cost strictly exceeds a non-forced (mid) exit cost.
    forced = v3w_fill_cost(units_y * exit_y, "mid", cal,
                           median_daily_dollar_vol=mid_mdv, forced_close=True)
    not_forced = v3w_fill_cost(units_y * exit_y, "mid", cal,
                               median_daily_dollar_vol=mid_mdv)
    assert forced > not_forced


def test_simulate_price_pnl_long_spread(tmp_path):
    """Hand-computed price P&L for a long_spread that exits on z-cross."""
    tstart = _to_ms("2023-06-01")
    tend = tstart + 30 * _DAY_MS
    # entry signal bar1, fill bar2 (y=e^-2.5, x=1). exit signal bar3 (z=0), fill bar4.
    closes_y = [1.0, math.exp(-2.5), math.exp(-2.5), 1.0, 2.0, 2.0]
    closes_x = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
    bars_x = [(tstart + i * _HOUR_MS, cx, 1.0) for i, cx in enumerate(closes_x)]
    bars_y = [(tstart + i * _HOUR_MS, cy, 1.0) for i, cy in enumerate(closes_y)]
    p = tmp_path / "pnl.db"
    _klines_db(p, {"XXXUSDT": bars_x, "YYYUSDT": bars_y})
    pair = _make_pair(alpha=0.0, beta=1.0, mu=0.0, sigma=1.0,
                      fstart=tstart - 180 * _DAY_MS, fend=tstart)
    with _ro(p) as con:
        positions = simulate_window(con, [pair], tstart, tend, _CUTOFFS, _calibration())
    pos = positions[0]
    entry_y, entry_x = closes_y[2], closes_x[2]   # fill at bar2
    exit_y, exit_x = closes_y[4], closes_x[4]      # exit signal bar3 (z=0)->fill bar4
    units_y = constants.NOTIONAL_PER_LEG / entry_y
    units_x = constants.NOTIONAL_PER_LEG / entry_x
    # long_spread: long Y (+1), short X (-1).
    expected_gross = (+1) * units_y * (exit_y - entry_y) + (-1) * units_x * (exit_x - entry_x)
    assert pos["gross"] == pytest.approx(expected_gross)
    assert pos["net"] == pytest.approx(pos["gross"] + pos["funding"] - pos["costs"])
    assert pos["pair"] == "XXXUSDT/YYYUSDT"
