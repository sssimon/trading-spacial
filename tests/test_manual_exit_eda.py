"""Tests for tools/manual_exit_eda.py (descriptive EDA).

Synthetic fixtures only — NO touching papá's signals.db.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tools.manual_exit_eda import (
    CURATED_10,
    _parse_position_ts,
    _stats,
    compute_d1_hold_time,
    compute_d2_sl_tp_distance,
    compute_d4_per_symbol,
    compute_position_excursion,
)


class TestStats:
    def test_empty_list(self):
        s = _stats([])
        assert s["n"] == 0
        assert s["median"] is None
        assert s["mean"] is None

    def test_single_value(self):
        s = _stats([5.0])
        assert s["n"] == 1
        assert s["median"] == 5.0
        assert s["mean"] == 5.0
        assert s["min"] == s["max"] == 5.0

    def test_multiple_values(self):
        s = _stats([1.0, 2.0, 3.0, 4.0, 5.0])
        assert s["n"] == 5
        assert s["median"] == 3.0
        assert s["mean"] == 3.0
        assert s["p25"] == 2.0
        assert s["p75"] == 4.0
        assert s["min"] == 1.0
        assert s["max"] == 5.0


class TestParsePositionTs:
    def test_iso_with_offset(self):
        dt = _parse_position_ts("2026-03-30T22:33:27.833864+00:00")
        assert dt.year == 2026 and dt.hour == 22

    def test_z_suffix(self):
        dt = _parse_position_ts("2026-01-15T10:05:00Z")
        assert dt.year == 2026 and dt.hour == 10
        assert dt.tzinfo is not None


class TestPositionExcursion:
    def _bars(self, prices: list[tuple[float, float]]) -> list[dict]:
        """Build bars from (high, low) pairs."""
        return [
            {"open_time": i * 3600 * 1000, "open": (h + l) / 2,
             "high": h, "low": l, "close": (h + l) / 2}
            for i, (h, l) in enumerate(prices)
        ]

    def test_long_winning_excursion(self):
        """LONG entry at $100, price goes to $110 max, $98 min, exit at $108.
        max_favorable = 10%, max_adverse = 2%, capture_rate = 80%."""
        pos = {
            "entry_price": 100.0, "pnl_pct": 8.0, "direction": "LONG",
        }
        bars = self._bars([(105, 99), (110, 102), (108, 98), (108, 105)])
        exc = compute_position_excursion(pos, bars)
        assert exc["max_favorable_pct"] == pytest.approx(10.0)
        assert exc["max_adverse_pct"] == pytest.approx(2.0)
        assert exc["capture_rate_pct"] == pytest.approx(80.0)
        assert exc["n_bars"] == 4

    def test_short_winning_excursion(self):
        """SHORT entry at $100, price goes to $95 min (favorable), $105 max (adverse),
        exit at $98 → realized +2%, max_favorable 5%, capture 40%."""
        pos = {
            "entry_price": 100.0, "pnl_pct": 2.0, "direction": "SHORT",
        }
        bars = self._bars([(102, 95), (105, 96), (101, 98)])
        exc = compute_position_excursion(pos, bars)
        assert exc["max_favorable_pct"] == pytest.approx(5.0)
        assert exc["max_adverse_pct"] == pytest.approx(5.0)
        assert exc["capture_rate_pct"] == pytest.approx(40.0)

    def test_no_bars(self):
        exc = compute_position_excursion(
            {"entry_price": 100.0, "pnl_pct": 0, "direction": "LONG"}, [],
        )
        assert exc["n_bars"] == 0
        assert exc["max_favorable_pct"] is None
        assert exc["capture_rate_pct"] is None

    def test_capture_negative_when_favorable_positive_but_realized_loss(self):
        """Price went up first (favorable), then down, exited at loss."""
        pos = {
            "entry_price": 100.0, "pnl_pct": -3.0, "direction": "LONG",
        }
        # Max favorable +5%, exit at -3%
        bars = self._bars([(105, 99), (103, 96), (98, 96)])
        exc = compute_position_excursion(pos, bars)
        assert exc["max_favorable_pct"] == pytest.approx(5.0)
        assert exc["capture_rate_pct"] == pytest.approx(-60.0)


class TestD1HoldTime:
    def test_basic_winners_vs_losers(self):
        positions = [
            {"id": 1, "symbol": "BTCUSDT", "direction": "LONG",
             "entry_ts": "2026-04-01T00:00:00+00:00",
             "exit_ts": "2026-04-01T05:00:00+00:00",
             "pnl_usd": 10.0, "pnl_pct": 2.0},
            {"id": 2, "symbol": "ETHUSDT", "direction": "LONG",
             "entry_ts": "2026-04-02T00:00:00+00:00",
             "exit_ts": "2026-04-02T10:00:00+00:00",
             "pnl_usd": -5.0, "pnl_pct": -1.0},
        ]
        d1 = compute_d1_hold_time(positions)
        assert d1["all"]["n"] == 2
        assert d1["winners"]["n"] == 1
        assert d1["winners"]["median"] == pytest.approx(5.0)
        assert d1["losers"]["n"] == 1
        assert d1["losers"]["median"] == pytest.approx(10.0)


class TestD2SLTPDistance:
    def test_long_winner_captures_60pct_of_tp(self):
        positions = [
            {"id": 1, "symbol": "BTC", "direction": "LONG",
             "entry_price": 100.0, "exit_price": 106.0,  # +6%
             "tp_price": 110.0, "sl_price": 95.0,  # TP +10%, SL -5%
             "pnl_usd": 6.0, "pnl_pct": 6.0},
        ]
        d2 = compute_d2_sl_tp_distance(positions)
        # realized_distance = 6, tp_distance = 10 → 60% captured
        assert d2["pct_of_tp_captured_winners"]["median"] == pytest.approx(60.0)

    def test_long_loser_travels_80pct_of_sl(self):
        positions = [
            {"id": 1, "symbol": "BTC", "direction": "LONG",
             "entry_price": 100.0, "exit_price": 96.0,  # -4%
             "tp_price": 110.0, "sl_price": 95.0,  # SL distance = 5
             "pnl_usd": -4.0, "pnl_pct": -4.0},
        ]
        d2 = compute_d2_sl_tp_distance(positions)
        assert d2["pct_of_sl_traveled_losers"]["median"] == pytest.approx(80.0)

    def test_null_tp_excluded_from_capture(self):
        positions = [
            {"id": 1, "symbol": "BTC", "direction": "LONG",
             "entry_price": 100.0, "exit_price": 106.0,
             "tp_price": None, "sl_price": 95.0,
             "pnl_usd": 6.0, "pnl_pct": 6.0},
        ]
        d2 = compute_d2_sl_tp_distance(positions)
        assert d2["pct_of_tp_captured_winners"]["n"] == 0
        assert d2["excluded"]["null_tp_winners"] == 1


class TestD4PerSymbol:
    def test_grouping_by_symbol(self):
        positions = [
            {"id": 1, "symbol": "BTCUSDT", "direction": "LONG",
             "entry_ts": "2026-04-01T00:00:00+00:00",
             "exit_ts": "2026-04-01T05:00:00+00:00",
             "entry_price": 100.0, "exit_price": 105.0,
             "tp_price": 110.0, "sl_price": 95.0,
             "pnl_usd": 5.0, "pnl_pct": 5.0},
            {"id": 2, "symbol": "BTCUSDT", "direction": "LONG",
             "entry_ts": "2026-04-02T00:00:00+00:00",
             "exit_ts": "2026-04-02T03:00:00+00:00",
             "entry_price": 100.0, "exit_price": 96.0,
             "tp_price": 110.0, "sl_price": 95.0,
             "pnl_usd": -4.0, "pnl_pct": -4.0},
            {"id": 3, "symbol": "ETHUSDT", "direction": "LONG",
             "entry_ts": "2026-04-03T00:00:00+00:00",
             "exit_ts": "2026-04-03T10:00:00+00:00",
             "entry_price": 100.0, "exit_price": 105.0,
             "tp_price": 110.0, "sl_price": 95.0,
             "pnl_usd": 5.0, "pnl_pct": 5.0},
        ]
        d1 = compute_d1_hold_time(positions)
        d2 = compute_d2_sl_tp_distance(positions)
        # Synthesize d3 with empty per_position (D3 requires real bars; not in this test)
        d3 = {"per_position": [
            {"id": 1, "max_favorable_pct": 6.0, "capture_rate_pct": 83.33},
            {"id": 2, "max_favorable_pct": 1.0, "capture_rate_pct": None},
            {"id": 3, "max_favorable_pct": 7.0, "capture_rate_pct": 71.43},
        ]}
        d4 = compute_d4_per_symbol(d1, d2, d3)
        assert "BTCUSDT" in d4
        assert "ETHUSDT" in d4
        assert d4["BTCUSDT"]["n_trades"] == 2
        assert d4["BTCUSDT"]["n_winners"] == 1
        assert d4["BTCUSDT"]["n_losers"] == 1
        assert d4["ETHUSDT"]["n_trades"] == 1


class TestCuratedConstants:
    def test_curated_10_size(self):
        assert len(CURATED_10) == 10

    def test_known_members(self):
        for sym in ("BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT",
                    "UNIUSDT", "XLMUSDT", "PENDLEUSDT", "JUPUSDT", "RUNEUSDT"):
            assert sym in CURATED_10
