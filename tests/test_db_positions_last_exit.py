"""db_last_exit_ts(symbol) — direct unit coverage."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from db.positions import db_last_exit_ts
from db.transaction import transaction


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Fresh schema'd DB per test."""
    import btc_api
    db_path = str(tmp_path / "test_last_exit.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    btc_api.init_db()
    return btc_api  # for convenience callers


def _insert_position(
    btc_api,
    *,
    symbol: str = "BTCUSDT",
    status: str = "closed",
    entry_price: float = 100.0,
    entry_ts: str = "2026-01-01T00:00:00+00:00",
    exit_ts: str | None = "2026-01-02T00:00:00+00:00",
    direction: str = "LONG",
):
    """Insert a position row directly via db helper."""
    with transaction() as con:
        pos = btc_api.db_create_position(con, {
            "symbol": symbol,
            "entry_price": entry_price,
            "direction": direction,
            "entry_ts": entry_ts,
        })
    if status == "closed":
        with transaction() as con:
            con.execute(
                "UPDATE positions SET status=?, exit_ts=? WHERE id=?",
                (status, exit_ts, pos["id"]),
            )
    return pos


def _last_exit(symbol: str):
    with transaction() as con:
        return db_last_exit_ts(con, symbol)


# ── Path A: empty DB / no closed positions ─────────────────────────────────


def test_no_positions_returns_none(tmp_db):
    """Fresh DB → None (cooldown free for first signal of every symbol)."""
    assert _last_exit("BTCUSDT") is None


def test_only_open_position_returns_none(tmp_db):
    """Open positions don't count — cooldown only respects closed exits."""
    _insert_position(tmp_db, status="open", exit_ts=None)
    assert _last_exit("BTCUSDT") is None


# ── Path B: single closed position ─────────────────────────────────────────


def test_single_closed_returns_its_exit_ts(tmp_db):
    """Returns tz-aware UTC datetime."""
    _insert_position(tmp_db, exit_ts="2026-01-02T15:30:00+00:00")
    result = _last_exit("BTCUSDT")
    assert isinstance(result, datetime)
    assert result == datetime(2026, 1, 2, 15, 30, 0, tzinfo=timezone.utc)
    assert result.tzinfo is not None


# ── Path C: multiple closed → most recent ──────────────────────────────────


def test_multiple_closed_returns_most_recent(tmp_db):
    """ORDER BY exit_ts DESC LIMIT 1 — newest wins."""
    _insert_position(tmp_db, exit_ts="2026-01-01T10:00:00+00:00")
    _insert_position(tmp_db, exit_ts="2026-01-03T10:00:00+00:00")  # most recent
    _insert_position(tmp_db, exit_ts="2026-01-02T10:00:00+00:00")
    result = _last_exit("BTCUSDT")
    assert result == datetime(2026, 1, 3, 10, 0, 0, tzinfo=timezone.utc)


# ── Path D: NULL exit_ts ─────────────────────────────────────────────────


def test_closed_with_null_exit_ts_returns_none(tmp_db):
    """NULL exit_ts on a 'closed' row is malformed — treat as no exit."""
    _insert_position(tmp_db, exit_ts=None)  # status will still get set to closed
    assert _last_exit("BTCUSDT") is None


# ── Path E: symbol case ────────────────────────────────────────────────────


def test_lowercase_symbol_normalized_to_upper(tmp_db):
    """`symbol.upper()` so 'btc' / 'BtcUsdT' all hit 'BTCUSDT' rows."""
    _insert_position(tmp_db, symbol="BTCUSDT", exit_ts="2026-01-02T00:00:00+00:00")
    assert _last_exit("btcusdt") == datetime(2026, 1, 2, tzinfo=timezone.utc)
    assert _last_exit("BtcUsdT") == datetime(2026, 1, 2, tzinfo=timezone.utc)


# ── Path F: tz-naive ISO strings ───────────────────────────────────────────


def test_naive_iso_promoted_to_utc(tmp_db):
    """Legacy naive ISO rows get tz=UTC attached for safe arithmetic."""
    _insert_position(tmp_db, exit_ts="2026-01-02T15:30:00")  # NO tz
    result = _last_exit("BTCUSDT")
    assert isinstance(result, datetime)
    assert result.tzinfo is not None
    # Check value preserved (tz attached, not converted from another zone)
    assert result.replace(tzinfo=None) == datetime(2026, 1, 2, 15, 30, 0)


# ── Path G: malformed ISO ──────────────────────────────────────────────────


def test_malformed_exit_ts_returns_none(tmp_db):
    """ValueError from datetime.fromisoformat must be caught — no crash."""
    _insert_position(tmp_db, exit_ts="not-a-real-iso-string")
    assert _last_exit("BTCUSDT") is None
