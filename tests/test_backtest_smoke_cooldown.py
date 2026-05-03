"""End-to-end smoke for per-symbol cooldown across the curated basket."""
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


# Baseline post-cap, pre-cooldown trade counts (TL active + cap active, costs
# OFF, train segment 2023-10-01 → 2025-03-31). Captured against commit
# ec0a586 (post-cap-merge, pre-cooldown). Hard-pinned: a future increase in
# any per-symbol count means the cooldown stopped firing for that symbol —
# surface as regression.
BASELINE_CLOSED_POST_CAP_PRE_COOLDOWN: dict[str, int] = {
    "BTCUSDT": 206, "ETHUSDT": 169, "ADAUSDT":   79, "AVAXUSDT": 145,
    "DOGEUSDT": 170, "UNIUSDT":   40, "XLMUSDT":  23, "PENDLEUSDT": 10,
    "JUPUSDT":   55, "RUNEUSDT":  67,
}
BASELINE_TOTAL = sum(BASELINE_CLOSED_POST_CAP_PRE_COOLDOWN.values())  # = 964

# Symbols whose cooldown changed from the legacy global 6h:
# BTC/ETH 6→14, AVAX 6→8. These MUST show some retention drop (cooldown
# binding at least once in 18 months); otherwise the per-symbol resolution
# silently fell back to the global default.
COOLDOWN_INCREASED_SYMBOLS = {"BTCUSDT", "ETHUSDT", "AVAXUSDT"}


@pytest.mark.skipif(
    not os.path.exists(OHLCV_DB),
    reason="requires cached market data (data/ohlcv.db)",
)
def test_smoke_cooldown_across_curated_symbols(tmp_path, monkeypatch):
    """Per-symbol cooldown smoke: counts ≤ baseline, BTC/ETH/AVAX retention < 100%."""
    from backtest import simulate_strategy, get_cached_data
    import btc_api

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    btc_api.init_db()

    with open(CONFIG_DEFAULTS_PATH) as f:
        cfg = json.load(f)

    symbol_overrides = cfg.get("symbol_overrides", {})
    for sym in CURATED_SYMBOLS:
        assert "cooldown_hours" in symbol_overrides.get(sym, {}), (
            f"{sym} must have cooldown_hours in config.defaults.json"
        )

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

        for t in trades:
            assert t["pnl_usd"] is not None and not math.isnan(t["pnl_usd"]), (
                f"NaN pnl_usd for {symbol}: {t}"
            )
            assert t["pnl_pct"] is not None and not math.isnan(t["pnl_pct"]), (
                f"NaN pnl_pct for {symbol}: {t}"
            )

        closed = [t for t in trades if t["exit_reason"] != "OPEN"]
        summary[symbol] = {"closed": len(closed)}

    # ── Per-symbol monotonic regression: cooldown can only reduce ───────────
    for symbol in CURATED_SYMBOLS:
        post_cooldown = summary[symbol]["closed"]
        baseline = BASELINE_CLOSED_POST_CAP_PRE_COOLDOWN[symbol]
        assert 0 <= post_cooldown <= baseline, (
            f"{symbol}: post_cooldown_count={post_cooldown} must be in [0, {baseline}] "
            f"(cooldown can only equal or reduce, never add)"
        )

    # ── Aggregate monotonic ─────────────────────────────────────────────────
    total_post_cooldown = sum(s["closed"] for s in summary.values())
    assert total_post_cooldown <= BASELINE_TOTAL, (
        f"aggregate post_cooldown={total_post_cooldown} must be ≤ baseline={BASELINE_TOTAL}; "
        f"larger means cooldown logic broke (or per-symbol resolution leaked into "
        f"a faster path than the legacy global)"
    )

    # ── Cooldown-increased symbols MUST show binding ────────────────────────
    # BTC/ETH 6h→14h, AVAX 6h→8h. Over 18 months, the new cooldown MUST bind
    # at least once for each — else per-symbol resolution silently no-op'd.
    for symbol in COOLDOWN_INCREASED_SYMBOLS:
        post = summary[symbol]["closed"]
        baseline = BASELINE_CLOSED_POST_CAP_PRE_COOLDOWN[symbol]
        assert post < baseline, (
            f"{symbol}: cooldown increased from 6h to "
            f"{symbol_overrides[symbol]['cooldown_hours']}h but trade count did "
            f"not drop ({post} == {baseline}). Per-symbol resolution may be "
            f"silently falling back to the global default. Investigate."
        )
        # Soft floor: cooldown can bind but not annihilate. Catches an
        # "always-blocks" regression (e.g., TZ bug → hours_since negative →
        # activo always True) that would dump trade count to ~0 silently.
        assert post >= baseline * 0.5, (
            f"{symbol}: post-cooldown count {post} below {baseline * 0.5:.0f} "
            f"(50% baseline floor). Cooldown is binding too aggressively — "
            f"investigate for an always-blocks regression (TZ sign flip, "
            f"future-dated last_exit_ts)."
        )

    # ── Print summary so post-merge readers can compare ─────────────────────
    print("\n=== SMOKE COOLDOWN PER-SYMBOL SUMMARY ===")
    print(f"{'Symbol':<14} {'Post-CD':>9} {'Baseline':>9} {'Retention':>10} {'CD':>4}")
    for sym in CURATED_SYMBOLS:
        post = summary[sym]["closed"]
        base = BASELINE_CLOSED_POST_CAP_PRE_COOLDOWN[sym]
        ret = (post / base) if base > 0 else 0.0
        cd_h = symbol_overrides[sym]["cooldown_hours"]
        print(f"  {sym:<12} {post:>9d} {base:>9d} {ret:>9.1%} {cd_h:>4}h")
    print(f"  {'TOTAL':<12} {total_post_cooldown:>9d} {BASELINE_TOTAL:>9d} "
          f"{total_post_cooldown/BASELINE_TOTAL:>9.1%}")
    print(f"  Reduction: {BASELINE_TOTAL - total_post_cooldown} trades "
          f"({(1 - total_post_cooldown/BASELINE_TOTAL):.1%})")
