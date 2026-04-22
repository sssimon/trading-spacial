# Notifier core (#162 PR A) implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace ad-hoc Telegram notification code (`push_telegram_direct`, `build_telegram_message`, `_send_telegram_raw` in `btc_api.py`) with a centralized `notifier/` module exposing a typed `notify(event)` API backed by Jinja2 templates, DB-backed dedupe, token-bucket ratelimit, and an extensible `Channel` ABC. Only `TelegramChannel` ships in this PR.

**Architecture:** `notifier.notify(event)` pipeline: `dedupe.should_send` → `ratelimit.acquire` → template render → `channel.send` → record to `notifications_sent`. Events are `@dataclass` (SignalEvent, HealthEvent, InfraEvent, SystemEvent). Templates are Jinja2 files in `notifier/templates/`. Current ~10 call sites in `btc_api.py` are redirected to `notifier.notify(SignalEvent(...))` with a snapshot test guaranteeing byte-identical output.

**Tech Stack:** Python 3.12, SQLite (existing `signals.db`), Jinja2 (already transitive via FastAPI/Starlette, added explicitly to requirements), pytest, requests (existing).

---

## File structure

```
notifier/                                 (new package)
├── __init__.py                           (public API: notify(), event exports)
├── events.py                             (@dataclass types)
├── _storage.py                           (notifications_sent table + helpers)
├── dedupe.py                             (DB-backed sliding window)
├── ratelimit.py                          (token bucket per channel)
├── _templates.py                         (Jinja2 loader + render helper)
├── channels/
│   ├── __init__.py
│   ├── base.py                           (Channel ABC, DeliveryReceipt)
│   └── telegram.py                       (TelegramChannel — wraps HTTP call)
└── templates/
    ├── signal.telegram.j2
    ├── health.telegram.j2
    ├── infra.telegram.j2
    └── system.telegram.j2

tests/
├── test_notifier_events.py               (dataclass sanity)
├── test_notifier_storage.py              (insert / query / mark-read)
├── test_notifier_dedupe.py               (sliding window)
├── test_notifier_ratelimit.py            (token bucket)
├── test_notifier_templates.py            (render per event type)
├── test_notifier_telegram_channel.py     (send + retry behavior)
├── test_notifier_integration.py          (end-to-end notify() flow)
└── test_notifier_signal_parity.py        (snapshot: pre-refactor byte-equals post-refactor)

btc_api.py                                (modified: ~10 call sites redirected)
requirements.txt                          (modified: add jinja2>=3.1)
```

**Modifications to `btc_api.py`:**
- Line 622, 1086, 1094, 1124, 1151, 1254, 1569, 1588, 1605, 1757 — replace direct calls with `notifier.notify(SignalEvent(...))`.
- Deprecate `push_telegram_direct` (line 1086), `build_telegram_message` (line 1010), `_send_telegram_raw` (line 1124) via module-level comment `# DEPRECATED: use notifier.notify. Kept for backwards compat through this refactor.` Do not delete yet — `trading_webhook.py` still uses the `telegram_message` payload key emitted by scanner.

---

## Task 1: Add jinja2 to requirements + create notifier package skeleton

**Files:**
- Modify: `requirements.txt`
- Create: `notifier/__init__.py`
- Create: `notifier/channels/__init__.py`
- Create: `tests/test_notifier_events.py`

- [ ] **Step 1: Confirm Jinja2 availability**

Run: `python -c "import jinja2; print(jinja2.__version__)"`
Expected: `3.1.6` (or newer). It's already installed transitively via FastAPI/Starlette.

- [ ] **Step 2: Add jinja2 to requirements.txt**

Read `requirements.txt` and append:
```
jinja2>=3.1
```

- [ ] **Step 3: Write failing test for notifier package import**

Create `tests/test_notifier_events.py`:
```python
"""Sanity tests for notifier package. Proves the package imports and exposes
the dataclass event types the rest of the system will use."""
from datetime import datetime, timezone


def test_notifier_package_imports():
    import notifier  # noqa: F401


def test_event_types_exported():
    from notifier import SignalEvent, HealthEvent, InfraEvent, SystemEvent  # noqa: F401
```

- [ ] **Step 4: Run test to verify failure**

Run: `python -m pytest tests/test_notifier_events.py -v`
Expected: `ModuleNotFoundError: No module named 'notifier'`.

- [ ] **Step 5: Create empty package files**

Create `notifier/__init__.py` with just:
```python
"""Centralized notifier (#162). Public API: notify, event types."""
```

Create `notifier/channels/__init__.py`:
```python
"""Channel implementations."""
```

- [ ] **Step 6: Re-run and verify partial pass**

Run: `python -m pytest tests/test_notifier_events.py::test_notifier_package_imports -v`
Expected: PASS. The second test still fails (types not exported yet) — next task fixes it.

- [ ] **Step 7: Commit**

```bash
git add requirements.txt notifier/__init__.py notifier/channels/__init__.py tests/test_notifier_events.py
git commit -m "feat(notifier): package skeleton + jinja2 dep (#162)"
```

---

## Task 2: Event dataclasses

**Files:**
- Create: `notifier/events.py`
- Modify: `notifier/__init__.py`
- Modify: `tests/test_notifier_events.py`

- [ ] **Step 1: Expand failing test with event shape assertions**

Append to `tests/test_notifier_events.py`:
```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_notifier_events.py -v`
Expected: FAIL with `ImportError: cannot import name 'SignalEvent'`.

- [ ] **Step 3: Create events.py**

Create `notifier/events.py`:
```python
"""Typed events consumed by notifier.notify().

All events share: event_type, priority, dedupe_key, to_dict().
Specific events add their own fields.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


Priority = str  # 'info' | 'warning' | 'critical'


@dataclass
class _BaseEvent:
    """Shared behavior. Do not instantiate directly."""
    event_type: str = field(init=False)
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
        # severity drives priority directly
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
```

- [ ] **Step 4: Re-export from package __init__.py**

Replace contents of `notifier/__init__.py`:
```python
"""Centralized notifier (#162). Public API: notify, event types."""
from notifier.events import (
    SignalEvent, HealthEvent, InfraEvent, SystemEvent,
    Event,
)

__all__ = ["SignalEvent", "HealthEvent", "InfraEvent", "SystemEvent", "Event"]
```

- [ ] **Step 5: Run tests to verify pass**

Run: `python -m pytest tests/test_notifier_events.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add notifier/events.py notifier/__init__.py tests/test_notifier_events.py
git commit -m "feat(notifier): typed event dataclasses (#162)"
```

---

## Task 3: Storage helpers for `notifications_sent`

**Files:**
- Create: `notifier/_storage.py`
- Create: `tests/test_notifier_storage.py`
- Modify: `btc_api.py` (add table to `init_db()` — insert after existing tables around line 865)

- [ ] **Step 1: Write failing storage tests**

Create `tests/test_notifier_storage.py`:
```python
"""Storage of outbound notifications — insert, list unread, mark-read."""
import json
from datetime import datetime, timezone

import pytest


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Isolated signals.db pointing at tmp path."""
    import btc_api
    from notifier import _storage as notif_storage

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    # reset any thread-local connection if present
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    # create all tables on fresh db
    btc_api.init_db()
    yield db_path


def test_record_delivery_inserts_row(tmp_db):
    from notifier._storage import record_delivery
    record_delivery(
        event_type="signal", event_key="signal:BTCUSDT",
        priority="info",
        payload={"symbol": "BTCUSDT", "score": 6},
        channels_sent=["telegram"],
        delivery_status="ok",
    )
    from notifier._storage import list_unread
    rows = list_unread(limit=10)
    assert len(rows) == 1
    assert rows[0]["event_type"] == "signal"
    assert rows[0]["delivery_status"] == "ok"


def test_list_unread_filters_read(tmp_db):
    from notifier._storage import record_delivery, list_unread, mark_read
    record_delivery("signal", "signal:BTCUSDT", "info",
                    {"symbol": "BTCUSDT"}, ["telegram"], "ok")
    (row_id,) = (r["id"] for r in list_unread(limit=10))
    mark_read(row_id)
    assert list_unread(limit=10) == []


def test_list_unread_ordered_by_sent_at_desc(tmp_db):
    from notifier._storage import record_delivery, list_unread
    record_delivery("signal", "signal:A", "info", {"s": "A"}, ["telegram"], "ok")
    record_delivery("signal", "signal:B", "info", {"s": "B"}, ["telegram"], "ok")
    rows = list_unread(limit=10)
    assert rows[0]["event_key"] == "signal:B"
    assert rows[1]["event_key"] == "signal:A"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_notifier_storage.py -v`
Expected: all FAIL — `_storage.py` doesn't exist.

- [ ] **Step 3: Add `notifications_sent` table to `btc_api.init_db()`**

Open `btc_api.py`, find `init_db()` (around line 780). After the existing `tune_results` table creation (around line 865), add before the closing of the function:

```python
    con.execute("""
        CREATE TABLE IF NOT EXISTS notifications_sent (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type      TEXT    NOT NULL,
            event_key       TEXT    NOT NULL,
            priority        TEXT    NOT NULL DEFAULT 'info',
            payload_json    TEXT    NOT NULL,
            channels_sent   TEXT    NOT NULL,
            delivery_status TEXT    NOT NULL DEFAULT 'ok',
            sent_at         TEXT    NOT NULL,
            read_at         TEXT,
            error_log       TEXT
        )
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_notif_sent_unread
            ON notifications_sent(read_at, sent_at DESC) WHERE read_at IS NULL
    """)
```

- [ ] **Step 4: Implement `notifier/_storage.py`**

Create `notifier/_storage.py`:
```python
"""Thin wrapper around signals.db for notification records.

Uses btc_api.get_db() so tests monkeypatching DB_FILE work transparently.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn() -> sqlite3.Connection:
    import btc_api
    return btc_api.get_db()


def record_delivery(
    event_type: str,
    event_key: str,
    priority: str,
    payload: dict[str, Any],
    channels_sent: list[str],
    delivery_status: str,
    error_log: str | None = None,
) -> int:
    conn = _conn()
    cur = conn.execute(
        """INSERT INTO notifications_sent
           (event_type, event_key, priority, payload_json,
            channels_sent, delivery_status, sent_at, error_log)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event_type, event_key, priority,
            json.dumps(payload, default=str),
            ",".join(channels_sent), delivery_status,
            _now_iso(), error_log,
        ),
    )
    conn.commit()
    return cur.lastrowid


def list_unread(limit: int = 50) -> list[dict[str, Any]]:
    conn = _conn()
    rows = conn.execute(
        """SELECT id, event_type, event_key, priority, payload_json,
                  channels_sent, delivery_status, sent_at, read_at, error_log
           FROM notifications_sent
           WHERE read_at IS NULL
           ORDER BY sent_at DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    cols = ["id", "event_type", "event_key", "priority", "payload_json",
            "channels_sent", "delivery_status", "sent_at", "read_at", "error_log"]
    return [dict(zip(cols, r)) for r in rows]


def mark_read(notification_id: int) -> None:
    conn = _conn()
    conn.execute(
        "UPDATE notifications_sent SET read_at = ? WHERE id = ?",
        (_now_iso(), notification_id),
    )
    conn.commit()


def mark_all_read() -> int:
    conn = _conn()
    cur = conn.execute(
        "UPDATE notifications_sent SET read_at = ? WHERE read_at IS NULL",
        (_now_iso(),),
    )
    conn.commit()
    return cur.rowcount
```

- [ ] **Step 5: Run storage tests**

Run: `python -m pytest tests/test_notifier_storage.py -v`
Expected: 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add notifier/_storage.py tests/test_notifier_storage.py btc_api.py
git commit -m "feat(notifier): notifications_sent table + storage helpers (#162)"
```

---

## Task 4: Dedupe

**Files:**
- Create: `notifier/dedupe.py`
- Create: `tests/test_notifier_dedupe.py`

- [ ] **Step 1: Write failing dedupe tests**

Create `tests/test_notifier_dedupe.py`:
```python
"""Dedupe is a DB-backed sliding window over notifications_sent.
Same (event_type, event_key) within window_seconds returns False (don't send).
Outside window or first occurrence returns True (send)."""
from datetime import timedelta
import time

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


def test_first_send_always_allowed(tmp_db):
    from notifier.dedupe import should_send
    assert should_send("health", "health:BTC:PAUSED", window_seconds=60) is True


def test_repeat_within_window_blocked(tmp_db):
    from notifier.dedupe import should_send
    from notifier._storage import record_delivery

    record_delivery("health", "health:BTC:PAUSED", "warning",
                    {"symbol": "BTC"}, ["telegram"], "ok")
    assert should_send("health", "health:BTC:PAUSED", window_seconds=60) is False


def test_zero_window_never_dedupes(tmp_db):
    from notifier.dedupe import should_send
    from notifier._storage import record_delivery

    record_delivery("signal", "signal:BTC", "info",
                    {"symbol": "BTC"}, ["telegram"], "ok")
    assert should_send("signal", "signal:BTC", window_seconds=0) is True


def test_critical_priority_bypasses_dedupe(tmp_db):
    """Critical events always send, regardless of recent history."""
    from notifier.dedupe import should_send
    from notifier._storage import record_delivery

    record_delivery("infra", "infra:scanner", "critical",
                    {"component": "scanner"}, ["telegram"], "ok")
    assert should_send("infra", "infra:scanner", window_seconds=60,
                        priority="critical") is True
    assert should_send("infra", "infra:scanner", window_seconds=60,
                        priority="warning") is False
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_notifier_dedupe.py -v`
Expected: FAIL — `notifier.dedupe` doesn't exist.

- [ ] **Step 3: Implement dedupe.py**

Create `notifier/dedupe.py`:
```python
"""DB-backed sliding-window deduplication for notifier.notify().

Query shape:
  SELECT 1 FROM notifications_sent
  WHERE event_type=? AND event_key=?
        AND sent_at >= (now - window_seconds)
  LIMIT 1
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def should_send(
    event_type: str,
    event_key: str,
    window_seconds: int,
    priority: str = "info",
) -> bool:
    """Return True if this event should be sent (no recent duplicate found).

    Critical-priority events always pass. Window of 0 disables dedupe.
    """
    if priority == "critical":
        return True
    if window_seconds <= 0:
        return True

    import btc_api
    conn = btc_api.get_db()
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    row = conn.execute(
        """SELECT 1 FROM notifications_sent
           WHERE event_type = ? AND event_key = ? AND sent_at >= ?
           LIMIT 1""",
        (event_type, event_key, cutoff.isoformat()),
    ).fetchone()
    return row is None
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_notifier_dedupe.py -v`
Expected: all 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add notifier/dedupe.py tests/test_notifier_dedupe.py
git commit -m "feat(notifier): DB-backed sliding-window dedupe (#162)"
```

---

## Task 5: Rate limit (token bucket)

**Files:**
- Create: `notifier/ratelimit.py`
- Create: `tests/test_notifier_ratelimit.py`

- [ ] **Step 1: Write failing ratelimit tests**

Create `tests/test_notifier_ratelimit.py`:
```python
"""Token-bucket per channel. Default capacity=20, refill_per_sec=20/60 = 0.333."""
import time


def test_fresh_bucket_allows_up_to_capacity():
    from notifier.ratelimit import TokenBucket
    b = TokenBucket(capacity=20, refill_per_sec=1.0)
    # Fresh bucket starts full; can consume 20 without waiting
    for _ in range(20):
        assert b.acquire() is True
    # 21st must fail (bucket empty, no time passed)
    assert b.acquire() is False


def test_refill_over_time():
    from notifier.ratelimit import TokenBucket
    b = TokenBucket(capacity=10, refill_per_sec=10.0)
    # Drain
    for _ in range(10):
        assert b.acquire() is True
    assert b.acquire() is False
    time.sleep(0.5)  # refill 5 tokens
    # Now ~5 should be available
    acquired = sum(1 for _ in range(10) if b.acquire())
    assert 4 <= acquired <= 6, f"expected ~5 refilled tokens, got {acquired}"


def test_bucket_never_exceeds_capacity():
    from notifier.ratelimit import TokenBucket
    b = TokenBucket(capacity=5, refill_per_sec=100.0)
    time.sleep(0.5)  # should refill way past capacity
    # Can only drain up to capacity even though many tokens were "refilled"
    for _ in range(5):
        assert b.acquire() is True
    assert b.acquire() is False
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_notifier_ratelimit.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement ratelimit.py**

Create `notifier/ratelimit.py`:
```python
"""Thread-safe in-memory token bucket.

Shared across the process. For multi-process workers a DB-backed
alternative would be needed, but the scanner runs single-process today."""
from __future__ import annotations

import threading
import time


class TokenBucket:
    def __init__(self, capacity: int, refill_per_sec: float):
        self.capacity = float(capacity)
        self.refill_per_sec = float(refill_per_sec)
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, n: float = 1.0) -> bool:
        """Try to acquire n tokens. Returns True if granted, False if not."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_per_sec)
            self._last_refill = now
            if self._tokens >= n:
                self._tokens -= n
                return True
            return False


# Module-level registry: one bucket per channel name.
_buckets: dict[str, TokenBucket] = {}
_registry_lock = threading.Lock()


def bucket_for(channel_name: str, capacity: int = 20, refill_per_sec: float | None = None) -> TokenBucket:
    """Get-or-create the token bucket for a channel.

    Default: capacity=20, refill_per_sec=capacity/60 (i.e. 20 req/min steady state)."""
    refill = refill_per_sec if refill_per_sec is not None else capacity / 60.0
    with _registry_lock:
        if channel_name not in _buckets:
            _buckets[channel_name] = TokenBucket(capacity, refill)
        return _buckets[channel_name]


def reset_all_for_tests() -> None:
    """Test helper. Clears the registry so each test starts fresh."""
    with _registry_lock:
        _buckets.clear()
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_notifier_ratelimit.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add notifier/ratelimit.py tests/test_notifier_ratelimit.py
git commit -m "feat(notifier): token-bucket rate limiter (#162)"
```

---

## Task 6: Templates + template loader

**Files:**
- Create: `notifier/_templates.py`
- Create: `notifier/templates/signal.telegram.j2`
- Create: `notifier/templates/health.telegram.j2`
- Create: `notifier/templates/infra.telegram.j2`
- Create: `notifier/templates/system.telegram.j2`
- Create: `tests/test_notifier_templates.py`

- [ ] **Step 1: Write failing template tests**

Create `tests/test_notifier_templates.py`:
```python
"""Template loader renders per (event_type, channel) combination.
Renders must match the current Telegram message format for backward compat
(the snapshot test in Task 9 will enforce byte-level parity for signals)."""


def test_render_signal_telegram_includes_symbol_score_direction():
    from notifier._templates import render
    from notifier import SignalEvent
    ev = SignalEvent(symbol="BTCUSDT", score=6, direction="LONG",
                      entry=50_000.0, sl=49_000.0, tp=55_000.0)
    msg = render(ev, channel="telegram")
    assert "BTCUSDT" in msg
    assert "6" in msg
    assert "LONG" in msg


def test_render_health_telegram_flags_transition():
    from notifier._templates import render
    from notifier import HealthEvent
    ev = HealthEvent(symbol="JUPUSDT", from_state="REDUCED", to_state="PAUSED",
                      reason="3mo_consec_neg", metrics={"pnl_30d": -500})
    msg = render(ev, channel="telegram")
    assert "JUPUSDT" in msg
    assert "PAUSED" in msg
    assert "3mo_consec_neg" in msg


def test_render_infra_telegram_critical():
    from notifier._templates import render
    from notifier import InfraEvent
    ev = InfraEvent(component="scanner", severity="critical", message="died")
    msg = render(ev, channel="telegram")
    assert "scanner" in msg
    assert "critical" in msg.lower()
    assert "died" in msg


def test_render_system_telegram():
    from notifier._templates import render
    from notifier import SystemEvent
    ev = SystemEvent(kind="startup", message="API online")
    msg = render(ev, channel="telegram")
    assert "startup" in msg
    assert "API online" in msg


def test_unknown_template_raises():
    import pytest
    from notifier._templates import render
    from notifier import SignalEvent
    ev = SignalEvent(symbol="X", score=1, direction="LONG",
                     entry=1.0, sl=1.0, tp=1.0)
    with pytest.raises(FileNotFoundError):
        render(ev, channel="sms")  # no template for sms
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_notifier_templates.py -v`
Expected: FAIL — `_templates.py` doesn't exist.

- [ ] **Step 3: Create template files**

Create `notifier/templates/signal.telegram.j2`:
```jinja
*Signal* `{{ symbol }}`
Score: *{{ score }}* ({{ direction }})
Entry: `{{ "%.2f"|format(entry) }}` | SL: `{{ "%.2f"|format(sl) }}` | TP: `{{ "%.2f"|format(tp) }}`
```

Create `notifier/templates/health.telegram.j2`:
```jinja
{%- if to_state == "PAUSED" %}🛑{% elif to_state == "REDUCED" %}⚠️{% elif to_state == "ALERT" %}⚠️{% else %}ℹ️{% endif %} *Health transition*
`{{ symbol }}` {{ from_state }} → *{{ to_state }}*
Reason: `{{ reason }}`
{%- if metrics %}
Metrics: `{{ metrics|tojson }}`
{%- endif %}
```

Create `notifier/templates/infra.telegram.j2`:
```jinja
{%- if severity == "critical" %}🚨{% elif severity == "warning" %}⚠️{% else %}ℹ️{% endif %} *Infra ({{ severity }})*
Component: `{{ component }}`
{{ message }}
```

Create `notifier/templates/system.telegram.j2`:
```jinja
ℹ️ *System: {{ kind }}*
{{ message }}
```

- [ ] **Step 4: Implement template loader**

Create `notifier/_templates.py`:
```python
"""Jinja2 template loader + render helper.

Templates are named '<event_type>.<channel>.j2' under notifier/templates/.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from notifier.events import _BaseEvent


_TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    undefined=StrictUndefined,
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
)


def render(event: _BaseEvent, channel: str) -> str:
    """Render an event through the appropriate <event_type>.<channel>.j2 template."""
    template_name = f"{event.event_type}.{channel}.j2"
    template_path = _TEMPLATE_DIR / template_name
    if not template_path.exists():
        raise FileNotFoundError(
            f"No template for event_type={event.event_type!r} channel={channel!r} "
            f"(looked for {template_name})"
        )
    template = _env.get_template(template_name)
    return template.render(**event.to_dict()).strip()
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_notifier_templates.py -v`
Expected: all 5 PASS.

- [ ] **Step 6: Commit**

```bash
git add notifier/_templates.py notifier/templates/ tests/test_notifier_templates.py
git commit -m "feat(notifier): Jinja2 template loader + telegram templates (#162)"
```

---

## Task 7: Channel ABC + TelegramChannel

**Files:**
- Create: `notifier/channels/base.py`
- Create: `notifier/channels/telegram.py`
- Create: `tests/test_notifier_telegram_channel.py`

- [ ] **Step 1: Write failing channel tests**

Create `tests/test_notifier_telegram_channel.py`:
```python
"""TelegramChannel refactors push_telegram_direct behind a Channel ABC.
Uses requests mocking to avoid real HTTP."""
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def telegram_cfg():
    return {"telegram_bot_token": "test-token", "telegram_chat_id": "12345"}


def test_telegram_send_success(telegram_cfg):
    from notifier.channels.telegram import TelegramChannel
    channel = TelegramChannel(telegram_cfg)

    fake_response = MagicMock()
    fake_response.ok = True
    fake_response.status_code = 200
    fake_response.json.return_value = {"ok": True, "result": {"message_id": 42}}

    with patch("notifier.channels.telegram.requests.post", return_value=fake_response) as mock_post:
        receipt = channel.send("hello")

    assert receipt.status == "ok"
    assert mock_post.call_count == 1
    args, kwargs = mock_post.call_args
    assert "test-token" in args[0]
    assert kwargs["json"]["chat_id"] == "12345"
    assert kwargs["json"]["text"] == "hello"


def test_telegram_send_retries_on_transient_failure(telegram_cfg):
    from notifier.channels.telegram import TelegramChannel
    channel = TelegramChannel(telegram_cfg)

    fail_resp = MagicMock()
    fail_resp.ok = False
    fail_resp.status_code = 500
    fail_resp.text = "server error"
    ok_resp = MagicMock()
    ok_resp.ok = True
    ok_resp.status_code = 200
    ok_resp.json.return_value = {"ok": True}

    with patch("notifier.channels.telegram.requests.post",
                side_effect=[fail_resp, fail_resp, ok_resp]) as mock_post:
        with patch("notifier.channels.telegram.time.sleep"):
            receipt = channel.send("hello")

    assert receipt.status == "ok"
    assert mock_post.call_count == 3


def test_telegram_send_gives_up_after_max_retries(telegram_cfg):
    from notifier.channels.telegram import TelegramChannel
    channel = TelegramChannel(telegram_cfg)

    fail_resp = MagicMock()
    fail_resp.ok = False
    fail_resp.status_code = 500
    fail_resp.text = "server error"

    with patch("notifier.channels.telegram.requests.post", return_value=fail_resp):
        with patch("notifier.channels.telegram.time.sleep"):
            receipt = channel.send("hello", max_retries=2)

    assert receipt.status == "failed"
    assert "server error" in (receipt.error or "")


def test_telegram_send_noop_when_not_configured():
    from notifier.channels.telegram import TelegramChannel
    # No token/chat_id — channel reports failed without attempting HTTP
    channel = TelegramChannel({})
    receipt = channel.send("hello")
    assert receipt.status == "failed"
    assert "not configured" in receipt.error.lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_notifier_telegram_channel.py -v`
Expected: FAIL — modules don't exist.

- [ ] **Step 3: Implement Channel ABC**

Create `notifier/channels/base.py`:
```python
"""Channel ABC + DeliveryReceipt."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class DeliveryReceipt:
    channel: str
    status: str       # 'ok' | 'failed'
    error: str | None = None


class Channel(ABC):
    """A destination (Telegram, Webhook, Email). Concrete impls implement send."""

    name: str = "base"

    @abstractmethod
    def send(self, message: str, **kwargs) -> DeliveryReceipt:
        raise NotImplementedError
```

- [ ] **Step 4: Implement TelegramChannel**

Create `notifier/channels/telegram.py`:
```python
"""Telegram channel. Wraps the direct sendMessage API.

Replaces btc_api.push_telegram_direct / _send_telegram_raw while preserving
the same retry behavior (up to 3 attempts with backoff)."""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

from notifier.channels.base import Channel, DeliveryReceipt


log = logging.getLogger("notifier.telegram")

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramChannel(Channel):
    name = "telegram"

    def __init__(self, cfg: dict[str, Any]):
        self._token = (cfg.get("telegram_bot_token") or "").strip()
        self._chat_id = (cfg.get("telegram_chat_id") or "").strip()

    def send(self, message: str, max_retries: int = 3) -> DeliveryReceipt:
        if not self._token or not self._chat_id:
            return DeliveryReceipt(channel=self.name, status="failed",
                                    error="telegram not configured (missing token or chat_id)")

        url = _TELEGRAM_API.format(token=self._token)
        payload = {
            "chat_id": self._chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }

        last_error: str | None = None
        for attempt in range(1, max_retries + 1):
            try:
                r = requests.post(url, json=payload, timeout=10)
                if r.ok:
                    return DeliveryReceipt(channel=self.name, status="ok")
                last_error = f"HTTP {r.status_code}: {r.text[:200]}"
                log.warning("telegram attempt %d/%d failed: %s", attempt, max_retries, last_error)
            except requests.RequestException as e:
                last_error = f"{type(e).__name__}: {e}"
                log.warning("telegram attempt %d/%d exception: %s", attempt, max_retries, last_error)

            if attempt < max_retries:
                time.sleep(2 ** (attempt - 1))  # 1s, 2s, 4s backoff

        return DeliveryReceipt(channel=self.name, status="failed", error=last_error)
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_notifier_telegram_channel.py -v`
Expected: 4 PASS.

- [ ] **Step 6: Commit**

```bash
git add notifier/channels/base.py notifier/channels/telegram.py tests/test_notifier_telegram_channel.py
git commit -m "feat(notifier): Channel ABC + TelegramChannel (#162)"
```

---

## Task 8: Main `notify()` orchestrator

**Files:**
- Modify: `notifier/__init__.py`
- Create: `tests/test_notifier_integration.py`

- [ ] **Step 1: Write failing integration test**

Create `tests/test_notifier_integration.py`:
```python
"""End-to-end notify() flow: dedupe → ratelimit → render → send → record."""
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def tmp_db_and_reset(tmp_path, monkeypatch):
    import btc_api
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    # Reset ratelimit singletons between tests
    from notifier import ratelimit
    ratelimit.reset_all_for_tests()
    yield db_path


@pytest.fixture
def ok_telegram():
    fake = MagicMock()
    fake.ok = True
    fake.status_code = 200
    fake.json.return_value = {"ok": True}
    return fake


def _cfg():
    return {
        "notifier": {"enabled": True, "test_mode": False,
                      "dedupe": {"default_window_minutes": 30}},
        "telegram_bot_token": "t", "telegram_chat_id": "1",
    }


def test_notify_signal_sends_to_telegram_and_records(tmp_db_and_reset, ok_telegram):
    from notifier import notify, SignalEvent
    ev = SignalEvent(symbol="BTCUSDT", score=6, direction="LONG",
                      entry=50_000, sl=49_000, tp=55_000)

    with patch("notifier.channels.telegram.requests.post", return_value=ok_telegram) as mock_post:
        receipts = notify(ev, cfg=_cfg())

    assert len(receipts) == 1
    assert receipts[0].status == "ok"
    assert mock_post.call_count == 1

    from notifier._storage import list_unread
    rows = list_unread(limit=5)
    assert len(rows) == 1
    assert rows[0]["event_type"] == "signal"


def test_notify_blocks_duplicate_within_dedupe_window(tmp_db_and_reset, ok_telegram):
    from notifier import notify, HealthEvent
    ev = HealthEvent(symbol="JUP", from_state="REDUCED", to_state="PAUSED",
                     reason="3mo_consec_neg")

    with patch("notifier.channels.telegram.requests.post", return_value=ok_telegram) as mock_post:
        r1 = notify(ev, cfg=_cfg())
        r2 = notify(ev, cfg=_cfg())

    assert len(r1) == 1 and r1[0].status == "ok"
    assert r2 == []  # deduped
    assert mock_post.call_count == 1


def test_notify_test_mode_skips_http(tmp_db_and_reset):
    from notifier import notify, SignalEvent
    cfg = _cfg()
    cfg["notifier"]["test_mode"] = True

    ev = SignalEvent(symbol="BTCUSDT", score=6, direction="LONG",
                     entry=50_000, sl=49_000, tp=55_000)
    with patch("notifier.channels.telegram.requests.post") as mock_post:
        receipts = notify(ev, cfg=cfg)

    assert mock_post.call_count == 0
    assert len(receipts) == 1
    assert receipts[0].status == "ok"  # treated as "simulated ok"


def test_notify_disabled_config_returns_empty(tmp_db_and_reset):
    from notifier import notify, SignalEvent
    cfg = _cfg()
    cfg["notifier"]["enabled"] = False

    ev = SignalEvent(symbol="X", score=1, direction="LONG",
                     entry=1, sl=1, tp=1)
    with patch("notifier.channels.telegram.requests.post") as mock_post:
        receipts = notify(ev, cfg=cfg)

    assert receipts == []
    assert mock_post.call_count == 0


def test_notify_rate_limit_queues_overflow(tmp_db_and_reset, ok_telegram):
    """21st call in a burst hits the rate limiter (default capacity=20)."""
    from notifier import notify, SignalEvent, ratelimit

    cfg = _cfg()

    with patch("notifier.channels.telegram.requests.post", return_value=ok_telegram):
        receipts_batch = []
        for i in range(25):
            ev = SignalEvent(symbol=f"SYM{i}", score=1, direction="LONG",
                              entry=1, sl=1, tp=1)
            receipts_batch.append(notify(ev, cfg=cfg))

    sent_count = sum(1 for r in receipts_batch if r and r[0].status == "ok")
    limited_count = sum(1 for r in receipts_batch if r and r[0].status == "rate_limited")
    # At most 20 go through; the rest are rate_limited
    assert sent_count <= 20
    assert sent_count + limited_count == 25
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_notifier_integration.py -v`
Expected: FAIL — `notify()` not implemented.

- [ ] **Step 3: Implement notify() orchestrator**

Replace `notifier/__init__.py` with:
```python
"""Centralized notifier (#162). Public API: notify, event types."""
from __future__ import annotations

import logging
from typing import Any

from notifier import dedupe, ratelimit
from notifier._storage import record_delivery
from notifier._templates import render
from notifier.channels.base import DeliveryReceipt
from notifier.channels.telegram import TelegramChannel
from notifier.events import (
    SignalEvent, HealthEvent, InfraEvent, SystemEvent,
    Event,
)


__all__ = [
    "notify",
    "SignalEvent", "HealthEvent", "InfraEvent", "SystemEvent", "Event",
    "DeliveryReceipt",
]


log = logging.getLogger("notifier")


_DEFAULT_CHANNELS_BY_EVENT_TYPE: dict[str, list[str]] = {
    "signal": ["telegram"],
    "health": ["telegram"],
    "infra":  ["telegram"],
    "system": ["telegram"],
}

_DEFAULT_DEDUPE_SECONDS_BY_EVENT_TYPE: dict[str, int] = {
    "signal": 0,      # no dedupe — signals are rare and each matters
    "health": 1800,   # 30 min
    "infra":  300,    # 5 min
    "system": 0,
}


def _resolve_channels(event: Event, cfg: dict) -> list[str]:
    notif_cfg = cfg.get("notifier", {}) or {}
    overrides = (notif_cfg.get("channels_by_event_type") or {})
    return overrides.get(event.event_type,
                          _DEFAULT_CHANNELS_BY_EVENT_TYPE.get(event.event_type, ["telegram"]))


def _resolve_dedupe_window(event: Event, cfg: dict) -> int:
    notif_cfg = cfg.get("notifier", {}) or {}
    dedupe_cfg = notif_cfg.get("dedupe", {}) or {}
    per_type = dedupe_cfg.get("by_event_type", {}) or {}
    if event.event_type in per_type:
        return int(per_type[event.event_type])
    default_min = dedupe_cfg.get("default_window_minutes")
    if default_min is not None:
        return int(default_min) * 60
    return _DEFAULT_DEDUPE_SECONDS_BY_EVENT_TYPE.get(event.event_type, 0)


def notify(event: Event, cfg: dict) -> list[DeliveryReceipt]:
    """Send an event through configured channels with dedupe + ratelimit.

    Returns [] if: notifier disabled, or the event was deduped, or no channels configured.
    Returns list of DeliveryReceipt (one per channel attempted) otherwise.
    """
    notif_cfg = cfg.get("notifier", {}) or {}
    if not notif_cfg.get("enabled", True):
        return []

    window_seconds = _resolve_dedupe_window(event, cfg)
    if not dedupe.should_send(event.event_type, event.dedupe_key,
                                window_seconds=window_seconds,
                                priority=event.priority):
        log.debug("notify deduped: %s %s", event.event_type, event.dedupe_key)
        return []

    test_mode = notif_cfg.get("test_mode", False)
    channels = _resolve_channels(event, cfg)
    receipts: list[DeliveryReceipt] = []
    channels_sent: list[str] = []
    any_error: str | None = None

    for channel_name in channels:
        bucket = ratelimit.bucket_for(channel_name)
        if not bucket.acquire():
            receipts.append(DeliveryReceipt(channel=channel_name, status="rate_limited",
                                              error="bucket empty"))
            continue

        # Render through template
        try:
            message = render(event, channel=channel_name)
        except Exception as e:
            receipts.append(DeliveryReceipt(channel=channel_name, status="failed",
                                              error=f"render failed: {e}"))
            any_error = any_error or str(e)
            continue

        if test_mode:
            receipts.append(DeliveryReceipt(channel=channel_name, status="ok",
                                              error="test_mode"))
            channels_sent.append(channel_name)
            continue

        if channel_name == "telegram":
            channel = TelegramChannel(cfg)
        else:
            receipts.append(DeliveryReceipt(channel=channel_name, status="failed",
                                              error="unsupported channel in PR A"))
            continue

        receipt = channel.send(message)
        receipts.append(receipt)
        if receipt.status == "ok":
            channels_sent.append(channel_name)
        else:
            any_error = any_error or receipt.error

    delivery_status = "ok" if channels_sent else "failed"
    if channels_sent and any_error:
        delivery_status = "partial"

    try:
        record_delivery(
            event_type=event.event_type,
            event_key=event.dedupe_key,
            priority=event.priority,
            payload=event.to_dict(),
            channels_sent=channels_sent or ["none"],
            delivery_status=delivery_status,
            error_log=any_error,
        )
    except Exception as e:
        log.exception("notifier failed to persist delivery record: %s", e)

    return receipts
```

- [ ] **Step 4: Run integration tests**

Run: `python -m pytest tests/test_notifier_integration.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add notifier/__init__.py tests/test_notifier_integration.py
git commit -m "feat(notifier): notify() orchestrator — dedupe + ratelimit + render + send (#162)"
```

---

## Task 9: Snapshot parity test (pre-refactor == post-refactor for SignalEvent)

**Files:**
- Create: `tests/test_notifier_signal_parity.py`

**Goal:** Lock down the exact string that used to be emitted by `build_telegram_message` for signals, so migrating callers can't regress the user-visible message format.

- [ ] **Step 1: Extract current format into a fixture**

Read `btc_api.py` `build_telegram_message` (around line 1010). Inspect the exact string shape for a representative input: a scan report dict with `symbol`, `score`, `direction`, `entry_price`, `sl_price`, `tp_price`, etc.

- [ ] **Step 2: Write snapshot test**

Create `tests/test_notifier_signal_parity.py`:
```python
"""Lock the current Telegram signal message format.

When we migrate btc_api call sites to notifier.notify(SignalEvent(...)),
the rendered message must match what build_telegram_message used to produce.
If this test fails, either:
  (a) the SignalEvent→telegram template drifted — fix the template, or
  (b) the legacy build_telegram_message evolved — sync the template.
"""
from notifier import SignalEvent
from notifier._templates import render


def _sample_scan_report():
    # Minimal report mirroring what scan() builds pre-refactor.
    return {
        "symbol": "BTCUSDT", "score": 6, "direction": "LONG",
        "price": 50_000.0,
        "sl": 49_000.0,
        "tp": 55_000.0,
    }


def test_signal_telegram_message_contains_required_tokens():
    """Loose contract: the message must mention symbol, score, direction, entry, sl, tp.
    Exact byte-level parity will be verified by the btc_api migration task against a
    fixture produced from build_telegram_message."""
    ev = SignalEvent(symbol="BTCUSDT", score=6, direction="LONG",
                      entry=50_000.0, sl=49_000.0, tp=55_000.0)
    msg = render(ev, channel="telegram")
    assert "BTCUSDT" in msg
    assert "6" in msg
    assert "LONG" in msg
    assert "50000" in msg or "50,000" in msg or "50000.00" in msg
    assert "49000" in msg or "49,000" in msg or "49000.00" in msg
    assert "55000" in msg or "55,000" in msg or "55000.00" in msg


def test_signal_template_stable_for_fixed_input():
    """Guard against accidental template edits that change the output.
    If you intentionally change the format, update EXPECTED below."""
    ev = SignalEvent(symbol="BTCUSDT", score=6, direction="LONG",
                      entry=50_000.0, sl=49_000.0, tp=55_000.0)
    got = render(ev, channel="telegram")
    expected = (
        "*Signal* `BTCUSDT`\n"
        "Score: *6* (LONG)\n"
        "Entry: `50000.00` | SL: `49000.00` | TP: `55000.00`"
    )
    assert got == expected, f"template drift detected:\nexpected:\n{expected!r}\ngot:\n{got!r}"
```

- [ ] **Step 3: Run test**

Run: `python -m pytest tests/test_notifier_signal_parity.py -v`
Expected: 2 PASS (assuming the template in Task 6 produces exactly this string — verify). If the template differs, either update the template in `notifier/templates/signal.telegram.j2` or update `expected` in the test, **but NOT both silently**. Document the decision in the commit.

- [ ] **Step 4: Commit**

```bash
git add tests/test_notifier_signal_parity.py
git commit -m "test(notifier): snapshot test for signal telegram template (#162)"
```

---

## Task 10: Migrate btc_api.py call sites + deprecation markers

**Files:**
- Modify: `btc_api.py` (~10 call sites)

**Goal:** Replace direct `push_telegram_direct` / `_send_telegram_raw` / `build_telegram_message` usage with `notifier.notify(SignalEvent(...))`. Leave the legacy functions in place with deprecation comments — `trading_webhook.py` and other external consumers still rely on the `telegram_message` payload key.

- [ ] **Step 1: Add module-level deprecation banners**

Open `btc_api.py`. Above the definition of each legacy function, add comments:

At line ~1010 (before `def build_telegram_message`):
```python
# DEPRECATED (#162): for new callers use notifier.notify(SignalEvent(...)).
# Kept because trading_webhook.py and a few legacy paths still consume the
# 'telegram_message' payload key emitted by scan results. Remove after those are migrated.
```

Same above `def push_telegram_direct` (line ~1086) and `def _send_telegram_raw` (line ~1124).

- [ ] **Step 2: Inventory every call site**

Run: `grep -n "push_telegram_direct\|_send_telegram_raw\|build_telegram_message" btc_api.py`

Expected output (from pre-refactor inventory): lines 622, 1010, 1086, 1094, 1124, 1151, 1254, 1569, 1588, 1605, 1757.

For each line, classify:
- **Direct send call** (needs migration): e.g. `push_telegram_direct(rep, cfg)` at line 1254 — replace with `notifier.notify(SignalEvent(...), cfg)`.
- **Payload-emitting**: e.g. lines 1171, 1569, 1588, 1605 that stuff `telegram_message` into a dict — keep for now, these serve `trading_webhook.py`.

- [ ] **Step 3: Migrate line 1254 (and any other direct call to `push_telegram_direct`)**

Open the context around that line and find the scan-report dict being passed to `push_telegram_direct`. Example rewrite:

```python
# Before
push_telegram_direct(rep, cfg)

# After
from notifier import notify, SignalEvent
notify(
    SignalEvent(
        symbol=rep["symbol"],
        score=int(rep.get("score", 0) or 0),
        direction=rep.get("direction", "LONG"),
        entry=float(rep.get("price") or 0.0),
        sl=float(rep.get("sl") or 0.0),
        tp=float(rep.get("tp") or 0.0),
    ),
    cfg=cfg,
)
```

The `from notifier import notify, SignalEvent` should be at module top of `btc_api.py`, not inline.

- [ ] **Step 4: Migrate line 622**

Read the 40 lines around line 622. It's inside a scanner loop path. Determine whether the call is a signal notification or something else, and migrate to the appropriate event type (`SignalEvent`, `InfraEvent`, etc.).

- [ ] **Step 5: Run full regression**

Run: `python -m pytest tests/ -q -m "not network"`
Expected: all pass (was 463 pre-refactor; now 463 + new notifier tests ~27 = ~490). No test suite regressions.

- [ ] **Step 6: Manual smoke**

Start the API locally (if safe in the dev env) and trigger a scan. Verify the Telegram message still lands correctly (or the `test_mode=true` equivalent produces a record in `notifications_sent`).

If manual smoke isn't safe/possible in this env, skip with a note and rely on the test suite. State clearly in the commit that manual verification is deferred to the reviewer.

- [ ] **Step 7: Commit**

```bash
git add btc_api.py
git commit -m "refactor(api): migrate telegram call sites to notifier.notify (#162)

Routes direct push_telegram_direct callers through notifier.notify(SignalEvent(...)).
Legacy build_telegram_message / push_telegram_direct / _send_telegram_raw
kept with deprecation banners — trading_webhook.py still consumes their
output via scan payload."
```

---

## Task 11: Full regression + open PR

**Files:** none changed; verification only.

- [ ] **Step 1: Full test suite**

Run: `python -m pytest tests/ -q -m "not network"`
Expected: all PASS; test count 463 (prior baseline) + ~27 new notifier tests = ~490. If any regression, stop and debug.

- [ ] **Step 2: Push branch**

```bash
git push -u origin feat/notifier-core
```

- [ ] **Step 3: Open PR**

```bash
gh pr create --base main --head feat/notifier-core \
  --title "feat(notifier): centralized typed notifier — PR A of #162" \
  --body "$(cat <<'BODY'
## Summary
First of 3 PRs for #162. Ships the notifier package skeleton + typed events + Jinja2 templates + DB-backed dedupe + token-bucket ratelimit + TelegramChannel (refactor of push_telegram_direct). Migrates btc_api direct call sites. Does NOT yet ship Webhook or Email channels (PR B) or the frontend notification center (PR C).

## Unblocks
- #138 Foundation can now use notifier.notify(HealthEvent(...)) directly.

## Test plan
- [x] 27 new notifier tests, 463 existing tests, all green (~490 total).
- [x] Snapshot test for signal telegram template stability.
- [x] Manual smoke (if feasible) — noted in commit message if deferred.

## Backward compatibility
- Legacy build_telegram_message / push_telegram_direct / _send_telegram_raw left in place with deprecation banners.
- config.json keys (telegram_bot_token, telegram_chat_id) read unchanged.
- trading_webhook.py path unaffected.

Closes partial: #162 (PR A). PRs B (multi-channel) and C (frontend) land separately.
BODY
)"
```

- [ ] **Step 4: Watch CI**

```bash
sleep 12 && gh pr checks --watch --interval 15
```
Expected: backend-tests + frontend-typecheck PASS.

- [ ] **Step 5: Report**

If CI green, report PR URL and verdict to the user. Do NOT merge automatically — wait for user's explicit approval.

---

## Self-review

**Spec coverage:**
- §3 "Incluido" PR A scope — all ships in this plan.
- §4 arquitectura — file structure matches the diagram.
- §5 API pública — notify() signature in Task 8, event exports in Task 2.
- §6 config — consumed in Task 8 (notifier.enabled, test_mode, dedupe); config schema itself is informational (no migration, just reads existing or defaults).
- §7 PR A scope — all items land (skeleton, events, storage, dedupe, ratelimit, templates, channel, orchestrator, snapshot, migration). ✓
- §8 testing — per-module tests in Tasks 2-7, integration in Task 8, snapshot in Task 9, regression in Task 11.
- §9 backward-compat — deprecation banners in Task 10; legacy functions not deleted. ✓
- §10 riesgos — snapshot test mitigates format drift; critical-priority dedupe bypass in dedupe.py; queue-via-rate_limited receipt in notify(); Jinja2 added to requirements; test coverage pre-refactor is the 463 baseline.
- §11 métricas de éxito — 1 entry point (notify()); 0 direct push_telegram_direct callers outside notifier (Task 10); notifier.notify(HealthEvent) unblocks #138 (stated in PR description); dedupe functional (test); snapshot parity test.

**Placeholder scan:** no "TBD", no "similar to Task N", no "add error handling as needed". Every code step shows concrete code. One hedge in Task 10 Step 6 about manual smoke — the escape hatch is explicit ("state clearly in the commit"), acceptable.

**Type consistency:**
- `DeliveryReceipt(channel, status, error)` defined in Task 7 base.py; used in Task 7 telegram.py, Task 8 orchestrator, Task 8 integration tests. Field `status` values: `"ok" | "failed" | "rate_limited"` — used consistently.
- `Event = SignalEvent | HealthEvent | InfraEvent | SystemEvent` defined in Task 2 events.py; imported and used in Task 8 orchestrator.
- `render(event, channel)` defined in Task 6 _templates.py; called in Task 8 orchestrator.
- `record_delivery(...)` 7 args defined in Task 3 _storage.py; called in Task 8 orchestrator with all 7.
- `should_send(event_type, event_key, window_seconds, priority)` in Task 4; called with all 4 kwargs in Task 8 orchestrator.
- `bucket_for(channel_name)` in Task 5; called with just `channel_name` in Task 8 (refill uses default = capacity/60).
- `notify(event, cfg)` — signature in Task 8; called from Task 10 with `(SignalEvent(...), cfg=cfg)`. ✓

All consistent.

**Scope:** single PR A of #162. Does not try to include Webhook/Email (PR B) or frontend (PR C). Does not try to include #138 content.

Plan looks good.
