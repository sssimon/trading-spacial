"""Integration tests for simulate_strategy with cost flags (A.0.2, #277).

These tests use the cached `data/ohlcv.db` and a fixed BTCUSDT 2024-H1 window
matching `tests/test_backtest_refactor_parity.py` so the post-cost numbers can
be reasoned about side-by-side with the legacy pin.
"""
import os
from datetime import datetime, timezone

import pytest


OHLCV_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "ohlcv.db",
)

requires_ohlcv = pytest.mark.skipif(
    not os.path.exists(OHLCV_DB), reason="requires cached market data (data/ohlcv.db)",
)


@pytest.fixture
def btc_data(tmp_path, monkeypatch):
    from backtest import get_cached_data
    import btc_api

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    btc_api.init_db()

    symbol = "BTCUSDT"
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 3, 1, tzinfo=timezone.utc)
    data_start = datetime(2023, 1, 1, tzinfo=timezone.utc)

    df1h = get_cached_data(symbol, "1h", start_date=data_start)
    df4h = get_cached_data(symbol, "4h", start_date=data_start)
    df5m = get_cached_data(symbol, "5m", start_date=data_start)
    df1d = get_cached_data(symbol, "1d", start_date=data_start)

    if df1h.empty or df4h.empty or df5m.empty:
        pytest.skip("BTCUSDT market data not cached in data/ohlcv.db")

    return {
        "symbol": symbol, "start": start, "end": end,
        "df1h": df1h, "df4h": df4h, "df5m": df5m, "df1d": df1d,
    }


@requires_ohlcv
def test_simulate_strategy_with_costs_disabled_matches_baseline_pin(btc_data):
    """When all cost flags are False, simulate_strategy is byte-identical to the
    pre-A.0.2 path. Parity with the existing pin in test_backtest_refactor_parity
    is the strict regression net for the cost-off branch."""
    from backtest import simulate_strategy

    trades, equity = simulate_strategy(
        btc_data["df1h"], btc_data["df4h"], btc_data["df5m"], btc_data["symbol"],
        sim_start=btc_data["start"], sim_end=btc_data["end"],
        df1d=btc_data["df1d"],
        enable_slippage=False, enable_spread=False, enable_fees=False,
    )

    EXPECTED_TRADE_COUNT = 24
    EXPECTED_FINAL_EQUITY = 11021.66
    EXPECTED_NET_PNL = 1021.66
    assert len(trades) == EXPECTED_TRADE_COUNT
    assert equity[-1]["equity"] == pytest.approx(EXPECTED_FINAL_EQUITY, rel=1e-4)
    net_pnl = round(sum(t["pnl_usd"] for t in trades), 2)
    assert net_pnl == pytest.approx(EXPECTED_NET_PNL, abs=0.01)


@requires_ohlcv
def test_simulate_strategy_costs_disabled_does_not_emit_cost_fields(btc_data):
    """Trade dict shape is unchanged when costs are disabled — preserves
    backwards compatibility for callers that introspect trade keys."""
    from backtest import simulate_strategy

    trades, _ = simulate_strategy(
        btc_data["df1h"], btc_data["df4h"], btc_data["df5m"], btc_data["symbol"],
        sim_start=btc_data["start"], sim_end=btc_data["end"],
        df1d=btc_data["df1d"],
        enable_slippage=False, enable_spread=False, enable_fees=False,
    )
    assert trades, "expected non-empty trades for fixture window"
    cost_keys = {
        "entry_slippage_bps", "exit_slippage_bps", "entry_spread_bps",
        "exit_spread_bps", "fee_bps", "total_cost_bps", "total_cost_usd",
        "gross_pnl_usd", "entry_notional_usd",
    }
    assert cost_keys.isdisjoint(trades[0].keys())


@requires_ohlcv
def test_simulate_strategy_with_costs_enabled_reduces_net_pnl(btc_data):
    """Flags-on net_pnl is strictly less than flags-off net_pnl on the same
    fixture window."""
    from backtest import simulate_strategy

    trades_off, _ = simulate_strategy(
        btc_data["df1h"], btc_data["df4h"], btc_data["df5m"], btc_data["symbol"],
        sim_start=btc_data["start"], sim_end=btc_data["end"],
        df1d=btc_data["df1d"],
        enable_slippage=False, enable_spread=False, enable_fees=False,
    )
    trades_on, _ = simulate_strategy(
        btc_data["df1h"], btc_data["df4h"], btc_data["df5m"], btc_data["symbol"],
        sim_start=btc_data["start"], sim_end=btc_data["end"],
        df1d=btc_data["df1d"],
        enable_slippage=True, enable_spread=True, enable_fees=True,
    )

    pnl_off = sum(t["pnl_usd"] for t in trades_off)
    pnl_on = sum(t["pnl_usd"] for t in trades_on)
    assert pnl_on < pnl_off, (
        f"costs-on net_pnl ({pnl_on:.2f}) must be strictly less than "
        f"costs-off ({pnl_off:.2f})"
    )


@requires_ohlcv
def test_simulate_strategy_with_costs_enabled_emits_cost_fields(btc_data):
    """Each trade dict carries per-component cost fields when flags are on."""
    from backtest import simulate_strategy

    trades, _ = simulate_strategy(
        btc_data["df1h"], btc_data["df4h"], btc_data["df5m"], btc_data["symbol"],
        sim_start=btc_data["start"], sim_end=btc_data["end"],
        df1d=btc_data["df1d"],
        enable_slippage=True, enable_spread=True, enable_fees=True,
    )
    assert trades

    required = {
        "entry_slippage_bps", "exit_slippage_bps",
        "entry_spread_bps", "exit_spread_bps",
        "fee_bps", "total_cost_bps", "total_cost_usd",
        "gross_pnl_usd", "entry_notional_usd",
    }
    for t in trades:
        missing = required - set(t.keys())
        assert not missing, f"trade missing fields {missing}: {t}"
        # Sanity: total_cost_bps > 0 (BTCUSDT major: 2 base + 1.5 spread + 10 fee per side ≈ 27 bps)
        assert t["total_cost_bps"] > 0
        # gross > net (pnl_usd is net)
        assert t["gross_pnl_usd"] >= t["pnl_usd"], (
            "gross_pnl_usd must be ≥ net pnl_usd (cost is non-negative)"
        )


@requires_ohlcv
def test_simulate_strategy_with_costs_enabled_net_equals_gross_minus_cost(btc_data):
    """Per-trade arithmetic: pnl_usd == gross_pnl_usd - total_cost_usd."""
    from backtest import simulate_strategy

    trades, _ = simulate_strategy(
        btc_data["df1h"], btc_data["df4h"], btc_data["df5m"], btc_data["symbol"],
        sim_start=btc_data["start"], sim_end=btc_data["end"],
        df1d=btc_data["df1d"],
        enable_slippage=True, enable_spread=True, enable_fees=True,
    )
    for t in trades:
        expected = round(t["gross_pnl_usd"] - t["total_cost_usd"], 2)
        assert t["pnl_usd"] == pytest.approx(expected, abs=0.01), (
            f"pnl_usd={t['pnl_usd']}, gross={t['gross_pnl_usd']}, "
            f"cost_usd={t['total_cost_usd']}"
        )


@requires_ohlcv
def test_calculate_metrics_includes_cost_aggregates_when_trades_carry_costs(btc_data):
    """Mini-contract (A.0.2 → A.0.3): calculate_metrics surfaces aggregate cost
    fields when individual trades carry per-component costs. The fields are:
    total_cost_bps_mean, total_cost_usd_sum, entry_slippage_bps_mean,
    exit_slippage_bps_mean, entry_spread_bps_mean, exit_spread_bps_mean,
    fee_bps_mean, gross_net_pnl_diff_usd."""
    from backtest import simulate_strategy, calculate_metrics

    trades, equity = simulate_strategy(
        btc_data["df1h"], btc_data["df4h"], btc_data["df5m"], btc_data["symbol"],
        sim_start=btc_data["start"], sim_end=btc_data["end"],
        df1d=btc_data["df1d"],
        enable_slippage=True, enable_spread=True, enable_fees=True,
    )
    metrics = calculate_metrics(trades, equity)

    expected = {
        "total_cost_bps_mean", "total_cost_usd_sum",
        "entry_slippage_bps_mean", "exit_slippage_bps_mean",
        "entry_spread_bps_mean", "exit_spread_bps_mean",
        "fee_bps_mean", "gross_net_pnl_diff_usd",
    }
    missing = expected - set(metrics.keys())
    assert not missing, f"calculate_metrics missing cost aggregates: {missing}"
    assert metrics["total_cost_bps_mean"] > 0
    assert metrics["total_cost_usd_sum"] > 0


@requires_ohlcv
def test_calculate_metrics_omits_cost_aggregates_for_costless_trades(btc_data):
    """Backwards compat: when trades have no cost fields (legacy callers with
    flags off), calculate_metrics returns the legacy shape — no cost
    aggregates leak as zeros that downstream might mis-interpret as signal."""
    from backtest import simulate_strategy, calculate_metrics

    trades, equity = simulate_strategy(
        btc_data["df1h"], btc_data["df4h"], btc_data["df5m"], btc_data["symbol"],
        sim_start=btc_data["start"], sim_end=btc_data["end"],
        df1d=btc_data["df1d"],
        enable_slippage=False, enable_spread=False, enable_fees=False,
    )
    metrics = calculate_metrics(trades, equity)

    cost_keys = {
        "total_cost_bps_mean", "total_cost_usd_sum",
        "entry_slippage_bps_mean", "exit_slippage_bps_mean",
        "entry_spread_bps_mean", "exit_spread_bps_mean",
        "fee_bps_mean", "gross_net_pnl_diff_usd",
    }
    assert cost_keys.isdisjoint(metrics.keys()), (
        "cost aggregates must be omitted when trades carry no cost fields"
    )


@requires_ohlcv
def test_calculate_metrics_does_not_use_a03_reserved_names(btc_data):
    """Mini-contract enforcement (#277 + #278): A.0.2 must NOT define any of
    the field names reserved for A.0.3. If A.0.3 needs more reserved names,
    surface in the PR and let the reviewer arbitrate — do not silently extend."""
    from backtest import simulate_strategy, calculate_metrics

    trades, equity = simulate_strategy(
        btc_data["df1h"], btc_data["df4h"], btc_data["df5m"], btc_data["symbol"],
        sim_start=btc_data["start"], sim_end=btc_data["end"],
        df1d=btc_data["df1d"],
        enable_slippage=True, enable_spread=True, enable_fees=True,
    )
    metrics = calculate_metrics(trades, equity)

    A03_RESERVED = {
        "sharpe_deflated", "sortino_deflated", "prob_sr_gt_0",
        "calmar_deflated_approx", "n_effective", "sigma_sr_trials",
        "calmar", "calmar_ratio",  # raw Calmar belongs to A.0.3 too per spec
    }
    leaks = A03_RESERVED & set(metrics.keys())
    assert not leaks, (
        f"A.0.2 must not define A.0.3-reserved fields; leaked: {leaks}"
    )


@requires_ohlcv
def test_simulate_strategy_with_costs_default_flags_are_on(btc_data):
    """Spec §1: 'enable_slippage, enable_spread, both default true'. Calling
    without explicit flags must apply costs by default."""
    from backtest import simulate_strategy

    trades, _ = simulate_strategy(
        btc_data["df1h"], btc_data["df4h"], btc_data["df5m"], btc_data["symbol"],
        sim_start=btc_data["start"], sim_end=btc_data["end"],
        df1d=btc_data["df1d"],
    )
    assert trades
    assert "total_cost_bps" in trades[0], (
        "default behavior must include cost fields"
    )
    assert trades[0]["total_cost_bps"] > 0
