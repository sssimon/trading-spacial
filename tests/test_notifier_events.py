"""Sanity tests for notifier package. Proves the package imports and exposes
the dataclass event types the rest of the system will use."""
from datetime import datetime, timezone


def test_notifier_package_imports():
    import notifier  # noqa: F401


def test_event_types_exported():
    from notifier import SignalEvent, HealthEvent, InfraEvent, SystemEvent  # noqa: F401
