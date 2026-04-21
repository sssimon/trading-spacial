"""Sanity tests for notifier package. Proves the package imports and exposes
the dataclass event types the rest of the system will use."""
from datetime import datetime, timezone


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
