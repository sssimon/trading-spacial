"""Tests for strategy.kill_switch_v2 — portfolio circuit breaker (#187 B2)."""
import pytest


def test_interpolate_threshold_at_slider_0():
    from strategy.kill_switch_v2 import interpolate_threshold
    # slider=0 → t_min
    assert interpolate_threshold(0, t_min=-0.08, t_max=-0.03) == pytest.approx(-0.08)


def test_interpolate_threshold_at_slider_100():
    from strategy.kill_switch_v2 import interpolate_threshold
    # slider=100 → t_max (more strict)
    assert interpolate_threshold(100, t_min=-0.08, t_max=-0.03) == pytest.approx(-0.03)


def test_interpolate_threshold_at_slider_50():
    from strategy.kill_switch_v2 import interpolate_threshold
    # slider=50 → midpoint
    assert interpolate_threshold(50, t_min=-0.08, t_max=-0.03) == pytest.approx(-0.055)


def test_interpolate_threshold_linear():
    from strategy.kill_switch_v2 import interpolate_threshold
    # slider=25 → 25% of the way
    assert interpolate_threshold(25, t_min=0.0, t_max=100.0) == pytest.approx(25.0)


def test_get_thresholds_from_config_default_aggressiveness():
    from strategy.kill_switch_v2 import get_portfolio_thresholds
    cfg = {
        "kill_switch": {
            "v2": {
                "aggressiveness": 50,
                "thresholds": {
                    "portfolio_dd_reduced": {"min": -0.08, "max": -0.03},
                    "portfolio_dd_frozen": {"min": -0.15, "max": -0.06},
                },
            },
        },
    }
    thresholds = get_portfolio_thresholds(cfg)
    assert thresholds["reduced_dd"] == pytest.approx(-0.055)
    assert thresholds["frozen_dd"] == pytest.approx(-0.105)


def test_get_thresholds_from_config_aggressiveness_0():
    from strategy.kill_switch_v2 import get_portfolio_thresholds
    cfg = {
        "kill_switch": {
            "v2": {
                "aggressiveness": 0,
                "thresholds": {
                    "portfolio_dd_reduced": {"min": -0.08, "max": -0.03},
                    "portfolio_dd_frozen": {"min": -0.15, "max": -0.06},
                },
            },
        },
    }
    thresholds = get_portfolio_thresholds(cfg)
    assert thresholds["reduced_dd"] == pytest.approx(-0.08)
    assert thresholds["frozen_dd"] == pytest.approx(-0.15)


def test_get_thresholds_missing_config_returns_defaults():
    from strategy.kill_switch_v2 import get_portfolio_thresholds
    # No v2 config present — should return sensible defaults (slider=50)
    thresholds = get_portfolio_thresholds({})
    # With defaults t_min=-0.08/-0.15 t_max=-0.03/-0.06 and slider=50
    assert thresholds["reduced_dd"] == pytest.approx(-0.055)
    assert thresholds["frozen_dd"] == pytest.approx(-0.105)


def test_compute_portfolio_equity_curve_empty():
    from strategy.kill_switch_v2 import compute_portfolio_equity_curve
    curve = compute_portfolio_equity_curve(
        closed_trades=[],
        open_positions=[],
        capital_base=100_000.0,
        now_price_by_symbol={},
    )
    # Empty history — single snapshot at capital_base
    assert len(curve) == 1
    assert curve[0]["equity"] == pytest.approx(100_000.0)


def test_compute_portfolio_equity_curve_closed_trades_only():
    from strategy.kill_switch_v2 import compute_portfolio_equity_curve
    # 2 closed trades: +200, -50 → cumulative equity steps
    closed_trades = [
        {"symbol": "BTCUSDT", "exit_ts": "2026-04-20T12:00:00+00:00", "pnl_usd": 200.0},
        {"symbol": "ETHUSDT", "exit_ts": "2026-04-21T14:00:00+00:00", "pnl_usd": -50.0},
    ]
    curve = compute_portfolio_equity_curve(
        closed_trades=closed_trades,
        open_positions=[],
        capital_base=100_000.0,
        now_price_by_symbol={},
    )
    # 3 points: start, after trade 1, after trade 2
    assert len(curve) == 3
    assert curve[0]["equity"] == pytest.approx(100_000.0)
    assert curve[1]["equity"] == pytest.approx(100_200.0)
    assert curve[2]["equity"] == pytest.approx(100_150.0)


def test_compute_portfolio_equity_curve_open_positions_mtm():
    """Open positions add an MTM point at the end using now_price_by_symbol."""
    from strategy.kill_switch_v2 import compute_portfolio_equity_curve
    # 1 closed trade (+100), 1 open position entered at $50k, now $51k with 0.01 qty
    closed_trades = [
        {"symbol": "BTCUSDT", "exit_ts": "2026-04-20T12:00:00+00:00", "pnl_usd": 100.0},
    ]
    open_positions = [
        {
            "symbol": "BTCUSDT",
            "entry_price": 50_000.0,
            "qty": 0.01,
            "direction": "LONG",
        },
    ]
    now_prices = {"BTCUSDT": 51_000.0}
    curve = compute_portfolio_equity_curve(
        closed_trades=closed_trades,
        open_positions=open_positions,
        capital_base=100_000.0,
        now_price_by_symbol=now_prices,
    )
    # Start 100k → after trade +100 → +MTM of (51k-50k)*0.01 = 10
    # 3 points: [100_000, 100_100, 100_110]
    assert len(curve) == 3
    assert curve[-1]["equity"] == pytest.approx(100_110.0)


def test_compute_portfolio_equity_curve_short_mtm():
    """SHORT position MTM is (entry - current) * qty."""
    from strategy.kill_switch_v2 import compute_portfolio_equity_curve
    open_positions = [
        {
            "symbol": "ETHUSDT",
            "entry_price": 3_000.0,
            "qty": 1.0,
            "direction": "SHORT",
        },
    ]
    now_prices = {"ETHUSDT": 2_950.0}
    curve = compute_portfolio_equity_curve(
        closed_trades=[],
        open_positions=open_positions,
        capital_base=10_000.0,
        now_price_by_symbol=now_prices,
    )
    # SHORT won (+50 per coin × 1 coin = +50)
    # 2 points: start, end
    assert curve[-1]["equity"] == pytest.approx(10_050.0)


def test_compute_portfolio_equity_curve_missing_price_skips_mtm():
    """If now_price_by_symbol is missing the open position's symbol, skip MTM for it."""
    from strategy.kill_switch_v2 import compute_portfolio_equity_curve
    open_positions = [
        {
            "symbol": "UNKNOWNUSDT",
            "entry_price": 1.0,
            "qty": 100.0,
            "direction": "LONG",
        },
    ]
    now_prices = {}  # empty
    curve = compute_portfolio_equity_curve(
        closed_trades=[],
        open_positions=open_positions,
        capital_base=100_000.0,
        now_price_by_symbol=now_prices,
    )
    # Only the start point remains (no MTM applied)
    assert len(curve) == 1
    assert curve[0]["equity"] == pytest.approx(100_000.0)


def test_compute_portfolio_dd_from_flat_curve():
    from strategy.kill_switch_v2 import compute_portfolio_dd
    curve = [
        {"ts": "a", "equity": 100_000.0},
        {"ts": "b", "equity": 100_000.0},
    ]
    assert compute_portfolio_dd(curve) == pytest.approx(0.0)


def test_compute_portfolio_dd_only_gains():
    from strategy.kill_switch_v2 import compute_portfolio_dd
    curve = [
        {"ts": "a", "equity": 100_000.0},
        {"ts": "b", "equity": 105_000.0},
        {"ts": "c", "equity": 110_000.0},
    ]
    assert compute_portfolio_dd(curve) == pytest.approx(0.0)


def test_compute_portfolio_dd_drawdown_from_peak():
    from strategy.kill_switch_v2 import compute_portfolio_dd
    # Peak 110k, valley 99k → DD = (99-110)/110 = -0.10
    curve = [
        {"ts": "a", "equity": 100_000.0},
        {"ts": "b", "equity": 110_000.0},
        {"ts": "c", "equity": 105_000.0},
        {"ts": "d", "equity": 99_000.0},
    ]
    assert compute_portfolio_dd(curve) == pytest.approx(-0.10)


def test_compute_portfolio_dd_current_at_peak_zero_dd():
    from strategy.kill_switch_v2 import compute_portfolio_dd
    # Went down then back up to peak
    curve = [
        {"ts": "a", "equity": 100_000.0},
        {"ts": "b", "equity": 110_000.0},
        {"ts": "c", "equity": 95_000.0},
        {"ts": "d", "equity": 110_000.0},
    ]
    # DD is measured at LAST point vs running peak. Last == peak → 0.
    assert compute_portfolio_dd(curve) == pytest.approx(0.0)


def test_compute_portfolio_dd_empty_curve():
    from strategy.kill_switch_v2 import compute_portfolio_dd
    assert compute_portfolio_dd([]) == 0.0


def test_evaluate_portfolio_tier_normal():
    from strategy.kill_switch_v2 import evaluate_portfolio_tier
    cfg = {"kill_switch": {"v2": {
        "aggressiveness": 50,
        "thresholds": {
            "portfolio_dd_reduced": {"min": -0.08, "max": -0.03},
            "portfolio_dd_frozen": {"min": -0.15, "max": -0.06},
        },
    }}}
    # DD -0.01 → well above -0.055 reduced threshold → NORMAL
    result = evaluate_portfolio_tier(
        portfolio_dd=-0.01,
        concurrent_failures=0,
        cfg=cfg,
    )
    assert result["tier"] == "NORMAL"
    assert result["dd"] == pytest.approx(-0.01)


def test_evaluate_portfolio_tier_warned_by_concurrent_failures():
    from strategy.kill_switch_v2 import evaluate_portfolio_tier
    cfg = {"kill_switch": {"v2": {
        "aggressiveness": 50,
        "concurrent_alert_threshold": 3,
        "thresholds": {
            "portfolio_dd_reduced": {"min": -0.08, "max": -0.03},
            "portfolio_dd_frozen": {"min": -0.15, "max": -0.06},
        },
    }}}
    # DD safe, but 3 concurrent failures → WARNED
    result = evaluate_portfolio_tier(
        portfolio_dd=-0.01,
        concurrent_failures=3,
        cfg=cfg,
    )
    assert result["tier"] == "WARNED"


def test_evaluate_portfolio_tier_reduced_by_dd():
    from strategy.kill_switch_v2 import evaluate_portfolio_tier
    cfg = {"kill_switch": {"v2": {
        "aggressiveness": 50,
        "thresholds": {
            "portfolio_dd_reduced": {"min": -0.08, "max": -0.03},
            "portfolio_dd_frozen": {"min": -0.15, "max": -0.06},
        },
    }}}
    # DD -0.07 crosses reduced threshold -0.055 → REDUCED
    result = evaluate_portfolio_tier(
        portfolio_dd=-0.07,
        concurrent_failures=0,
        cfg=cfg,
    )
    assert result["tier"] == "REDUCED"


def test_evaluate_portfolio_tier_frozen_by_dd():
    from strategy.kill_switch_v2 import evaluate_portfolio_tier
    cfg = {"kill_switch": {"v2": {
        "aggressiveness": 50,
        "thresholds": {
            "portfolio_dd_reduced": {"min": -0.08, "max": -0.03},
            "portfolio_dd_frozen": {"min": -0.15, "max": -0.06},
        },
    }}}
    # DD -0.12 crosses frozen threshold -0.105 → FROZEN
    result = evaluate_portfolio_tier(
        portfolio_dd=-0.12,
        concurrent_failures=0,
        cfg=cfg,
    )
    assert result["tier"] == "FROZEN"


def test_evaluate_portfolio_tier_frozen_takes_priority_over_concurrent():
    from strategy.kill_switch_v2 import evaluate_portfolio_tier
    cfg = {"kill_switch": {"v2": {
        "aggressiveness": 50,
        "concurrent_alert_threshold": 3,
        "thresholds": {
            "portfolio_dd_reduced": {"min": -0.08, "max": -0.03},
            "portfolio_dd_frozen": {"min": -0.15, "max": -0.06},
        },
    }}}
    result = evaluate_portfolio_tier(
        portfolio_dd=-0.15,
        concurrent_failures=5,  # also WARNED eligible
        cfg=cfg,
    )
    # FROZEN is the most severe; takes priority
    assert result["tier"] == "FROZEN"


# ── Shadow glue: price cache, default capital, fail-open ────────────────────


@pytest.fixture
def _clean_shadow_cache():
    from strategy import kill_switch_v2_shadow
    kill_switch_v2_shadow._PRICE_CACHE.clear()
    yield
    kill_switch_v2_shadow._PRICE_CACHE.clear()


def test_update_price_accumulates_across_symbols(_clean_shadow_cache):
    from strategy.kill_switch_v2_shadow import update_price, _snapshot_prices
    update_price("BTCUSDT", 50_000.0)
    update_price("ETHUSDT", 3_000.0)
    update_price("ADAUSDT", 0.5)
    snap = _snapshot_prices()
    assert snap == {"BTCUSDT": 50_000.0, "ETHUSDT": 3_000.0, "ADAUSDT": 0.5}


def test_update_price_overwrites_stale(_clean_shadow_cache):
    from strategy.kill_switch_v2_shadow import update_price, _snapshot_prices
    update_price("BTCUSDT", 50_000.0)
    update_price("BTCUSDT", 51_000.0)
    assert _snapshot_prices()["BTCUSDT"] == 51_000.0


def test_default_capital_matches_scanner_hardcoded_1000():
    """cfg without capital_usd must fall back to $1000 (matches btc_scanner.scan)."""
    from strategy import kill_switch_v2_shadow
    assert kill_switch_v2_shadow._DEFAULT_CAPITAL_USD == 1000.0


def test_emit_shadow_uses_cache_for_multi_symbol_mtm(tmp_path, monkeypatch, _clean_shadow_cache):
    """emit_shadow_decision MTMs every open position with a cached price,
    not just the currently-scanned symbol."""
    import btc_api, observability
    from strategy.kill_switch_v2_shadow import emit_shadow_decision, update_price

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    # Seed 2 open positions in 2 different symbols, both priced
    conn = btc_api.get_db()
    try:
        conn.execute(
            "INSERT INTO positions(symbol, direction, entry_price, qty, status, entry_ts) "
            "VALUES('BTCUSDT', 'LONG', 50000, 0.01, 'open', '2026-04-20T10:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO positions(symbol, direction, entry_price, qty, status, entry_ts) "
            "VALUES('ETHUSDT', 'LONG', 3000, 1.0, 'open', '2026-04-20T10:00:00+00:00')"
        )
        conn.commit()
    finally:
        conn.close()

    # Simulate two prior scans that populated the cache for both symbols
    update_price("BTCUSDT", 51_000.0)   # +$10 on 0.01 qty
    update_price("ETHUSDT", 3_050.0)    # +$50 on 1 qty

    # Current scan is for RUNEUSDT (no open position, irrelevant) — but ETH
    # and BTC MTMs should both land
    emit_shadow_decision(symbol="RUNEUSDT", cfg={})

    rows = observability.query_decisions(symbol="RUNEUSDT", engine="v2_shadow")
    assert len(rows) == 1
    import json
    reasons = json.loads(rows[0]["reasons_json"])
    # Capital $1000 + MTM +$60 → peak=current → DD = 0
    # (No closed trades; equity only grows, so DD stays 0)
    assert reasons["portfolio_dd"] == pytest.approx(0.0)


def test_emit_shadow_fail_open_swallows_exceptions(tmp_path, monkeypatch, caplog, _clean_shadow_cache):
    """If any internal call raises, emit_shadow_decision must not escape — v1 must keep running."""
    import btc_api, observability
    from strategy.kill_switch_v2_shadow import emit_shadow_decision
    import strategy.kill_switch_v2_shadow as shadow_mod

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    def _boom():
        raise RuntimeError("simulated DB corruption")

    monkeypatch.setattr(shadow_mod, "_load_closed_trades", _boom)

    import logging
    with caplog.at_level(logging.WARNING, logger="kill_switch_v2_shadow"):
        emit_shadow_decision(symbol="BTCUSDT", cfg={})

    # No exception escaped, warning logged with symbol context
    assert any(
        "kill_switch_v2_shadow.emit_shadow_decision failed for BTCUSDT"
        in rec.getMessage()
        for rec in caplog.records
    )
    # And no v2_shadow row was persisted
    rows = observability.query_decisions(symbol="BTCUSDT", engine="v2_shadow")
    assert len(rows) == 0


def test_emit_shadow_default_capital_1000_applied(tmp_path, monkeypatch, _clean_shadow_cache):
    """cfg without capital_usd → $1000 base, not $100,000."""
    import btc_api, observability
    from strategy.kill_switch_v2_shadow import emit_shadow_decision

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    # Seed one closed trade: -$50 PnL
    # With capital=$1000 → DD = -50/1000 = -0.05 (REDUCED band at slider=50)
    # With capital=$100k → DD = -50/100_000 = -0.0005 (NORMAL — the bug)
    conn = btc_api.get_db()
    try:
        conn.execute(
            "INSERT INTO positions(symbol, direction, entry_price, qty, status, "
            "entry_ts, exit_ts, pnl_usd) VALUES('BTCUSDT', 'LONG', 50000, 0.01, "
            "'closed', '2026-04-20T10:00:00+00:00', '2026-04-20T12:00:00+00:00', -50.0)"
        )
        conn.commit()
    finally:
        conn.close()

    emit_shadow_decision(symbol="BTCUSDT", cfg={})

    rows = observability.query_decisions(symbol="BTCUSDT", engine="v2_shadow")
    assert len(rows) == 1
    import json
    reasons = json.loads(rows[0]["reasons_json"])
    assert reasons["portfolio_dd"] == pytest.approx(-0.05)
    # At slider=50, reduced=-0.055 → -0.05 is still NORMAL, but the number
    # is at the right order of magnitude (bug would produce -0.0005).


def test_emit_shadow_warning_includes_traceback(tmp_path, monkeypatch, caplog, _clean_shadow_cache):
    """Fail-open warning must include exc_info=True so the traceback is loggable."""
    import btc_api
    from strategy.kill_switch_v2_shadow import emit_shadow_decision
    import strategy.kill_switch_v2_shadow as shadow_mod

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    def _boom():
        raise RuntimeError("deep error")

    monkeypatch.setattr(shadow_mod, "_load_open_positions", _boom)

    import logging
    with caplog.at_level(logging.WARNING, logger="kill_switch_v2_shadow"):
        emit_shadow_decision(symbol="BTCUSDT", cfg={})

    # At least one record has exc_info (traceback) attached
    assert any(rec.exc_info is not None for rec in caplog.records)
