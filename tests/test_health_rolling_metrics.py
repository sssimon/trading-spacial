"""Rolling metrics computed from positions table (closed positions only)."""
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import btc_api
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    yield db_path


def _insert_closed_position(conn, symbol, pnl_usd, exit_ts):
    """Insert a closed position directly. exit_ts is an ISO datetime string."""
    conn.execute(
        """INSERT INTO positions
           (symbol, direction, status, entry_price, entry_ts,
            exit_price, exit_ts, exit_reason, pnl_usd, pnl_pct)
           VALUES (?, 'LONG', 'closed', 100.0, ?, 101.0, ?, 'TP', ?, ?)""",
        (symbol, exit_ts, exit_ts, pnl_usd, pnl_usd / 100.0),
    )
    conn.commit()


NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


def test_empty_db_returns_zero_trades(tmp_db):
    from health import compute_rolling_metrics
    import btc_api
    conn = btc_api.get_db()
    try:
        metrics = compute_rolling_metrics("BTCUSDT", conn, now=NOW)
    finally:
        conn.close()
    assert metrics["trades_count_total"] == 0
    assert metrics["win_rate_20_trades"] == 0.0
    assert metrics["pnl_30d"] == 0.0
    assert metrics["months_negative_consecutive"] == 0


def test_open_positions_are_excluded(tmp_db):
    """Only closed positions count."""
    from health import compute_rolling_metrics
    import btc_api
    conn = btc_api.get_db()
    try:
        conn.execute(
            """INSERT INTO positions
               (symbol, direction, status, entry_price, entry_ts, pnl_usd, pnl_pct)
               VALUES ('BTCUSDT', 'LONG', 'open', 100.0, ?, NULL, NULL)""",
            (NOW.isoformat(),),
        )
        conn.commit()
        metrics = compute_rolling_metrics("BTCUSDT", conn, now=NOW)
    finally:
        conn.close()
    assert metrics["trades_count_total"] == 0


def test_win_rate_last_20_trades(tmp_db):
    """win_rate_20_trades = (winners in last 20 closed) / 20."""
    from health import compute_rolling_metrics
    import btc_api
    conn = btc_api.get_db()
    try:
        # 25 closed positions: first 5 wins, then 15 losses, then 5 wins → last 20 = 15 L + 5 W
        for i in range(25):
            pnl = 100.0 if (i < 5 or i >= 20) else -50.0
            ts = (NOW - timedelta(days=25 - i)).isoformat()
            _insert_closed_position(conn, "BTCUSDT", pnl, ts)
        metrics = compute_rolling_metrics("BTCUSDT", conn, now=NOW)
    finally:
        conn.close()
    assert metrics["trades_count_total"] == 25
    # Last 20 sorted desc by exit_ts: the 20 most recent → i=5..24 → 15 losses (i=5..19) + 5 wins (i=20..24) = 5/20 = 0.25
    assert metrics["win_rate_20_trades"] == 0.25


def test_win_rate_falls_back_to_available_when_under_20(tmp_db):
    """If only 10 trades exist, win_rate uses those 10 (caller handles cold-start via trades_count_total)."""
    from health import compute_rolling_metrics
    import btc_api
    conn = btc_api.get_db()
    try:
        for i in range(10):
            pnl = 100.0 if i < 3 else -50.0
            ts = (NOW - timedelta(days=10 - i)).isoformat()
            _insert_closed_position(conn, "BTCUSDT", pnl, ts)
        metrics = compute_rolling_metrics("BTCUSDT", conn, now=NOW)
    finally:
        conn.close()
    assert metrics["trades_count_total"] == 10
    assert metrics["win_rate_20_trades"] == 0.3  # 3/10


def test_pnl_30d_window(tmp_db):
    """Only positions with exit_ts >= now - 30d contribute to pnl_30d."""
    from health import compute_rolling_metrics
    import btc_api
    conn = btc_api.get_db()
    try:
        _insert_closed_position(conn, "BTC", 999.0, (NOW - timedelta(days=40)).isoformat())
        _insert_closed_position(conn, "BTC", 100.0, (NOW - timedelta(days=10)).isoformat())
        _insert_closed_position(conn, "BTC", -50.0, (NOW - timedelta(days=5)).isoformat())
        _insert_closed_position(conn, "BTC", 30.0, (NOW - timedelta(days=1)).isoformat())
        metrics = compute_rolling_metrics("BTC", conn, now=NOW)
    finally:
        conn.close()
    assert metrics["pnl_30d"] == 80.0  # 100 - 50 + 30


def test_months_negative_consecutive_3_months(tmp_db):
    """3 full calendar months all with sum(pnl)<0 → months_negative_consecutive=3."""
    from health import compute_rolling_metrics
    import btc_api
    conn = btc_api.get_db()
    try:
        _insert_closed_position(conn, "BTC", -100.0, "2026-05-10T12:00:00+00:00")
        _insert_closed_position(conn, "BTC", -50.0,  "2026-04-15T12:00:00+00:00")
        _insert_closed_position(conn, "BTC", -75.0,  "2026-03-20T12:00:00+00:00")
        _insert_closed_position(conn, "BTC", +200.0, "2026-02-05T12:00:00+00:00")
        metrics = compute_rolling_metrics("BTC", conn, now=NOW)
    finally:
        conn.close()
    assert metrics["months_negative_consecutive"] == 3


def test_months_negative_consecutive_broken_by_positive(tmp_db):
    """A positive month in the trailing window caps the streak."""
    from health import compute_rolling_metrics
    import btc_api
    conn = btc_api.get_db()
    try:
        _insert_closed_position(conn, "BTC", +500.0, "2026-05-10T12:00:00+00:00")
        _insert_closed_position(conn, "BTC", -50.0,  "2026-04-15T12:00:00+00:00")
        _insert_closed_position(conn, "BTC", -75.0,  "2026-03-20T12:00:00+00:00")
        metrics = compute_rolling_metrics("BTC", conn, now=NOW)
    finally:
        conn.close()
    assert metrics["months_negative_consecutive"] == 0


def test_months_negative_consecutive_partial_streak(tmp_db):
    """Most recent 2 months negative, 3rd back positive → streak=2."""
    from health import compute_rolling_metrics
    import btc_api
    conn = btc_api.get_db()
    try:
        _insert_closed_position(conn, "BTC", -100.0, "2026-05-10T12:00:00+00:00")
        _insert_closed_position(conn, "BTC", -50.0,  "2026-04-15T12:00:00+00:00")
        _insert_closed_position(conn, "BTC", +200.0, "2026-03-20T12:00:00+00:00")
        metrics = compute_rolling_metrics("BTC", conn, now=NOW)
    finally:
        conn.close()
    assert metrics["months_negative_consecutive"] == 2


def test_current_month_in_progress_is_excluded_from_streak(tmp_db):
    """The month containing NOW does NOT count toward consecutive_negative
    (it's partial). Only FULL prior calendar months do."""
    from health import compute_rolling_metrics
    import btc_api
    conn = btc_api.get_db()
    try:
        _insert_closed_position(conn, "BTC", -999.0, "2026-06-10T12:00:00+00:00")
        _insert_closed_position(conn, "BTC", -100.0, "2026-05-10T12:00:00+00:00")
        _insert_closed_position(conn, "BTC", -50.0,  "2026-04-15T12:00:00+00:00")
        _insert_closed_position(conn, "BTC", -75.0,  "2026-03-20T12:00:00+00:00")
        metrics = compute_rolling_metrics("BTC", conn, now=NOW)
    finally:
        conn.close()
    assert metrics["months_negative_consecutive"] == 3  # not 4


# ── B5: win_rate_10_trades ──────────────────────────────────────────────────


def test_win_rate_10_trades_empty():
    """No trades → win_rate_10_trades is None (mirrors win_rate_20_trades semantics)."""
    from datetime import datetime, timezone
    from health import compute_rolling_metrics_from_trades
    metrics = compute_rolling_metrics_from_trades([], now=datetime(2026, 6, 15, tzinfo=timezone.utc))
    assert metrics["win_rate_10_trades"] is None


def test_win_rate_10_trades_uses_last_ten():
    """win_rate_10_trades = winners in last 10 trades / 10."""
    from datetime import datetime, timezone
    from health import compute_rolling_metrics_from_trades
    # 12 trades total: first 2 wins, last 10 = 3 wins / 7 losses → 0.3
    trades = []
    for i in range(2):
        trades.append({"exit_ts": f"2026-04-{1+i:02d}T12:00:00+00:00", "pnl_usd": 50.0})
    for i in range(10):
        pnl = 50.0 if i < 3 else -10.0
        trades.append({"exit_ts": f"2026-05-{1+i:02d}T12:00:00+00:00", "pnl_usd": pnl})
    metrics = compute_rolling_metrics_from_trades(
        trades, now=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    assert metrics["win_rate_10_trades"] == 0.3


def test_win_rate_10_trades_dbwrapper_coerces_none_to_zero(tmp_path, monkeypatch):
    """compute_rolling_metrics (DB wrapper) coerces None → 0.0 same as for win_rate_20_trades."""
    import btc_api
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    from datetime import datetime, timezone
    from health import compute_rolling_metrics
    conn = btc_api.get_db()
    try:
        metrics = compute_rolling_metrics("BTC", conn, now=datetime(2026, 6, 15, tzinfo=timezone.utc))
    finally:
        conn.close()
    assert metrics["win_rate_10_trades"] == 0.0
