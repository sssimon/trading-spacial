from datetime import datetime, timezone


def test_optimize_new_tokens_claims_and_finalizes(monkeypatch):
    import optimize_new_tokens as ont

    monkeypatch.setattr(ont, "get_cached_data", lambda *a, **k: ["bar"])
    monkeypatch.setattr(ont, "get_historical_fear_greed", lambda: None)
    monkeypatch.setattr(ont, "get_historical_funding_rate", lambda: None)

    monkeypatch.setattr(ont, "simulate_strategy", lambda *a, **k: (["t"], ["e"]))
    monkeypatch.setattr(ont, "calculate_metrics", lambda *a, **k: {
        "total_trades": 3, "win_rate": 0.6, "net_pnl": 10, "profit_factor": 1.5,
        "max_drawdown_pct": -5, "sharpe_ratio": 1.1, "final_equity": 110,
    })

    claims, finals = [], []
    monkeypatch.setattr(ont, "claim_trial",
                        lambda **kw: (claims.append(kw), len(claims))[1])
    monkeypatch.setattr(ont, "finalize_trial",
                        lambda tid, **kw: finals.append((tid, kw)))

    # Shrink the grid to 2 combos for a fast deterministic test.
    monkeypatch.setattr(ont, "GRID", {
        "atr_sl_mult": [0.5, 1.0], "atr_tp_mult": [3.0], "atr_be_mult": [2.0],
    })

    ont.optimize_symbol(
        "NEWUSDT",
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        datetime(2024, 4, 1, tzinfo=timezone.utc),
    )

    assert len(claims) == 2
    assert all(c["source"] == "optimize_new_tokens" for c in claims)
    assert [kw["status"] for _, kw in finals] == ["ok", "ok"]
