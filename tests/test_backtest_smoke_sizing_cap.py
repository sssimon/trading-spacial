"""End-to-end smoke for the participation cap across the curated basket.

Runs simulate_strategy across the 10 curated symbols on the same pre-holdout
18-month train segment as test_backtest_smoke_time_limit.py, with the per-symbol
`max_participation_rate` from config.defaults.json applied on top of the
existing time-limit barrier.

Assertions:
- Per symbol: 0 ≤ post_cap_count ≤ baseline (cap can only reduce, never add)
- Aggregate: post_cap_total < BASELINE_TOTAL (cap MUST reduce — else silent
  no-op bug)
- Majors (BTC/ETH cap 0.010): retention >= 80%. Cap halves worst-case sizing
  but most majors trade well below 1% participation, so binding is rare.
- Floor-cap symbols (XLM/PENDLE 0.0015): only `>= 0` — most signals are
  expected to skip; that is the structural intent of the floor cap.
- 0 NaN in pnl_usd / pnl_pct, 0 exceptions

Skipped without cached market data — guards on `data/ohlcv.db`.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone

import pytest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OHLCV_DB = os.path.join(REPO_ROOT, "data", "ohlcv.db")
CONFIG_DEFAULTS_PATH = os.path.join(REPO_ROOT, "config.defaults.json")


CURATED_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT",
    "UNIUSDT", "XLMUSDT", "PENDLEUSDT", "JUPUSDT", "RUNEUSDT",
]


# Baseline pre-cap trade counts (TL active, costs OFF, train segment
# 2023-10-01 → 2025-03-31). Hard-pinned: a future change that increases any
# per-symbol count would mean the cap stopped firing for that symbol —
# surface as a regression.
BASELINE_CLOSED_PRE_CAP: dict[str, int] = {
    "BTCUSDT": 206, "ETHUSDT": 169, "ADAUSDT": 200, "AVAXUSDT": 173,
    "DOGEUSDT": 200, "UNIUSDT": 197, "XLMUSDT": 155, "PENDLEUSDT": 241,
    "JUPUSDT":  154, "RUNEUSDT": 171,
}
BASELINE_TOTAL = sum(BASELINE_CLOSED_PRE_CAP.values())  # = 1866


@pytest.mark.skipif(
    not os.path.exists(OHLCV_DB),
    reason="requires cached market data (data/ohlcv.db)",
)
def test_smoke_sizing_cap_across_curated_symbols(tmp_path, monkeypatch):
    """Per-symbol participation cap smoke: counts ≤ baseline, no NaN/exceptions."""
    from backtest import simulate_strategy, get_cached_data
    import btc_api

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    btc_api.init_db()

    # Source-of-truth cfg (defaults). Each symbol's max_participation_rate must be
    # present here so a future config refactor cannot silently drop the field.
    with open(CONFIG_DEFAULTS_PATH) as f:
        cfg = json.load(f)

    symbol_overrides = cfg.get("symbol_overrides", {})
    for sym in CURATED_SYMBOLS:
        assert "max_participation_rate" in symbol_overrides.get(sym, {}), (
            f"{sym} must have max_participation_rate in config.defaults.json"
        )

    # Strip cooldown_hours so this smoke isolates cap-only delta. The pinned
    # BASELINE_CLOSED_PRE_CAP was captured when cooldown was global (6h via
    # COOLDOWN_H); per-symbol cooldown shifts BTC/ETH/AVAX baselines and would
    # confound the cap-only retention assertion.
    symbol_overrides = {
        sym: {k: v for k, v in ov.items() if k != "cooldown_hours"}
        for sym, ov in symbol_overrides.items()
    }
    cfg = {**cfg, "symbol_overrides": symbol_overrides}

    # Same window as the time-limit smoke for direct comparability.
    sim_start = datetime(2023, 10, 1, tzinfo=timezone.utc)
    sim_end = datetime(2025, 3, 31, tzinfo=timezone.utc)
    data_start = datetime(2022, 1, 1, tzinfo=timezone.utc)

    df1d_btc = get_cached_data("BTCUSDT", "1d", start_date=data_start)
    if df1d_btc.empty:
        pytest.skip("BTCUSDT 1d not cached")

    summary: dict[str, dict] = {}

    for symbol in CURATED_SYMBOLS:
        df1h = get_cached_data(symbol, "1h", start_date=data_start)
        df4h = get_cached_data(symbol, "4h", start_date=data_start)
        df5m = get_cached_data(symbol, "5m", start_date=data_start)
        df1d = get_cached_data(symbol, "1d", start_date=data_start)
        if df1h.empty or df4h.empty or df5m.empty:
            pytest.skip(f"{symbol} market data not cached")

        trades, _equity = simulate_strategy(
            df1h, df4h, df5m, symbol,
            sim_start=sim_start, sim_end=sim_end,
            df1d=df1d, df1d_btc=df1d_btc,
            cfg=cfg, symbol_overrides=symbol_overrides,
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

        assert isinstance(trades, list)

        # No NaN propagation: the cap-skip path must not poison downstream metrics.
        for t in trades:
            assert t["pnl_usd"] is not None and not math.isnan(t["pnl_usd"]), (
                f"NaN pnl_usd for {symbol}: {t}"
            )
            assert t["pnl_pct"] is not None and not math.isnan(t["pnl_pct"]), (
                f"NaN pnl_pct for {symbol}: {t}"
            )

        closed = [t for t in trades if t["exit_reason"] != "OPEN"]
        summary[symbol] = {"closed": len(closed)}

    # ── Per-symbol regression net: counts must not exceed pre-cap baseline ───
    for symbol in CURATED_SYMBOLS:
        post_cap = summary[symbol]["closed"]
        baseline = BASELINE_CLOSED_PRE_CAP[symbol]
        assert 0 <= post_cap <= baseline, (
            f"{symbol}: post_cap_count={post_cap} must be in [0, {baseline}] "
            f"(cap can only reduce trade count, never add)"
        )

    # ── Aggregate: cap MUST reduce total count ──────────────────────────────
    total_post_cap = sum(s["closed"] for s in summary.values())
    assert total_post_cap < BASELINE_TOTAL, (
        f"aggregate post_cap={total_post_cap} must be < pre_cap baseline={BASELINE_TOTAL}; "
        f"if equal, the cap is silently no-op for every symbol — likely a bug"
    )

    # ── Tier-specific retention bounds ──────────────────────────────────────
    # Majors (cap 0.010): observed_p99 ≈ 0.018-0.024 → cap halves worst-case;
    # high retention expected.
    btc_retention = summary["BTCUSDT"]["closed"] / BASELINE_CLOSED_PRE_CAP["BTCUSDT"]
    eth_retention = summary["ETHUSDT"]["closed"] / BASELINE_CLOSED_PRE_CAP["ETHUSDT"]
    assert btc_retention >= 0.80, (
        f"BTCUSDT retention {btc_retention:.2%} below 80% — cap firing too aggressively "
        f"on a major (count={summary['BTCUSDT']['closed']}/{BASELINE_CLOSED_PRE_CAP['BTCUSDT']})"
    )
    assert eth_retention >= 0.80, (
        f"ETHUSDT retention {eth_retention:.2%} below 80% — cap firing too aggressively "
        f"on a major (count={summary['ETHUSDT']['closed']}/{BASELINE_CLOSED_PRE_CAP['ETHUSDT']})"
    )
    # JUP volume profile is thinner than other mid-tier (DOGE/AVAX 84-85%
    # retention with the same 0.005 cap); cap binds harder. Floor catches
    # accidental drift to extreme suppression that would silently flag the
    # symbol as effectively dead.
    jup_count = summary["JUPUSDT"]["closed"]
    assert jup_count >= 30, (
        f"JUPUSDT count {jup_count} below floor 30 — cap firing more "
        f"aggressively than expected; investigate volume profile drift"
    )

    # Floor caps (XLM/PENDLE = 0.0015): most signals are expected to skip —
    # this is the structural intent of the floor cap. Only `>= 0` asserted
    # (covered above by per-symbol assert). No upper retention bound.

    # ── Print summary so post-merge readers can compare with future runs ────
    print("\n=== SMOKE SIZING CAP PER-SYMBOL SUMMARY ===")
    print(f"{'Symbol':<14} {'Post-cap':>9} {'Baseline':>9} {'Retention':>10} {'Cap':>8}")
    for sym in CURATED_SYMBOLS:
        post = summary[sym]["closed"]
        base = BASELINE_CLOSED_PRE_CAP[sym]
        ret = (post / base) if base > 0 else 0.0
        cap = symbol_overrides[sym]["max_participation_rate"]
        print(f"  {sym:<12} {post:>9d} {base:>9d} {ret:>9.1%} {cap:>8.4f}")
    print(f"  {'TOTAL':<12} {total_post_cap:>9d} {BASELINE_TOTAL:>9d} "
          f"{total_post_cap/BASELINE_TOTAL:>9.1%}")
    print(f"  Reduction: {BASELINE_TOTAL - total_post_cap} trades "
          f"({(1 - total_post_cap/BASELINE_TOTAL):.1%})")
