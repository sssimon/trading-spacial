"""check_position_stops time-limit barrier — live (non-backtest) auto-close path.

When a position has been open for >= cfg.symbol_overrides[symbol].time_limit_hours,
the next price tick closes it with reason TIME_LIMIT_HIT (mapped to TIME_LIMIT
for the notifier templates).

Pinned invariants:
- SL/TP win over time-limit when both met in the same tick.
- Stateless config resolution: edits apply retroactively to open positions;
  a defensive log.warning surfaces closures that fire materially past horizon.
- check_position_stops accepts an optional `now` parameter (defaults to
  datetime.now(UTC)) to make these tests deterministic.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

import api.positions as _pos_mod


# ─────────────────────────────────────────────────────────────────────────────
# Fixture: fresh DB + config injection per test
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def setup_db_and_cfg(tmp_path, monkeypatch):
    """Fresh DB. cfg returned by api.positions.load_config is overridable
    via the `set_cfg` callable yielded to the test. Also resets the shared
    validator warning-throttle set so each test starts with no previous
    warnings recorded."""
    import btc_api
    from strategy import _validators

    db_path = str(tmp_path / "test_pos.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    btc_api.init_db()

    cfg_holder: dict = {"cfg": {}}

    def fake_load_config():
        return cfg_holder["cfg"]

    monkeypatch.setattr(_pos_mod, "load_config", fake_load_config)
    monkeypatch.setattr(_validators, "_validator_warned", set())

    def set_cfg(cfg: dict):
        cfg_holder["cfg"] = cfg

    yield set_cfg


def _open_btc_position(entry_ts_iso: str, *, sl: float = 50000.0, tp: float = 80000.0):
    import btc_api
    return btc_api.db_create_position({
        "symbol": "BTCUSDT",
        "entry_price": 65000.0,
        "sl_price": sl,
        "tp_price": tp,
        "direction": "LONG",
        "entry_ts": entry_ts_iso,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Time-limit firing
# ─────────────────────────────────────────────────────────────────────────────


def test_check_position_stops_time_limit_fires(setup_db_and_cfg):
    """now = entry_ts + 14h with BTC time_limit_hours=14 → close TIME_LIMIT_HIT."""
    import btc_api

    setup_db_and_cfg({
        "symbol_overrides": {"BTCUSDT": {"time_limit_hours": 14}},
    })

    entry_dt = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    _open_btc_position(entry_dt.isoformat())

    now = entry_dt + timedelta(hours=14)
    btc_api.check_position_stops("BTCUSDT", 65500.0, now=now)

    closed = btc_api.db_get_positions(status="closed")
    assert len(closed) == 1
    assert closed[0]["exit_reason"] == "TIME_LIMIT_HIT"
    assert closed[0]["exit_price"] == 65500.0


def test_check_position_stops_time_limit_does_not_fire_before(setup_db_and_cfg):
    """now = entry_ts + 13h59m with TL=14 → position still open."""
    import btc_api

    setup_db_and_cfg({
        "symbol_overrides": {"BTCUSDT": {"time_limit_hours": 14}},
    })

    entry_dt = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    _open_btc_position(entry_dt.isoformat())

    now = entry_dt + timedelta(hours=13, minutes=59)
    btc_api.check_position_stops("BTCUSDT", 65500.0, now=now)

    open_pos = btc_api.db_get_positions(status="open")
    assert len(open_pos) == 1


def test_check_position_stops_sl_wins_over_time_limit(setup_db_and_cfg):
    """SL hit AND time-limit elapsed in same call → SL_HIT wins."""
    import btc_api

    setup_db_and_cfg({
        "symbol_overrides": {"BTCUSDT": {"time_limit_hours": 14}},
    })

    entry_dt = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    _open_btc_position(entry_dt.isoformat(), sl=63000.0)

    now = entry_dt + timedelta(hours=20)  # well past TL
    btc_api.check_position_stops("BTCUSDT", 62000.0, now=now)  # below SL

    closed = btc_api.db_get_positions(status="closed")
    assert len(closed) == 1
    assert closed[0]["exit_reason"] == "SL_HIT"


def test_check_position_stops_no_time_limit_in_config(setup_db_and_cfg):
    """No symbol_overrides → no time-limit applied, position remains open."""
    import btc_api

    setup_db_and_cfg({})  # empty cfg

    entry_dt = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    _open_btc_position(entry_dt.isoformat())

    now = entry_dt + timedelta(hours=100)
    btc_api.check_position_stops("BTCUSDT", 65500.0, now=now)

    open_pos = btc_api.db_get_positions(status="open")
    assert len(open_pos) == 1


def test_check_position_stops_retroactive_trigger_logs_warning(
    setup_db_and_cfg, caplog
):
    """When hours_open exceeds the horizon by more than the scanner-lag buffer,
    a defensive log.warning is emitted.

    Stateless config resolution means a lowered time_limit_hours edit can apply
    retroactively to an already-old open position. The warning makes that case
    visible to an operator instead of closing silently.
    """
    import btc_api

    setup_db_and_cfg({
        "symbol_overrides": {"BTCUSDT": {"time_limit_hours": 14}},
    })

    entry_dt = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    _open_btc_position(entry_dt.isoformat())

    # 30 hours open with TL=14 → 30 > 14 + 0.167 (scanner-lag buffer) → warning fires
    now = entry_dt + timedelta(hours=30)
    with caplog.at_level(logging.WARNING, logger="api.positions"):
        btc_api.check_position_stops("BTCUSDT", 65500.0, now=now)

    closed = btc_api.db_get_positions(status="closed")
    assert len(closed) == 1
    assert closed[0]["exit_reason"] == "TIME_LIMIT_HIT"

    matching = [r for r in caplog.records if "TIME_LIMIT" in r.getMessage()]
    assert matching, (
        f"expected a WARNING-level log mentioning TIME_LIMIT for retroactive "
        f"trigger; got records: {[r.getMessage() for r in caplog.records]}"
    )


def test_check_position_stops_signature_now_optional():
    """check_position_stops accepts `now=` as a keyword argument; default is current UTC."""
    import inspect
    import btc_api

    sig = inspect.signature(btc_api.check_position_stops)
    assert "now" in sig.parameters, (
        f"check_position_stops signature missing `now` param; got {sig}"
    )
    param = sig.parameters["now"]
    assert param.default is None, (
        f"`now` must default to None (so func computes datetime.now(UTC)); "
        f"got default={param.default}"
    )


def test_check_position_stops_no_now_arg_uses_utcnow(setup_db_and_cfg):
    """Production scanner path: caller passes no `now=`, function uses datetime.now(UTC)."""
    import btc_api

    setup_db_and_cfg({
        "symbol_overrides": {"BTCUSDT": {"time_limit_hours": 14}},
    })

    entry_dt = datetime.now(timezone.utc) - timedelta(hours=30)
    _open_btc_position(entry_dt.isoformat())

    btc_api.check_position_stops("BTCUSDT", 65500.0)

    closed = btc_api.db_get_positions(status="closed")
    assert len(closed) == 1
    assert closed[0]["exit_reason"] == "TIME_LIMIT_HIT"


def test_check_position_stops_non_aligned_entry_ts_boundary(setup_db_and_cfg):
    """entry_ts at 12:00:00.500 with TL=5h. Boundary precision must be exact:
    now=17:00:00.000 → hours_open=4.999... should NOT fire.
    now=17:00:00.501 → hours_open=5.0+ → fires."""
    import btc_api

    setup_db_and_cfg({
        "symbol_overrides": {"BTCUSDT": {"time_limit_hours": 5}},
    })

    entry_dt = datetime(2025, 1, 1, 12, 0, 0, 500_000, tzinfo=timezone.utc)
    _open_btc_position(entry_dt.isoformat())

    now_just_before = datetime(2025, 1, 1, 17, 0, 0, 0, tzinfo=timezone.utc)
    btc_api.check_position_stops("BTCUSDT", 65500.0, now=now_just_before)
    assert len(btc_api.db_get_positions(status="open")) == 1, (
        "hours_open=4.9999...h must not trigger TL=5h"
    )

    now_just_after = datetime(2025, 1, 1, 17, 0, 0, 600_000, tzinfo=timezone.utc)
    btc_api.check_position_stops("BTCUSDT", 65500.0, now=now_just_after)
    closed = btc_api.db_get_positions(status="closed")
    assert len(closed) == 1
    assert closed[0]["exit_reason"] == "TIME_LIMIT_HIT"


def test_check_position_stops_time_limit_notifier_event_payload(
    setup_db_and_cfg, monkeypatch
):
    """Notifier receives PositionExitEvent with exit_reason='TIME_LIMIT' (not 'TIME_LIMIT_HIT')."""
    import btc_api

    setup_db_and_cfg({
        "symbol_overrides": {"BTCUSDT": {"time_limit_hours": 14}},
    })

    captured = []
    monkeypatch.setattr(
        "notifier.notify",
        lambda event, cfg: captured.append(event),
    )

    entry_dt = datetime.now(timezone.utc) - timedelta(hours=20)
    _open_btc_position(entry_dt.isoformat())

    btc_api.check_position_stops("BTCUSDT", 65500.0)

    assert len(captured) == 1, f"expected 1 PositionExitEvent, got {captured}"
    event = captured[0]
    assert event.exit_reason == "TIME_LIMIT", (
        f"notifier must receive 'TIME_LIMIT' (template tier code), not raw "
        f"DB reason; got {event.exit_reason!r}"
    )


def test_check_position_stops_malformed_entry_ts_skips_position(
    setup_db_and_cfg, caplog
):
    """Position with malformed entry_ts: log.warning, skip THIS position only;
    other positions for the same symbol still process via the time-limit block."""
    import btc_api

    setup_db_and_cfg({
        "symbol_overrides": {"BTCUSDT": {"time_limit_hours": 14}},
    })

    # Position 1: malformed entry_ts, SL/TP set far from price so neither
    # fires (forces the call to reach the time-limit block where the bad
    # entry_ts will be parsed).
    btc_api.db_create_position({
        "symbol": "BTCUSDT",
        "entry_price": 65000.0,
        "sl_price": 50000.0,
        "tp_price": 80000.0,
        "direction": "LONG",
        "entry_ts": "not-an-iso-timestamp",
    })
    # Position 2: valid entry_ts, far SL/TP, 30h old (beyond TL=14h).
    btc_api.db_create_position({
        "symbol": "BTCUSDT",
        "entry_price": 65000.0,
        "sl_price": 50000.0,
        "tp_price": 80000.0,
        "direction": "LONG",
        "entry_ts": (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat(),
    })

    with caplog.at_level(logging.WARNING, logger="api.positions"):
        btc_api.check_position_stops("BTCUSDT", 65500.0)  # neither SL nor TP

    closed = btc_api.db_get_positions(status="closed")
    assert len(closed) == 1
    assert closed[0]["exit_reason"] == "TIME_LIMIT_HIT"
    assert closed[0]["entry_ts"] != "not-an-iso-timestamp"

    open_pos = btc_api.db_get_positions(status="open")
    assert len(open_pos) == 1
    assert open_pos[0]["entry_ts"] == "not-an-iso-timestamp"

    assert any(
        "malformed" in r.getMessage().lower() or "entry_ts" in r.getMessage()
        for r in caplog.records
    ), f"expected warning about malformed entry_ts; got {[r.getMessage() for r in caplog.records]}"


def test_check_position_stops_invalid_type_rejected(setup_db_and_cfg, caplog):
    """time_limit_hours='14' (string) → log.warning, no time-limit applied."""
    import btc_api

    setup_db_and_cfg({
        "symbol_overrides": {"BTCUSDT": {"time_limit_hours": "14"}},
    })

    entry_dt = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    _open_btc_position(entry_dt.isoformat())

    now = entry_dt + timedelta(hours=20)
    with caplog.at_level(logging.WARNING, logger="api.positions"):
        btc_api.check_position_stops("BTCUSDT", 65500.0, now=now)

    open_pos = btc_api.db_get_positions(status="open")
    assert len(open_pos) == 1
    assert any("time_limit_hours" in r.getMessage() for r in caplog.records)


def test_check_position_stops_negative_value_rejected(setup_db_and_cfg, caplog):
    """time_limit_hours=-5 → log.warning, no time-limit applied."""
    import btc_api

    setup_db_and_cfg({
        "symbol_overrides": {"BTCUSDT": {"time_limit_hours": -5}},
    })

    entry_dt = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    _open_btc_position(entry_dt.isoformat())

    now = entry_dt + timedelta(hours=20)
    with caplog.at_level(logging.WARNING, logger="api.positions"):
        btc_api.check_position_stops("BTCUSDT", 65500.0, now=now)

    open_pos = btc_api.db_get_positions(status="open")
    assert len(open_pos) == 1
    assert any("time_limit_hours" in r.getMessage() for r in caplog.records)


def test_check_position_stops_zero_value_rejected(setup_db_and_cfg, caplog):
    """time_limit_hours=0 → log.warning, no time-limit applied (0 is degenerate)."""
    import btc_api

    setup_db_and_cfg({
        "symbol_overrides": {"BTCUSDT": {"time_limit_hours": 0}},
    })

    entry_dt = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    _open_btc_position(entry_dt.isoformat())

    now = entry_dt + timedelta(hours=100)
    with caplog.at_level(logging.WARNING, logger="api.positions"):
        btc_api.check_position_stops("BTCUSDT", 65500.0, now=now)

    open_pos = btc_api.db_get_positions(status="open")
    assert len(open_pos) == 1
    assert any("time_limit_hours" in r.getMessage() for r in caplog.records)


def test_write_position_event_log_time_limit_label(
    setup_db_and_cfg, tmp_path, monkeypatch
):
    """_write_position_event_log must label TIME_LIMIT_HIT closes as 'TIME LIMIT', not 'STOP LOSS'."""
    import btc_api

    log_path = tmp_path / "signals_log.txt"
    monkeypatch.setattr(_pos_mod, "SIGNALS_LOG_FILE", str(log_path))

    setup_db_and_cfg({
        "symbol_overrides": {"BTCUSDT": {"time_limit_hours": 14}},
    })

    entry_dt = datetime.now(timezone.utc) - timedelta(hours=20)
    _open_btc_position(entry_dt.isoformat())

    btc_api.check_position_stops("BTCUSDT", 65500.0)

    assert log_path.exists(), "signals log file should be written"
    contents = log_path.read_text()
    assert "TIME LIMIT" in contents, (
        f"log should contain 'TIME LIMIT' for TIME_LIMIT_HIT close; got: {contents}"
    )
    assert "STOP LOSS" not in contents, (
        f"log must not mislabel TIME_LIMIT_HIT as 'STOP LOSS'; got: {contents}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Validator edge cases — bool / NaN / Inf must not silently pass through
# ─────────────────────────────────────────────────────────────────────────────


def test_check_position_stops_time_limit_bool_rejected(setup_db_and_cfg, caplog):
    """time_limit_hours=True (bool) is wrong type — must be rejected.

    Without the bool guard, isinstance(True, (int, float)) is True (bool subclasses
    int) and the value would be accepted as 1.0, closing every position 1h after entry.
    """
    import btc_api

    setup_db_and_cfg({
        "symbol_overrides": {"BTCUSDT": {"time_limit_hours": True}},
    })

    entry_dt = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    _open_btc_position(entry_dt.isoformat())

    now = entry_dt + timedelta(hours=10)
    with caplog.at_level(logging.WARNING, logger="api.positions"):
        btc_api.check_position_stops("BTCUSDT", 65500.0, now=now)

    open_pos = btc_api.db_get_positions(status="open")
    assert len(open_pos) == 1, "bool value must not collapse to 1.0 and close every position"
    assert any("time_limit_hours" in r.getMessage() for r in caplog.records)


def test_check_position_stops_time_limit_nan_rejected(setup_db_and_cfg, caplog):
    """time_limit_hours=NaN must be rejected — `hours_open >= nan` is always False
    so without the finite-check the time-limit silently never fires."""
    import btc_api

    setup_db_and_cfg({
        "symbol_overrides": {"BTCUSDT": {"time_limit_hours": float("nan")}},
    })

    entry_dt = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    _open_btc_position(entry_dt.isoformat())

    now = entry_dt + timedelta(hours=100)  # well past any reasonable horizon
    with caplog.at_level(logging.WARNING, logger="api.positions"):
        btc_api.check_position_stops("BTCUSDT", 65500.0, now=now)

    open_pos = btc_api.db_get_positions(status="open")
    assert len(open_pos) == 1
    assert any("time_limit_hours" in r.getMessage() for r in caplog.records)


def test_check_position_stops_time_limit_inf_rejected(setup_db_and_cfg, caplog):
    """time_limit_hours=Inf must be rejected — `hours_open >= inf` is always False."""
    import btc_api

    setup_db_and_cfg({
        "symbol_overrides": {"BTCUSDT": {"time_limit_hours": float("inf")}},
    })

    entry_dt = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    _open_btc_position(entry_dt.isoformat())

    now = entry_dt + timedelta(hours=100)
    with caplog.at_level(logging.WARNING, logger="api.positions"):
        btc_api.check_position_stops("BTCUSDT", 65500.0, now=now)

    open_pos = btc_api.db_get_positions(status="open")
    assert len(open_pos) == 1
    assert any("time_limit_hours" in r.getMessage() for r in caplog.records)


# ─────────────────────────────────────────────────────────────────────────────
# scan_interval_sec validation — bad cfg value must fall back to default
# ─────────────────────────────────────────────────────────────────────────────


def test_check_position_stops_invalid_scan_interval_falls_back(
    setup_db_and_cfg, caplog
):
    """Bad scan_interval_sec (string, negative, zero, NaN, bool) falls back to 300
    (the default). Without validation, a string would crash with TypeError
    inside the buffer arithmetic."""
    import btc_api

    setup_db_and_cfg({
        "symbol_overrides": {"BTCUSDT": {"time_limit_hours": 14}},
        "scan_interval_sec": "300",  # string from hand-edited config
    })

    entry_dt = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    _open_btc_position(entry_dt.isoformat())

    now = entry_dt + timedelta(hours=30)
    with caplog.at_level(logging.WARNING, logger="api.positions"):
        btc_api.check_position_stops("BTCUSDT", 65500.0, now=now)

    closed = btc_api.db_get_positions(status="closed")
    assert len(closed) == 1
    assert closed[0]["exit_reason"] == "TIME_LIMIT_HIT", (
        "string scan_interval must fall back to 300, not crash the close"
    )
    assert any("scan_interval_sec" in r.getMessage() for r in caplog.records)


# ─────────────────────────────────────────────────────────────────────────────
# Throttling — same misconfig must warn only once per (caller, symbol, kind)
# ─────────────────────────────────────────────────────────────────────────────


def test_validator_warning_throttled_per_symbol_error_kind(setup_db_and_cfg, caplog):
    """Repeated calls with the same misconfigured symbol emit exactly one warning,
    not one per tick. Critical for long-running scanner: a single bad config
    must not produce N warnings/hour."""
    import btc_api

    setup_db_and_cfg({
        "symbol_overrides": {"BTCUSDT": {"time_limit_hours": -5}},
    })

    entry_dt = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    _open_btc_position(entry_dt.isoformat())

    now = entry_dt + timedelta(hours=10)
    with caplog.at_level(logging.WARNING, logger="api.positions"):
        for _ in range(5):
            btc_api.check_position_stops("BTCUSDT", 65500.0, now=now)

    matching = [r for r in caplog.records if "time_limit_hours" in r.getMessage()]
    assert len(matching) == 1, (
        f"throttle must emit exactly 1 warning across 5 ticks for the same "
        f"(symbol, error_kind); got {len(matching)} warnings"
    )


# ─────────────────────────────────────────────────────────────────────────────
# _EVENT_LOG_LABELS dict completeness — catches future drift
# ─────────────────────────────────────────────────────────────────────────────


def test_event_log_labels_dict_completeness():
    """All known exit reasons map to distinct labels in _EVENT_LOG_LABELS."""
    from api.positions import _EVENT_LOG_LABELS

    assert _EVENT_LOG_LABELS["TP_HIT"] == "TAKE PROFIT"
    assert _EVENT_LOG_LABELS["TIME_LIMIT_HIT"] == "TIME LIMIT"
    assert _EVENT_LOG_LABELS["SL_HIT"] == "STOP LOSS"
    expected_keys = {"TP_HIT", "TIME_LIMIT_HIT", "SL_HIT"}
    assert set(_EVENT_LOG_LABELS.keys()) == expected_keys, (
        f"_EVENT_LOG_LABELS keys changed: {set(_EVENT_LOG_LABELS.keys())} vs "
        f"expected {expected_keys}. If adding a new exit reason, update both "
        f"the dict AND this test."
    )
