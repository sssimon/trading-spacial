"""Typed events consumed by notifier.notify().

All events share: event_type, priority, dedupe_key, to_dict().
Specific events add their own fields.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


Priority = str  # 'info' | 'warning' | 'critical'


@dataclass
class _BaseEvent:
    """Shared behavior. Do not instantiate directly.

    Subclasses MUST set `self.event_type` in `__post_init__`. The default empty
    string is a safety net so missing-override bugs surface as empty strings
    in logs rather than as `AttributeError` on the first attribute access.
    """
    event_type: str = field(init=False, default="")
    priority: Priority = field(init=False, default="info")

    @property
    def dedupe_key(self) -> str:
        return self.event_type

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["dedupe_key"] = self.dedupe_key
        return d


@dataclass
class SignalEvent(_BaseEvent):
    symbol: str = ""
    score: int = 0
    direction: str = "LONG"
    entry: float = 0.0
    sl: float = 0.0
    tp: float = 0.0

    def __post_init__(self):
        self.event_type = "signal"
        self.priority = "info"

    @property
    def dedupe_key(self) -> str:
        return f"signal:{self.symbol}"


@dataclass
class HealthEvent(_BaseEvent):
    symbol: str = ""
    from_state: str = "NORMAL"
    to_state: str = "NORMAL"
    reason: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.event_type = "health"
        self.priority = "warning"

    @property
    def dedupe_key(self) -> str:
        return f"health:{self.symbol}:{self.to_state}"


@dataclass
class InfraEvent(_BaseEvent):
    component: str = ""
    severity: str = "info"  # 'info' | 'warning' | 'critical'
    message: str = ""

    def __post_init__(self):
        self.event_type = "infra"
        self.priority = self.severity if self.severity in {"info", "warning", "critical"} else "warning"

    @property
    def dedupe_key(self) -> str:
        return f"infra:{self.component}"


@dataclass
class SystemEvent(_BaseEvent):
    kind: str = ""
    message: str = ""

    def __post_init__(self):
        self.event_type = "system"
        self.priority = "info"

    @property
    def dedupe_key(self) -> str:
        return f"system:{self.kind}"


Event = SignalEvent | HealthEvent | InfraEvent | SystemEvent
