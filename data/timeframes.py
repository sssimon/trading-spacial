"""Central registry of supported timeframes.

Adding a new timeframe = 1 line in TIMEFRAMES.
"""
from datetime import datetime, timezone


TIMEFRAMES: dict[str, int] = {
    "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "30m": 30 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
    "1w": 7 * 24 * 60 * 60 * 1000,
}


def delta_ms(timeframe: str) -> int:
    """Milliseconds per bar for this timeframe."""
    return TIMEFRAMES[timeframe]


def last_closed_bar_time(timeframe: str, now: datetime | None = None) -> int:
    """open_time of the last fully-closed bar at `now` (or datetime.now(UTC) if None).

    Returns ms UTC. A bar with open_time=T is considered CLOSED once now >= T + delta.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    d = delta_ms(timeframe)
    now_ms = int(now.timestamp() * 1000)
    return (now_ms // d - 1) * d
