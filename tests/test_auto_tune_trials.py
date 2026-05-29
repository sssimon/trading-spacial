"""Trial-registry wiring for ``auto_tune.run_backtest_with_params`` (#278 Part 1).

``run_backtest_with_params`` is the single chokepoint through which auto_tune's
baseline + grid + validate backtests all flow, so wiring claim/finalize there
records every exploratory trial.

NOTE on monkeypatching: ``run_backtest_with_params`` imports its data + simulator
helpers FUNCTION-LOCALLY (``from backtest import ...`` inside the body), so the
helpers must be patched on the ``backtest`` module, not on ``auto_tune``. The
registry hooks (``claim_trial`` / ``finalize_trial``) ARE module-level imports in
``auto_tune``, so those are patched on ``auto_tune``.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest


def _nonempty_frame():
    idx = pd.DatetimeIndex(
        [datetime(2022, 1, 1), datetime(2022, 1, 2)], name="ts"
    )
    return pd.DataFrame(
        {
            "open": [100.0, 100.0],
            "high": [101.0, 101.0],
            "low": [99.0, 99.0],
            "close": [100.5, 100.5],
            "volume": [1.0, 1.0],
        },
        index=idx,
    )


def _patch_loaders(monkeypatch):
    import backtest as bt

    monkeypatch.setattr(bt, "get_cached_data", lambda *a, **k: _nonempty_frame())
    monkeypatch.setattr(bt, "get_historical_fear_greed", lambda: pd.DataFrame())
    monkeypatch.setattr(bt, "get_historical_funding_rate", lambda: pd.DataFrame())


def test_run_backtest_with_params_finalizes_ok(monkeypatch):
    import auto_tune as at
    import backtest as bt

    _patch_loaders(monkeypatch)
    monkeypatch.setattr(bt, "simulate_strategy", lambda *a, **k: (["t"], ["e"]))
    monkeypatch.setattr(bt, "calculate_metrics", lambda *a, **k: {
        "total_trades": 5, "net_pnl": 50, "profit_factor": 1.3, "sharpe_ratio": 0.9,
    })

    claims, finals = [], []
    monkeypatch.setattr(at, "claim_trial",
                        lambda **kw: (claims.append(kw), len(claims))[1])
    monkeypatch.setattr(at, "finalize_trial",
                        lambda tid, **kw: finals.append((tid, kw)))

    trades, metrics = at.run_backtest_with_params(
        "BTCUSDT",
        {"atr_sl_mult": 1.0, "atr_tp_mult": 3.0, "atr_be_mult": 2.0},
        datetime(2022, 1, 1, tzinfo=timezone.utc),
        datetime(2022, 4, 1, tzinfo=timezone.utc),
        trial_source="auto_tune",
    )

    # The wrap returns the (trades, metrics) tuple unchanged.
    assert trades == ["t"]
    assert metrics["net_pnl"] == 50

    assert len(claims) == 1
    assert claims[0]["source"] == "auto_tune"
    assert claims[0]["symbol"] == "BTCUSDT"
    assert claims[0]["combo"] == {"atr_sl_mult": 1.0, "atr_tp_mult": 3.0, "atr_be_mult": 2.0}
    assert claims[0]["window_label"] == "2022-01-01..2022-04-01"
    assert len(finals) == 1
    assert finals[0][1]["status"] == "ok"


def test_run_backtest_with_params_finalizes_failed_on_exception(monkeypatch):
    import auto_tune as at
    import backtest as bt

    _patch_loaders(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("simulator exploded")

    monkeypatch.setattr(bt, "simulate_strategy", boom)

    claims, finals = [], []
    monkeypatch.setattr(at, "claim_trial",
                        lambda **kw: (claims.append(kw), len(claims))[1])
    monkeypatch.setattr(at, "finalize_trial",
                        lambda tid, **kw: finals.append((tid, kw)))

    with pytest.raises(RuntimeError, match="exploded"):
        at.run_backtest_with_params(
            "BTCUSDT",
            {"atr_sl_mult": 1.0, "atr_tp_mult": 3.0, "atr_be_mult": 2.0},
            datetime(2022, 1, 1, tzinfo=timezone.utc),
            datetime(2022, 4, 1, tzinfo=timezone.utc),
            trial_source="auto_tune",
        )

    assert len(claims) == 1
    assert finals[0][1]["status"] == "failed"
    assert "exploded" in finals[0][1]["error"]


def test_run_backtest_with_params_no_trial_source_does_not_register(monkeypatch):
    """Default path (no trial_source) is the walk_forward/tools path: it must
    register NOTHING. Only auto_tune's own sweep opts in via
    trial_source="auto_tune". Counting evaluation/research runs as selection
    trials would corrupt the N denominator (#278 Part 2)."""
    import auto_tune as at
    import backtest as bt

    _patch_loaders(monkeypatch)
    monkeypatch.setattr(bt, "simulate_strategy", lambda *a, **k: (["t"], ["e"]))
    monkeypatch.setattr(bt, "calculate_metrics", lambda *a, **k: {
        "total_trades": 5, "net_pnl": 50, "profit_factor": 1.3, "sharpe_ratio": 0.9,
    })

    claims, finals = [], []
    monkeypatch.setattr(at, "claim_trial",
                        lambda **kw: (claims.append(kw), len(claims))[1])
    monkeypatch.setattr(at, "finalize_trial",
                        lambda tid, **kw: finals.append((tid, kw)))

    trades, metrics = at.run_backtest_with_params(
        "BTCUSDT",
        {"atr_sl_mult": 1.0, "atr_tp_mult": 3.0, "atr_be_mult": 2.0},
        datetime(2022, 1, 1, tzinfo=timezone.utc),
        datetime(2022, 4, 1, tzinfo=timezone.utc),
    )

    # Result is unchanged...
    assert trades == ["t"]
    assert metrics["net_pnl"] == 50
    # ...but no trial was claimed or finalized (this is the eval/research path).
    assert claims == []
    assert finals == []
