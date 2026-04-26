"""B5 PROBATION tier — pure functions + state machine + DB lifecycle (#187 #199)."""
import pytest


# ── compute_probation_trades_remaining ──────────────────────────────────────


def test_compute_probation_trades_remaining_zero_days():
    """No days in PAUSED → trades_base unchanged."""
    from health import compute_probation_trades_remaining
    assert compute_probation_trades_remaining(0, trades_base=10, per_pause_day=0.2) == 10


def test_compute_probation_trades_remaining_negative_days_clamps():
    """Negative days_paused (clock skew, etc.) → trades_base."""
    from health import compute_probation_trades_remaining
    assert compute_probation_trades_remaining(-3, trades_base=10, per_pause_day=0.2) == 10


def test_compute_probation_trades_remaining_seven_days():
    """7 days * 0.2 = 1.4 → rounds to 11 (10 + 1)."""
    from health import compute_probation_trades_remaining
    assert compute_probation_trades_remaining(7, trades_base=10, per_pause_day=0.2) == 11


def test_compute_probation_trades_remaining_fifteen_days():
    """15 days * 0.2 = 3 → 13 (spec example)."""
    from health import compute_probation_trades_remaining
    assert compute_probation_trades_remaining(15, trades_base=10, per_pause_day=0.2) == 13


def test_compute_probation_trades_remaining_thirty_days():
    """30 days * 0.2 = 6 → 16."""
    from health import compute_probation_trades_remaining
    assert compute_probation_trades_remaining(30, trades_base=10, per_pause_day=0.2) == 16


def test_compute_probation_trades_remaining_default_args():
    """Defaults match spec: trades_base=10, per_pause_day=0.2."""
    from health import compute_probation_trades_remaining
    assert compute_probation_trades_remaining(15) == 13


# ── reactivate_symbol PAUSED → PROBATION ────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import btc_api
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    yield db_path


def _seed_paused(symbol, days_ago):
    """Insert a PAUSED row whose state_since is `days_ago` days before now."""
    from datetime import datetime, timezone, timedelta
    import btc_api
    state_since = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    conn = btc_api.get_db()
    try:
        conn.execute(
            """INSERT INTO symbol_health
               (symbol, state, state_since, last_evaluated_at, last_metrics_json)
               VALUES (?, 'PAUSED', ?, ?, '{}')""",
            (symbol, state_since, state_since),
        )
        conn.commit()
    finally:
        conn.close()


def _read_health_row(symbol):
    import btc_api
    conn = btc_api.get_db()
    try:
        row = conn.execute(
            """SELECT state, probation_trades_remaining, probation_started_at,
                      paused_days_at_entry, manual_override
               FROM symbol_health WHERE symbol=?""",
            (symbol,),
        ).fetchone()
    finally:
        conn.close()
    return row


def test_reactivate_symbol_paused_zero_days_to_probation_base_count(tmp_db):
    """PAUSED for 0 days → PROBATION + trades_remaining=10 (default trades_base)."""
    from health import reactivate_symbol
    _seed_paused("BTC", days_ago=0)
    reactivate_symbol("BTC", reason="manual")
    row = _read_health_row("BTC")
    assert row[0] == "PROBATION"
    assert row[1] == 10
    assert row[2] is not None  # probation_started_at populated
    assert row[3] == 0


def test_reactivate_symbol_paused_seven_days_to_probation_eleven(tmp_db):
    """PAUSED for 7 days → PROBATION + trades_remaining=11 (10 + round(0.2*7) = 11)."""
    from health import reactivate_symbol
    _seed_paused("ETH", days_ago=7)
    reactivate_symbol("ETH", reason="manual")
    row = _read_health_row("ETH")
    assert row[0] == "PROBATION"
    assert row[1] == 11
    assert row[3] == 7


def test_reactivate_symbol_paused_fifteen_days_to_probation_thirteen(tmp_db):
    """Spec example: 15 days paused → 13 trades_remaining."""
    from health import reactivate_symbol
    _seed_paused("DOGE", days_ago=15)
    reactivate_symbol("DOGE", reason="manual")
    row = _read_health_row("DOGE")
    assert row[0] == "PROBATION"
    assert row[1] == 13
    assert row[3] == 15


def test_reactivate_symbol_sets_manual_override_for_manual_reason(tmp_db):
    """reason='manual' sets manual_override=1; reason='auto_recovery' sets 0."""
    from health import reactivate_symbol
    _seed_paused("UNI", days_ago=10)
    reactivate_symbol("UNI", reason="manual")
    assert _read_health_row("UNI")[4] == 1

    _seed_paused("XLM", days_ago=10)
    reactivate_symbol("XLM", reason="auto_recovery")
    assert _read_health_row("XLM")[4] == 0


def test_reactivate_symbol_noop_when_not_paused(tmp_db, caplog):
    """Calling reactivate_symbol on a NORMAL symbol is a no-op + warning."""
    import logging
    from health import reactivate_symbol
    # Seed NORMAL (no row → reactivate sees state='NORMAL' default)
    with caplog.at_level(logging.WARNING, logger="health"):
        reactivate_symbol("AVAX", reason="manual")
    # Row may or may not exist; the key assertion is no PROBATION transition occurred.
    row = _read_health_row("AVAX")
    if row is not None:
        assert row[0] != "PROBATION"
    # Warning logged
    assert any("not in PAUSED" in r.message or "not paused" in r.message.lower()
               for r in caplog.records)
