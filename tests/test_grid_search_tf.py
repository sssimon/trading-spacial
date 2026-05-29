from datetime import datetime, timezone


def test_grid_search_claims_and_finalizes_each_trial(monkeypatch):
    import grid_search_tf as gst

    # Stub data loaders so no real data is needed.
    monkeypatch.setattr(gst, "get_cached_data", lambda *a, **k: ["bar"])
    monkeypatch.setattr(gst, "get_historical_fear_greed", lambda: None)
    monkeypatch.setattr(gst, "get_historical_funding_rate", lambda: None)

    # First combo succeeds, second produces no trades.
    seq = iter([(["t"], ["e"]), ([], [])])
    monkeypatch.setattr(gst, "simulate_strategy", lambda *a, **k: next(seq))
    monkeypatch.setattr(gst, "calculate_metrics", lambda *a, **k: {
        "total_trades": 3, "win_rate": 0.6, "net_pnl": 10, "profit_factor": 1.5,
        "max_drawdown_pct": -5, "sharpe_ratio": 1.1, "final_equity": 110,
        "trades_per_month": 2,
    })

    claims, finals = [], []
    monkeypatch.setattr(gst, "claim_trial",
                        lambda **kw: (claims.append(kw), len(claims))[1])
    monkeypatch.setattr(gst, "finalize_trial",
                        lambda tid, **kw: finals.append((tid, kw)))

    # tiny_grid widened on tf_ema_fast to [10, 11] so itertools.product yields
    # 2 combos. With tf_ema_slow=[20], both 10 and 11 are < 20 so neither is
    # skipped by the invalid-combo guard (fast >= slow) — both combos run.
    tiny_grid = {
        "tf_ema_fast": [10, 11], "tf_ema_slow": [20],
        "tf_adx_min": [20], "tf_atr_mult": [2.0], "tf_rsi_entry_long": [55],
    }
    gst.grid_search_symbol(
        "BTCUSDT", tiny_grid,
        datetime(2022, 1, 1, tzinfo=timezone.utc),
        datetime(2022, 4, 1, tzinfo=timezone.utc),
    )

    # Both combos claimed; first finalized ok, second finalized failed.
    assert len(claims) == 2
    assert all(c["source"] == "grid_search_tf" for c in claims)
    statuses = [kw["status"] for _, kw in finals]
    assert statuses == ["ok", "failed"]
