"""Sanity tests for notifier package. Proves the package imports and exposes
the dataclass event types the rest of the system will use."""


def test_notifier_package_imports():
    import notifier  # noqa: F401


def test_event_types_exported():
    from notifier import SignalEvent, HealthEvent, InfraEvent, SystemEvent  # noqa: F401


def test_signal_event_required_fields():
    from notifier import SignalEvent
    ev = SignalEvent(
        symbol="BTCUSDT", score=6, direction="LONG",
        entry=50_000.0, sl=49_000.0, tp=55_000.0,
    )
    assert ev.event_type == "signal"
    assert ev.priority == "info"  # default
    assert ev.dedupe_key == "signal:BTCUSDT"


def test_signal_event_lrc_pct_optional_default_none():
    """#385: lrc_pct is optional — callers that don't have it (legacy paths,
    tests) pass None and the frontend renders '?' instead of fabricating."""
    from notifier import SignalEvent
    ev = SignalEvent(
        symbol="BTCUSDT", score=6, direction="LONG",
        entry=50_000.0, sl=49_000.0, tp=55_000.0,
    )
    assert ev.lrc_pct is None
    assert ev.to_dict()["lrc_pct"] is None


def test_signal_event_lrc_pct_persisted_in_payload():
    """#385: when callers DO have an LRC percentile, it survives to_dict()
    so notifier.storage can persist it for the bell."""
    from notifier import SignalEvent
    ev = SignalEvent(
        symbol="RUNEUSDT", score=4, direction="SHORT",
        entry=4.20, sl=4.31, tp=4.05,
        lrc_pct=87.3,
    )
    payload = ev.to_dict()
    assert payload["lrc_pct"] == 87.3
    # Round-trip through json (the bell payload_json column is text)
    import json
    assert json.loads(json.dumps(payload))["lrc_pct"] == 87.3


def test_health_event_required_fields():
    from notifier import HealthEvent
    ev = HealthEvent(
        symbol="JUPUSDT", from_state="REDUCED", to_state="PAUSED",
        reason="3mo_consec_neg", metrics={"pnl_30d": -500},
    )
    assert ev.event_type == "health"
    assert ev.priority == "warning"  # default
    assert ev.dedupe_key == "health:JUPUSDT:PAUSED"


def test_infra_event_severity_maps_to_priority():
    from notifier import InfraEvent
    ev = InfraEvent(component="scanner", severity="critical", message="died")
    assert ev.priority == "critical"
    crit = InfraEvent(component="x", severity="info", message="ok")
    assert crit.priority == "info"


def test_system_event_defaults():
    from notifier import SystemEvent
    ev = SystemEvent(kind="startup", message="API online")
    assert ev.event_type == "system"
    assert ev.priority == "info"


def test_event_to_dict_serializable():
    """to_dict() must produce a JSON-serializable dict (used by _storage)."""
    import json
    from notifier import SignalEvent
    ev = SignalEvent(symbol="BTCUSDT", score=6, direction="LONG",
                      entry=50_000.0, sl=49_000.0, tp=55_000.0)
    d = ev.to_dict()
    json.dumps(d)  # must not raise
    assert d["symbol"] == "BTCUSDT"
    assert d["event_type"] == "signal"


def test_signal_event_health_state_default():
    """Default health_state is 'NORMAL' so existing callers stay backward-compat."""
    from notifier import SignalEvent
    ev = SignalEvent(symbol="BTC", score=5, direction="LONG",
                     entry=1.0, sl=1.0, tp=1.0)
    assert ev.health_state == "NORMAL"


def test_signal_event_health_state_set_to_alert():
    from notifier import SignalEvent
    ev = SignalEvent(symbol="BTC", score=5, direction="LONG",
                     entry=1.0, sl=1.0, tp=1.0, health_state="ALERT")
    assert ev.health_state == "ALERT"
