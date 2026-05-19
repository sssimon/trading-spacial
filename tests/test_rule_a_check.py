"""Tests for tools/rule_a_check.py (Rule A veto-only CLI)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools.rule_a_check import (
    CLOSE_POSITION_BLOCK_THRESHOLD,
    CLOSE_POSITION_EXIT_THRESHOLD,
    compute_rule_a,
    get_most_recent_closed_bar,
    load_position,
)


def _bar(o: float, h: float, l: float, c: float) -> dict:
    return {"open_time": 0, "open": o, "high": h, "low": l, "close": c}


class TestThresholdConstants:
    def test_block_threshold(self):
        assert CLOSE_POSITION_BLOCK_THRESHOLD == 0.7

    def test_exit_threshold(self):
        assert CLOSE_POSITION_EXIT_THRESHOLD == 0.5


class TestRuleALongHold:
    def test_green_strong_bar_triggers_hold(self):
        """Bar green AND close near top → HOLD (the green-bar bias trigger)."""
        bar = _bar(100, 105, 99, 104)  # green, close at top
        # close_position = (104-99)/(105-99) = 5/6 ≈ 0.83 > 0.7
        ruling = compute_rule_a(bar, "LONG")
        assert ruling["recommendation"] == "HOLD"
        assert ruling["bar_color"] == "green"
        assert ruling["close_position"] == pytest.approx(0.8333, abs=0.001)
        assert "veto active" in ruling["reasoning"].lower()

    def test_green_bar_close_at_literal_top(self):
        bar = _bar(100, 105, 99, 105)  # close == high
        ruling = compute_rule_a(bar, "LONG")
        assert ruling["recommendation"] == "HOLD"
        assert ruling["close_position"] == 1.0


class TestRuleALongExit:
    def test_red_bar_triggers_exit_ok(self):
        bar = _bar(100, 102, 95, 96)  # red, close in lower range
        ruling = compute_rule_a(bar, "LONG")
        assert ruling["recommendation"] == "EXIT_OK"
        assert ruling["bar_color"] == "red"

    def test_green_bar_close_low_triggers_exit_ok(self):
        """Green bar but close_position < 0.5 also triggers EXIT_OK."""
        bar = _bar(100, 110, 99, 102)  # green, but close_pos = 3/11 ≈ 0.27 < 0.5
        ruling = compute_rule_a(bar, "LONG")
        assert ruling["recommendation"] == "EXIT_OK"
        assert ruling["bar_color"] == "green"
        assert ruling["close_position"] < CLOSE_POSITION_EXIT_THRESHOLD


class TestRuleALongAmbiguous:
    def test_green_bar_mid_range_is_ambiguous(self):
        """Green bar with close_position in [0.5, 0.7] gray zone."""
        bar = _bar(100, 105, 99, 102.5)  # green, close_pos = 3.5/6 ≈ 0.58
        ruling = compute_rule_a(bar, "LONG")
        assert ruling["recommendation"] == "AMBIGUOUS"
        assert CLOSE_POSITION_EXIT_THRESHOLD <= ruling["close_position"] <= CLOSE_POSITION_BLOCK_THRESHOLD

    def test_doji_zero_range_bar(self):
        bar = _bar(100, 100, 100, 100)
        ruling = compute_rule_a(bar, "LONG")
        assert ruling["recommendation"] == "AMBIGUOUS"
        assert ruling["bar_color"] == "doji"
        assert ruling["close_position"] is None


class TestRuleAShortNotApplicable:
    def test_short_position_not_applicable(self):
        """Per findings §5: Rule A LONG-only, SHORT exits had no green-bar bias."""
        bar = _bar(100, 102, 98, 101)
        ruling = compute_rule_a(bar, "SHORT")
        assert ruling["recommendation"] == "NOT_APPLICABLE"
        assert ruling["rule_a_applicable"] is False
        assert "short" in ruling["reasoning"].lower()


class TestRuleAUnknownDirection:
    def test_invalid_direction_not_applicable(self):
        bar = _bar(100, 102, 98, 101)
        ruling = compute_rule_a(bar, "FLAT")
        assert ruling["recommendation"] == "NOT_APPLICABLE"


# ---------------------------------------------------------------------------
# DB integration with synthetic SQLite fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def synthetic_signals_db(tmp_path: Path) -> Path:
    """positions table with sample LONG open + closed positions."""
    db_path = tmp_path / "signals.db"
    con = sqlite3.connect(db_path)
    con.execute("""
        CREATE TABLE positions (
            id INTEGER PRIMARY KEY,
            symbol TEXT, direction TEXT, status TEXT,
            entry_price REAL, entry_ts TEXT,
            sl_price REAL, tp_price REAL, size_usd REAL,
            exit_price REAL, exit_ts TEXT, exit_reason TEXT,
            pnl_usd REAL, pnl_pct REAL,
            tenant_id INTEGER
        )
    """)
    con.executemany("""
        INSERT INTO positions
        (id, symbol, direction, status, entry_price, entry_ts, size_usd, tenant_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1)
    """, [
        (1, "BTCUSDT", "LONG", "open", 80000.0, "2026-05-15T10:00:00+00:00", 500),
        (2, "ETHUSDT", "LONG", "open", 2300.0, "2026-05-15T11:00:00+00:00", 300),
        (3, "BTCUSDT", "SHORT", "open", 81000.0, "2026-05-15T12:00:00+00:00", 400),
    ])
    con.commit()
    con.close()
    return db_path


@pytest.fixture
def synthetic_ohlcv_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "ohlcv.db"
    con = sqlite3.connect(db_path)
    con.execute("""
        CREATE TABLE ohlcv (
            symbol TEXT, timeframe TEXT, open_time INTEGER,
            open REAL, high REAL, low REAL, close REAL, volume REAL,
            provider TEXT, fetched_at INTEGER
        )
    """)
    # BTC 1h bar with green-strong pattern (HOLD trigger)
    base_ts_ms = int(datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
    con.executemany(
        "INSERT INTO ohlcv VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            ("BTCUSDT", "1h", base_ts_ms, 80000.0, 80500.0, 79900.0, 80400.0, 0.0, "synth", 0),
            ("BTCUSDT", "1h", base_ts_ms + 3600 * 1000, 80400.0, 80800.0, 80300.0, 80700.0, 0.0, "synth", 0),
        ],
    )
    con.commit()
    con.close()
    return db_path


class TestLoadPosition:
    def test_load_by_id(self, synthetic_signals_db: Path):
        pos = load_position(synthetic_signals_db, position_id=1, symbol=None)
        assert pos is not None
        assert pos["id"] == 1
        assert pos["symbol"] == "BTCUSDT"

    def test_load_by_symbol_returns_open(self, synthetic_signals_db: Path):
        pos = load_position(synthetic_signals_db, position_id=None, symbol="ETHUSDT")
        assert pos["id"] == 2

    def test_no_match_returns_none(self, synthetic_signals_db: Path):
        pos = load_position(synthetic_signals_db, position_id=999, symbol=None)
        assert pos is None


class TestGetMostRecentClosedBar:
    def test_returns_most_recent(self, synthetic_ohlcv_db: Path):
        # as_of well after both bars closed
        as_of = datetime(2026, 5, 15, 15, 0, tzinfo=timezone.utc)
        bar = get_most_recent_closed_bar(synthetic_ohlcv_db, "BTCUSDT", as_of=as_of)
        assert bar is not None
        # Second bar (13:00-14:00) should be closed by 15:00
        expected_ms = int(datetime(2026, 5, 15, 13, 0, tzinfo=timezone.utc).timestamp() * 1000)
        assert bar["open_time"] == expected_ms

    def test_no_bar_returns_none_for_unknown_symbol(self, synthetic_ohlcv_db: Path):
        as_of = datetime(2026, 5, 15, 15, 0, tzinfo=timezone.utc)
        bar = get_most_recent_closed_bar(synthetic_ohlcv_db, "UNKNOWN", as_of=as_of)
        assert bar is None
