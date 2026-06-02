"""Unit tests for tools/ks_stress_replay/falsify_cost_bound.py.

Uses synthetic position dicts — CI-runnable without the server DB.
"""
import math
import pytest
from tools.ks_stress_replay.falsify_cost_bound import (
    score_positions, assert_no_sign_inversion, MANDATORY_LOWER_BOUND_BPS,
    EXPECTED_MIN, InsufficientDataError, BoundFalsifiedError, looseness_report,
)


def _pos(symbol, pnl_usd, pnl_pct, size_usd=644.0, liq=1_000_000.0):
    return {"symbol": symbol, "direction": "SHORT", "pnl_usd": pnl_usd,
            "pnl_pct": pnl_pct, "size_usd": size_usd,
            "entry_liquidity_per_min": liq, "exit_liquidity_per_min": liq}


class TestFalsifyHarness:
    def test_mandatory_lower_bound_is_external_fee(self):
        # 2 * published taker fee (5.0) = 10.0 RT bps, NOT the model floor.
        assert MANDATORY_LOWER_BOUND_BPS == 10.0

    def test_insufficient_data_aborts(self):
        with pytest.raises(InsufficientDataError):
            assert_no_sign_inversion(score_positions([_pos("AVAXUSDT", 5.0, 0.5)]))

    def test_v3_preserves_winner_sign(self):
        # A winner in price stays a winner after v3 cost (floor-dominated).
        # gross = 20 * 6.0 = 120.0 > NOISE_BAND_USD=5.0 -> symbol is evaluated.
        rows = [_pos("AVAXUSDT", 6.0, 0.5) for _ in range(20)]
        scored = score_positions(rows)
        assert_no_sign_inversion(scored)  # should NOT raise

    def test_inflated_cost_inverts_sign_is_caught(self):
        # Simulate a model that overcharges (inject huge cost) -> inversion -> raises.
        # pnl_usd=0.4 -> gross = 20 * 0.4 = 8.0 > NOISE_BAND_USD=5.0 (evaluated).
        # force_cost_bps=500 -> cost_usd = 500*644/10000 = 32.2 per position.
        # net = 8.0 - 20*32.2 = 8.0 - 644.0 = -636.0 < 0 -> sign inversion caught.
        # (Original spec used pnl_usd=0.2 -> gross=4.0 <= 5.0 noise band -> skipped,
        #  so the test would never raise. Fixed to 0.4 to make gross just above noise.)
        rows = [_pos("AVAXUSDT", 0.4, 0.03) for _ in range(20)]
        scored = score_positions(rows, force_cost_bps=500.0)  # v2-like overcharge
        with pytest.raises(AssertionError, match="sign inversion"):
            assert_no_sign_inversion(scored)

    def test_falsification_error_is_assertionerror(self):
        from tools.ks_stress_replay.falsify_cost_bound import BoundFalsifiedError
        assert issubclass(BoundFalsifiedError, AssertionError)

    def test_nan_pnl_rejected(self):
        with pytest.raises(ValueError, match="non-finite pnl_usd"):
            score_positions([_pos("AVAXUSDT", float("nan"), 0.5)])

    def test_zero_size_rejected(self):
        with pytest.raises(ValueError, match="size_usd"):
            score_positions([_pos("AVAXUSDT", 5.0, 0.5, size_usd=0.0)])

    def test_missing_key_rejected(self):
        with pytest.raises(ValueError, match="missing required keys"):
            score_positions([{"symbol": "AVAXUSDT", "pnl_usd": 5.0}])

    def test_unscored_noise_symbols_reported(self):
        # 20 rows, gross 20*0.1=2.0 < NOISE_BAND 5.0 -> symbol skipped & REPORTED
        rows = [_pos("AVAXUSDT", 0.1, 0.01) for _ in range(20)]
        summary = assert_no_sign_inversion(score_positions(rows))
        assert "AVAXUSDT" in summary["skipped_noise"]
        assert summary["checked"] == []


class TestHarnessEntrypoint:
    def test_load_closed_shorts_case_insensitive_and_window(self, tmp_path):
        import sqlite3
        from tools.ks_stress_replay.falsify_cost_bound import _load_closed_shorts_from_db
        db = tmp_path / "signals.db"
        con = sqlite3.connect(str(db))
        con.execute("CREATE TABLE positions (symbol TEXT, direction TEXT, status TEXT, "
                    "pnl_usd REAL, pnl_pct REAL, size_usd REAL, entry_ts TEXT, exit_ts TEXT)")
        # closed short in-window (lowercase direction), an open one, a long, an out-of-window one
        con.executemany(
            "INSERT INTO positions VALUES (?,?,?,?,?,?,?,?)",
            [
                ("AVAXUSDT", "short", "closed", 5.0, 0.5, 644.0, "2026-05-22", "2026-05-23"),
                ("AVAXUSDT", "short", "open",   0.0, 0.0, 644.0, "2026-05-22", None),
                ("BTCUSDT",  "long",  "closed", 5.0, 0.5, 644.0, "2026-05-22", "2026-05-23"),
                ("XLMUSDT",  "short", "closed", 1.0, 0.2, 644.0, "2025-01-01", "2025-01-02"),  # pre-window
            ],
        )
        con.commit(); con.close()
        rows = _load_closed_shorts_from_db(str(db))
        # only the in-window closed short survives the filter
        syms = [r["symbol"] for r in rows]
        assert syms == ["AVAXUSDT"]
        assert rows[0]["direction"].upper() == "SHORT"

    def test_liquidity_proxy_at_returns_value_or_nan(self):
        import math
        import pandas as pd
        from tools.ks_stress_replay.falsify_cost_bound import _liquidity_proxy_at
        idx = pd.date_range("2026-05-01", periods=800, freq="1h", tz="UTC")
        df = pd.DataFrame({"close": [100.0]*800, "volume": [6000.0]*800}, index=idx)
        # at a ts well past min_periods, proxy ~ (100*6000)/60 = 10000 usd/min
        ts = idx[799]
        v = _liquidity_proxy_at(df, ts)
        assert v == pytest.approx(10000.0, rel=1e-6)
        # before min_periods (bar 10) -> NaN
        assert math.isnan(_liquidity_proxy_at(df, idx[10]))
        # empty df -> NaN
        assert math.isnan(_liquidity_proxy_at(pd.DataFrame(), ts))

    def test_liquidity_proxy_at_handles_tz_mismatch(self):
        # get_cached_data returns a tz-NAIVE index; main() passes a tz-AWARE ts.
        # _liquidity_proxy_at must normalize and NOT raise.
        import pandas as pd
        from tools.ks_stress_replay.falsify_cost_bound import _liquidity_proxy_at
        idx = pd.date_range("2026-05-01", periods=800, freq="1h")  # tz-NAIVE
        df = pd.DataFrame({"close": [100.0]*800, "volume": [6000.0]*800}, index=idx)
        ts_aware = pd.Timestamp("2026-06-01", tz="UTC")  # tz-AWARE
        v = _liquidity_proxy_at(df, ts_aware)
        assert v == pytest.approx(10000.0, rel=1e-6)


class TestRealModelGateFires:
    def test_real_model_gate_fires_on_inverting_winner(self):
        # RUNEUSDT (small tier, RT floor 30 bps).
        # Tiny winners: $1000 @ +0.2% gross move => pnl_usd=$2.
        # v3 floor for small tier: stress_mult=1.0, half_spread=10.0, fee=5.0
        #   floor per leg = 1.0*(10.0+5.0)=15.0 bps; RT floor = 30.0 bps.
        # Impact tail at liq=1e6, size=1000: order/v_daily=1000/(1e6*1440)~6.94e-7
        #   tail~1.5*800*sqrt(6.94e-7)~1.0 bps per fill; RT tail ~2 bps.
        # total_cost_bps ~ 32 bps; cost_usd = 32*1000/10000 = $3.20 > $2.00 gross.
        # 20 rows: gross=40.0 > NOISE_BAND=5.0; net = 40 - 20*3.2 = -24 < 0 => INVERSION.
        # No force_cost_bps: exercises load_calibration + compute_trade_costs.
        from tools.ks_stress_replay.falsify_cost_bound import (
            score_positions, assert_no_sign_inversion, BoundFalsifiedError)
        rows = [
            {"symbol": "RUNEUSDT", "direction": "SHORT", "pnl_usd": 2.0,
             "pnl_pct": 0.2, "size_usd": 1000.0,
             "entry_liquidity_per_min": 1_000_000.0,
             "exit_liquidity_per_min": 1_000_000.0}
            for _ in range(20)
        ]
        scored = score_positions(rows)  # REAL model — no force_cost_bps
        # Sanity: confirm per-trade cost really does exceed gross ($2.00)
        assert scored[0]["v3_cost_usd"] > 2.0, (
            f"Expected real v3 cost > $2 for RUNEUSDT@$1000 +0.2% move, "
            f"got {scored[0]['v3_cost_usd']:.4f}. Adjust pnl_pct DOWN.")
        with pytest.raises(BoundFalsifiedError, match="sign inversion"):
            assert_no_sign_inversion(scored)


class TestLooseness:
    def test_looseness_report_shape_and_r_i_computed(self):
        # 20 winners: pnl_usd=6.0, pnl_pct=0.5 (move=50bps). AVAXUSDT mid-tier.
        # v3 cost ~18bps RT floor (mid: 2*(4+5)=18) + tiny tail.
        # R_i ~ 18/50 ~ 0.36 << 1 (well-bound, not inverting).
        rows = [_pos("AVAXUSDT", 6.0, 0.5) for _ in range(20)]
        scored = score_positions(rows)
        report = looseness_report(scored)
        assert report["n_winners"] == 20
        assert report["R_i_max"] is not None
        assert report["R_i_median"] is not None
        assert report["n_winners_inverting_per_trade"] == 0  # v3 should not invert these
        assert len(report["per_winner"]) == 20
        assert "R_i" in report["per_winner"][0]
        assert "spread_band_bps" in report["per_winner"][0]
        # R_i should be well below 1 for a $6 winner on a $644 mid-tier position
        assert report["R_i_max"] < 1.0

    def test_looseness_report_no_winners(self):
        # All losers -> n_winners=0, all None
        rows = [_pos("AVAXUSDT", -5.0, -0.5) for _ in range(20)]
        scored = score_positions(rows)
        report = looseness_report(scored)
        assert report["n_winners"] == 0
        assert report["R_i_max"] is None
        assert report["R_i_median"] is None
        assert report["n_winners_inverting_per_trade"] == 0

    def test_per_symbol_counts_in_summary(self):
        # RP-6: per_symbol_counts present in assert_no_sign_inversion summary.
        rows = [_pos("AVAXUSDT", 6.0, 0.5) for _ in range(20)]
        summary = assert_no_sign_inversion(score_positions(rows))
        assert "per_symbol_counts" in summary
        assert summary["per_symbol_counts"]["AVAXUSDT"] == 20

    def test_exit_liquidity_distinct_from_entry(self):
        # RP-5: rows with different entry/exit liquidity are accepted and scored.
        row = {
            "symbol": "AVAXUSDT", "direction": "SHORT",
            "pnl_usd": 6.0, "pnl_pct": 0.5, "size_usd": 644.0,
            "entry_liquidity_per_min": 1_000_000.0,
            "exit_liquidity_per_min": 500_000.0,   # deliberately different
        }
        scored = score_positions([row])
        assert len(scored) == 1
        assert scored[0]["v3_cost_usd"] > 0

    def test_exit_liquidity_defaults_to_entry_when_absent(self):
        # RP-5: backward-compat — row without exit_liquidity_per_min is accepted.
        row = {
            "symbol": "AVAXUSDT", "direction": "SHORT",
            "pnl_usd": 6.0, "pnl_pct": 0.5, "size_usd": 644.0,
            "entry_liquidity_per_min": 1_000_000.0,
            # no exit_liquidity_per_min key
        }
        scored = score_positions([row])
        assert len(scored) == 1
        assert scored[0]["v3_cost_usd"] > 0
