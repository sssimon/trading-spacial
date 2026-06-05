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
    large = v3w_fill_cost(notional, "large", cal)
    mid = v3w_fill_cost(notional, "mid", cal)
    small = v3w_fill_cost(notional, "small", cal)
    assert small > mid > large > 0.0


def test_v3w_fill_cost_forced_close_fuerza_small():
    cal = load_calibration()
    notional = constants.NOTIONAL_PER_LEG
    small = v3w_fill_cost(notional, "small", cal)
    forced_from_large = v3w_fill_cost(notional, "large", cal, forced_close=True)
    assert forced_from_large == small


def test_v3w_fill_cost_reusa_floor_leg_de_v3():
    """El costo v3w de un fill == floor leg de v3 (stress_mult*(spread+fee))*$."""
    cal = load_calibration()
    notional = 10_000.0
    for v3w_tier, v3_key in (("large", "major"), ("mid", "mid"), ("small", "small")):
        tp = cal.tiers[v3_key]
        expected = tp.stress_mult * (tp.half_spread_bps + tp.fee_bps_per_side) * notional / 10_000.0
        assert v3w_fill_cost(notional, v3w_tier, cal) == pytest.approx(expected)


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
