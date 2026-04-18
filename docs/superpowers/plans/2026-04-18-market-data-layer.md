# Market Data Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a unified OHLCV cache + fetch layer (`data/` package) that becomes the single source of truth for market data across all modules. Migrate scanner, backtest, API, and ad-hoc scripts to consume it. Ship issue #125 (volatility-normalized position sizing) as the first feature consumer.

**Architecture:** SQLite (`ohlcv.db`, WAL mode, thread-local conns) as storage. Module-level public API in `data/market_data.py` (6 functions + utilities). Stale-on-read freshness — no scheduler. Cooperative concurrency: in-process lock registry dedups; cross-process relies on `INSERT OR REPLACE` idempotency. Provider adapter protocol (Binance primary, Bybit fallback) with sticky failover + recovery probe. Metrics exposed via `/status`.

**Tech Stack:** Python 3.11, stdlib (sqlite3, threading, concurrent.futures), pandas, numpy, requests. Testing with pytest + monkeypatching (no new runtime or test dependencies). Integrates with existing CI at `.github/workflows/ci.yml`.

**Spec:** `docs/superpowers/specs/en/2026-04-18-market-data-layer-design.md`

---

## File Structure

```
NEW FILES (layer + tests):
  data/
    __init__.py                       # re-exports public API (~15 lines)
    market_data.py                    # 6 public functions (~250 lines)
    _storage.py                       # SQLite, thread-local, upserts (~300 lines)
    _fetcher.py                       # providers, failover, lock registry (~280 lines)
    _scheduler.py                     # empty placeholder (~10 lines)
    timeframes.py                     # TIMEFRAMES registry + utilities (~60 lines)
    metrics.py                        # thread-safe counters + get_stats (~130 lines)
    cli.py                            # python -m data.cli {backfill,repair,stats,init} (~100 lines)
    providers/
      __init__.py                     # empty (~1 line)
      base.py                         # Protocol, Bar, exceptions (~90 lines)
      binance.py                      # BinanceAdapter (~120 lines)
      bybit.py                        # BybitAdapter (~120 lines)

  tests/
    test_timeframes.py                # ~80 lines
    test_metrics.py                   # ~90 lines
    test_providers_base.py            # ~60 lines
    test_providers_binance.py         # ~180 lines
    test_providers_bybit.py           # ~180 lines
    test_storage.py                   # ~300 lines
    test_fetcher.py                   # ~350 lines
    test_market_data.py               # ~450 lines
    test_market_data_integration.py   # ~200 lines
    _fakes.py                         # FakeProvider + helpers (~120 lines)

MODIFIED FILES:
  tests/conftest.py                   # add tmp_ohlcv_db + fake_provider fixtures
  btc_scanner.py                      # consume data.market_data; remove internal fetch
  backtest.py                         # consume data.market_data for historical ranges
  auto_tune.py                        # consume data.market_data
  grid_search_tf.py                   # consume data.market_data
  btc_report.py                       # consume data.market_data
  optimize_new_tokens.py              # consume data.market_data
  btc_api.py                          # /ohlcv uses get_klines_live; /status exposes stats
  .gitignore                          # add data/ohlcv.db (if not already via data/ pattern)
```

---

## Preflight

Before Task 1, confirm environment:

```bash
python --version                    # 3.11.x expected
python -c "import pandas, numpy, requests, sqlite3; print('OK')"
ls docs/superpowers/specs/en/2026-04-18-market-data-layer-design.md
```

Read the spec first. Keep it open in a second pane while implementing.

---

## Phase 0 — Scaffolding

### Task 1: Create `data/` package skeleton and test fixtures

**Files:**
- Create: `data/__init__.py`, `data/market_data.py`, `data/_storage.py`, `data/_fetcher.py`, `data/_scheduler.py`, `data/timeframes.py`, `data/metrics.py`, `data/cli.py`
- Create: `data/providers/__init__.py`, `data/providers/base.py`, `data/providers/binance.py`, `data/providers/bybit.py`
- Create: `tests/_fakes.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Create the package structure with empty stub files**

```bash
mkdir -p data/providers tests
touch data/_scheduler.py data/providers/__init__.py
```

Write `data/__init__.py`:
```python
"""Market Data Layer — unified OHLCV cache + fetch for all modules.

Public API is defined in data.market_data. See
docs/superpowers/specs/en/2026-04-18-market-data-layer-design.md for design.
"""
```

Write `data/providers/__init__.py`:
```python
```
(intentionally empty)

Write `data/_scheduler.py`:
```python
"""Reserved for future WebSocket push mode. Intentionally empty in v1."""
```

- [ ] **Step 2: Add `.gitignore` entry for the runtime DB**

Append to `.gitignore` if not already covered:
```
data/ohlcv.db
data/ohlcv.db-wal
data/ohlcv.db-shm
```

Check first: `grep -q '^data/ohlcv' .gitignore || echo "needs add"`.

- [ ] **Step 3: Update `tests/conftest.py` with new fixtures**

Replace the contents of `tests/conftest.py`:
```python
"""Configuración compartida de pytest para el proyecto BTC Scanner."""
import sys
import os
from pathlib import Path

import pytest

# Asegurar que el directorio raíz esté en el path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture
def tmp_ohlcv_db(tmp_path, monkeypatch):
    """Isolated ohlcv.db per test. Points data._storage at a tmp file + fresh schema."""
    from data import _storage
    db_path = tmp_path / "ohlcv.db"
    monkeypatch.setattr(_storage, "DB_PATH", str(db_path))
    # Reset thread-local connection so each test gets a fresh one pointing at tmp_path
    if hasattr(_storage._tls, "conn"):
        _storage._tls.conn.close()
        del _storage._tls.conn
    _storage.init_schema()
    yield db_path
    if hasattr(_storage._tls, "conn"):
        _storage._tls.conn.close()
        del _storage._tls.conn


@pytest.fixture
def fake_provider(monkeypatch):
    """Inject a deterministic FakeProvider as the only provider in data._fetcher."""
    from data import _fetcher
    from tests._fakes import FakeProvider

    fake = FakeProvider()
    monkeypatch.setattr(_fetcher, "_PROVIDERS", [fake])
    # Reset failover state
    _fetcher._active_idx = 0
    _fetcher._consecutive_failures = 0
    _fetcher._last_probe_ms = 0
    return fake


@pytest.fixture
def fake_providers(monkeypatch):
    """Inject two fake providers to test failover."""
    from data import _fetcher
    from tests._fakes import FakeProvider

    primary = FakeProvider(name="primary")
    fallback = FakeProvider(name="fallback")
    monkeypatch.setattr(_fetcher, "_PROVIDERS", [primary, fallback])
    _fetcher._active_idx = 0
    _fetcher._consecutive_failures = 0
    _fetcher._last_probe_ms = 0
    return primary, fallback
```

- [ ] **Step 4: Create `tests/_fakes.py`**

```python
"""Test doubles: FakeProvider implements ProviderAdapter deterministically."""
import time
from typing import Any


class FakeProvider:
    """Deterministic provider for tests. Records calls. Responds from pre-seeded data."""

    def __init__(self, name: str = "fake"):
        self.name = name
        self.rate_limit_per_min = 100_000
        self.calls: list[tuple[str, str, int, int]] = []
        self.bars_by_key: dict[tuple[str, str], list] = {}
        self.raise_by_key: dict[tuple[str, str], Exception] = {}
        self.healthy: bool = True

    def set_bars(self, symbol: str, timeframe: str, bars: list):
        """Seed with a list of Bar instances ordered by open_time ascending."""
        self.bars_by_key[(symbol, timeframe)] = bars

    def set_error(self, symbol: str, timeframe: str, exc: Exception):
        self.raise_by_key[(symbol, timeframe)] = exc

    def clear_errors(self):
        self.raise_by_key.clear()

    def fetch_klines(self, symbol: str, timeframe: str, start_ms: int, end_ms: int):
        self.calls.append((symbol, timeframe, start_ms, end_ms))
        if (symbol, timeframe) in self.raise_by_key:
            raise self.raise_by_key[(symbol, timeframe)]
        all_bars = self.bars_by_key.get((symbol, timeframe), [])
        return [b for b in all_bars if start_ms <= b.open_time <= end_ms]

    def is_healthy(self) -> bool:
        return self.healthy


def make_bar(symbol: str, timeframe: str, open_time: int, price: float = 100.0, **overrides):
    """Factory for test Bar instances. Imports locally so tests can run before Bar exists."""
    from data.providers.base import Bar
    defaults = dict(
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time,
        open=price,
        high=price * 1.01,
        low=price * 0.99,
        close=price,
        volume=1000.0,
        provider="fake",
        fetched_at=int(time.time() * 1000),
    )
    defaults.update(overrides)
    return Bar(**defaults)
```

- [ ] **Step 5: Run existing tests to confirm nothing broke**

Run: `python -m pytest tests/ -x --tb=short -q`
Expected: all existing tests pass (no `data/` tests exist yet, so nothing from the new package is collected).

- [ ] **Step 6: Commit**

```bash
git add data/ tests/conftest.py tests/_fakes.py .gitignore
git commit -m "feat(data): scaffold market data layer package + test fixtures"
```

---

## Phase 1 — Foundation utilities (parallelizable with Phase 3)

### Task 2: `data/timeframes.py`

**Files:**
- Create: `data/timeframes.py`
- Create: `tests/test_timeframes.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_timeframes.py`:
```python
import pytest
from datetime import datetime, timezone
from data.timeframes import TIMEFRAMES, delta_ms, last_closed_bar_time


class TestTimeframeRegistry:
    def test_registered_timeframes(self):
        for tf in ["5m", "15m", "30m", "1h", "4h", "1d", "1w"]:
            assert tf in TIMEFRAMES
            assert TIMEFRAMES[tf] > 0

    def test_delta_ms_matches_registry(self):
        assert delta_ms("5m") == 5 * 60 * 1000
        assert delta_ms("1h") == 60 * 60 * 1000
        assert delta_ms("1d") == 24 * 60 * 60 * 1000

    def test_delta_ms_unknown_raises(self):
        with pytest.raises(KeyError):
            delta_ms("13m")


class TestLastClosedBarTime:
    def test_1h_middle_of_hour(self):
        # 14:30 → last closed 1h bar is 13:00
        t = datetime(2026, 4, 18, 14, 30, 0, tzinfo=timezone.utc)
        result = last_closed_bar_time("1h", t)
        expected = int(datetime(2026, 4, 18, 13, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        assert result == expected

    def test_1h_exactly_at_hour_boundary(self):
        # 14:00:00 exactly — the 14:00 bar has just opened but is NOT closed
        t = datetime(2026, 4, 18, 14, 0, 0, tzinfo=timezone.utc)
        result = last_closed_bar_time("1h", t)
        expected = int(datetime(2026, 4, 18, 13, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        assert result == expected

    def test_5m_middle_of_interval(self):
        t = datetime(2026, 4, 18, 14, 37, 0, tzinfo=timezone.utc)
        result = last_closed_bar_time("5m", t)
        # last closed 5m bar opened at 14:30
        expected = int(datetime(2026, 4, 18, 14, 30, 0, tzinfo=timezone.utc).timestamp() * 1000)
        assert result == expected

    def test_1d_middle_of_day(self):
        t = datetime(2026, 4, 18, 14, 37, 0, tzinfo=timezone.utc)
        result = last_closed_bar_time("1d", t)
        # last closed 1d bar opened at 2026-04-17 00:00 UTC
        expected = int(datetime(2026, 4, 17, 0, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        assert result == expected

    def test_default_now_if_none(self, monkeypatch):
        # Passing None uses datetime.now(UTC); just verify it runs without error
        result = last_closed_bar_time("1h")
        assert isinstance(result, int) and result > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_timeframes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'data.timeframes'`

- [ ] **Step 3: Implement `data/timeframes.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_timeframes.py -v`
Expected: all 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add data/timeframes.py tests/test_timeframes.py
git commit -m "feat(data): add timeframes registry with last_closed_bar_time helper"
```

---

### Task 3: `data/metrics.py`

**Files:**
- Create: `data/metrics.py`
- Create: `tests/test_metrics.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_metrics.py`:
```python
import threading
import pytest
from data import metrics


@pytest.fixture(autouse=True)
def reset_metrics():
    metrics._counters.clear()
    metrics._latencies.clear()
    yield
    metrics._counters.clear()
    metrics._latencies.clear()


class TestCounters:
    def test_inc_no_labels(self):
        metrics.inc("fetches_total")
        metrics.inc("fetches_total", n=3)
        stats = metrics.get_stats()
        assert stats["counters"]["fetches_total"] == {(): 4}

    def test_inc_with_labels(self):
        metrics.inc("fetches_total", labels={"provider": "binance"})
        metrics.inc("fetches_total", labels={"provider": "binance"})
        metrics.inc("fetches_total", labels={"provider": "bybit"})
        stats = metrics.get_stats()
        assert stats["counters"]["fetches_total"][(("provider", "binance"),)] == 2
        assert stats["counters"]["fetches_total"][(("provider", "bybit"),)] == 1

    def test_inc_thread_safety(self):
        def worker():
            for _ in range(1000):
                metrics.inc("race_counter")
        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()
        stats = metrics.get_stats()
        assert stats["counters"]["race_counter"][()] == 8000


class TestLatencyHistogram:
    def test_observe_and_percentiles(self):
        for v in [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]:
            metrics.observe("fetch_latency_ms", v)
        stats = metrics.get_stats()
        p50_key = ("fetch_latency_ms", ())
        assert p50_key in stats["latency_p50_ms"]
        # p50 of 1..10 scaled to 10..100: median is 50 or 60 depending on interpolation
        assert 40 <= stats["latency_p50_ms"][p50_key] <= 70
        assert 85 <= stats["latency_p95_ms"][p50_key] <= 100

    def test_observe_bounded_deque(self):
        # maxlen=100; adding 250 values should retain only the last 100
        for v in range(250):
            metrics.observe("bounded", v)
        stats = metrics.get_stats()
        # Median of last 100 values (150..249) is ~199
        assert 190 <= stats["latency_p50_ms"][("bounded", ())] <= 210


class TestGetStats:
    def test_snapshot_is_plain_dict(self):
        metrics.inc("x")
        metrics.observe("lat", 5.0)
        stats = metrics.get_stats()
        # No mutable references leak
        assert isinstance(stats, dict)
        assert isinstance(stats["counters"], dict)
        assert isinstance(stats["latency_p50_ms"], dict)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_metrics.py -v`
Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 3: Implement `data/metrics.py`**

```python
"""Thread-safe metrics: counters + latency histograms.

Exposed via get_stats() for /status endpoint integration. Zero external deps.
"""
import threading
from collections import defaultdict, deque


_lock = threading.Lock()
_counters: dict[str, dict[tuple, int]] = defaultdict(lambda: defaultdict(int))
_latencies: dict[tuple[str, tuple], deque] = defaultdict(lambda: deque(maxlen=100))


def _labels_key(labels: dict | None) -> tuple:
    if not labels:
        return ()
    return tuple(sorted(labels.items()))


def inc(name: str, n: int = 1, labels: dict | None = None) -> None:
    """Increment a counter."""
    key = _labels_key(labels)
    with _lock:
        _counters[name][key] += n


def observe(name: str, value: float, labels: dict | None = None) -> None:
    """Record an observation for latency/size-like metrics.

    Retains the last 100 samples per (name, labels) pair for cheap percentiles.
    """
    key = _labels_key(labels)
    with _lock:
        _latencies[(name, key)].append(value)


def _percentile(values: list[float], p: int) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = int(len(sorted_vals) * p / 100)
    idx = min(idx, len(sorted_vals) - 1)
    return sorted_vals[idx]


def get_stats() -> dict:
    """Snapshot of all metrics. Safe to call from any thread."""
    with _lock:
        counters_snapshot = {
            name: dict(vals) for name, vals in _counters.items()
        }
        latencies_p50 = {
            key: _percentile(list(samples), 50)
            for key, samples in _latencies.items()
        }
        latencies_p95 = {
            key: _percentile(list(samples), 95)
            for key, samples in _latencies.items()
        }
    return {
        "counters": counters_snapshot,
        "latency_p50_ms": latencies_p50,
        "latency_p95_ms": latencies_p95,
    }
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_metrics.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add data/metrics.py tests/test_metrics.py
git commit -m "feat(data): add thread-safe metrics module with counters and latency histograms"
```

---

### Task 4: `data/providers/base.py` — Protocol, Bar, exceptions

**Files:**
- Create: `data/providers/base.py`
- Create: `tests/test_providers_base.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_providers_base.py`:
```python
import pytest
from data.providers.base import (
    Bar, ProviderError, ProviderInvalidSymbol,
    ProviderRateLimited, ProviderTemporaryError,
)


class TestBar:
    def test_construction_and_tuple(self):
        b = Bar(
            symbol="BTCUSDT", timeframe="1h", open_time=1000,
            open=100.0, high=110.0, low=95.0, close=105.0, volume=50.0,
            provider="binance", fetched_at=2000,
        )
        assert b.symbol == "BTCUSDT"
        tup = b.as_tuple()
        assert tup == ("BTCUSDT", "1h", 1000, 100.0, 110.0, 95.0, 105.0, 50.0, "binance", 2000)

    def test_frozen(self):
        b = Bar(symbol="X", timeframe="1h", open_time=0, open=1.0, high=1.0, low=1.0, close=1.0,
                volume=0.0, provider="x", fetched_at=0)
        with pytest.raises((AttributeError, Exception)):
            b.symbol = "Y"


class TestExceptionHierarchy:
    def test_all_inherit_from_provider_error(self):
        assert issubclass(ProviderInvalidSymbol, ProviderError)
        assert issubclass(ProviderRateLimited, ProviderError)
        assert issubclass(ProviderTemporaryError, ProviderError)

    def test_raising_and_catching(self):
        with pytest.raises(ProviderError):
            raise ProviderRateLimited("too many requests")
        with pytest.raises(ProviderError):
            raise ProviderTemporaryError("503")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_providers_base.py -v`
Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 3: Implement `data/providers/base.py`**

```python
"""Provider adapter contract: Bar dataclass, exceptions, Protocol.

Adding a new provider = implement ProviderAdapter Protocol in a new module and
register it in data._fetcher._PROVIDERS.
"""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Bar:
    """Normalized OHLCV bar, provider-agnostic."""
    symbol: str
    timeframe: str
    open_time: int    # ms UTC
    open: float
    high: float
    low: float
    close: float
    volume: float
    provider: str
    fetched_at: int   # ms UTC

    def as_tuple(self) -> tuple:
        """Serialization for SQLite INSERT."""
        return (
            self.symbol, self.timeframe, self.open_time,
            self.open, self.high, self.low, self.close, self.volume,
            self.provider, self.fetched_at,
        )


class ProviderError(Exception):
    """Base of all provider errors. May propagate to consumers."""


class ProviderInvalidSymbol(ProviderError):
    """Symbol does not exist on this provider. FATAL — no failover triggered."""


class ProviderRateLimited(ProviderError):
    """Rate limit hit. Triggers failover threshold counter."""


class ProviderTemporaryError(ProviderError):
    """5xx, timeout, DNS. Triggers failover threshold counter."""


class AllProvidersFailedError(ProviderError):
    """Every provider in the registry returned an error."""


class ProviderAdapter(Protocol):
    name: str
    rate_limit_per_min: int

    def fetch_klines(
        self, symbol: str, timeframe: str, start_ms: int, end_ms: int
    ) -> list[Bar]:
        """Fetch bars with open_time in [start_ms, end_ms] (inclusive both).

        Returns list ordered by open_time ascending. Empty list means
        no bars exist in that range (e.g., pre-listing).
        """
        ...

    def is_healthy(self) -> bool:
        """Cheap probe used for recovery logic after failover."""
        ...
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_providers_base.py -v`
Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add data/providers/base.py tests/test_providers_base.py
git commit -m "feat(data): add ProviderAdapter Protocol, Bar dataclass, error hierarchy"
```

---

## Phase 2 — Storage layer

### Task 5: `data/_storage.py` — connection lifecycle + schema

**Files:**
- Create: `data/_storage.py` (initial)
- Create: `tests/test_storage.py` (initial)

- [ ] **Step 1: Write failing tests for schema init + meta/symbol_earliest helpers**

Create `tests/test_storage.py`:
```python
import os
import sqlite3
import threading
import pytest
from data import _storage


class TestSchemaInit:
    def test_init_creates_tables(self, tmp_ohlcv_db):
        conn = _storage._conn()
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "ohlcv" in names
        assert "meta" in names
        assert "symbol_earliest" in names

    def test_init_creates_index(self, tmp_ohlcv_db):
        conn = _storage._conn()
        idx = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_ohlcv_time'"
        ).fetchone()
        assert idx is not None

    def test_init_sets_schema_version(self, tmp_ohlcv_db):
        conn = _storage._conn()
        v = conn.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()
        assert v[0] == "1"

    def test_pragmas_wal_mode(self, tmp_ohlcv_db):
        conn = _storage._conn()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"


class TestSymbolEarliest:
    def test_first_bar_ms_missing_returns_none(self, tmp_ohlcv_db):
        assert _storage.first_bar_ms("BTCUSDT", "1h") is None

    def test_set_and_get_first_bar_ms(self, tmp_ohlcv_db):
        _storage.set_first_bar_ms("BTCUSDT", "1h", 1609459200000)
        assert _storage.first_bar_ms("BTCUSDT", "1h") == 1609459200000

    def test_set_first_bar_ms_upsert(self, tmp_ohlcv_db):
        _storage.set_first_bar_ms("BTCUSDT", "1h", 1000)
        _storage.set_first_bar_ms("BTCUSDT", "1h", 2000)  # update
        assert _storage.first_bar_ms("BTCUSDT", "1h") == 2000


class TestThreadLocalConns:
    def test_different_threads_get_different_conns(self, tmp_ohlcv_db):
        conns = {}
        def worker(name):
            conns[name] = id(_storage._conn())
        t1 = threading.Thread(target=worker, args=("t1",))
        t2 = threading.Thread(target=worker, args=("t2",))
        t1.start(); t2.start(); t1.join(); t2.join()
        assert conns["t1"] != conns["t2"]

    def test_same_thread_reuses_conn(self, tmp_ohlcv_db):
        c1 = _storage._conn()
        c2 = _storage._conn()
        assert c1 is c2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_storage.py -v`
Expected: FAIL with import error.

- [ ] **Step 3: Implement `data/_storage.py` (schema + conn lifecycle + earliest helpers)**

```python
"""SQLite storage for OHLCV bars. Thread-local connections, WAL mode, idempotent upserts."""
import os
import sqlite3
import threading
from pathlib import Path

from data.providers.base import Bar


SCHEMA_VERSION = 1

_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = str(_ROOT / "data" / "ohlcv.db")

_tls = threading.local()


def _conn() -> sqlite3.Connection:
    """Lazy thread-local connection with WAL pragmas applied."""
    if not hasattr(_tls, "conn"):
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None, timeout=5)
        _apply_pragmas(conn)
        _tls.conn = conn
    return _tls.conn


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = NORMAL;
        PRAGMA busy_timeout = 5000;
        PRAGMA cache_size = -20000;
        PRAGMA temp_store = MEMORY;
    """)


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ohlcv (
    symbol     TEXT    NOT NULL,
    timeframe  TEXT    NOT NULL,
    open_time  INTEGER NOT NULL,
    open       REAL    NOT NULL,
    high       REAL    NOT NULL,
    low        REAL    NOT NULL,
    close      REAL    NOT NULL,
    volume     REAL    NOT NULL,
    provider   TEXT    NOT NULL,
    fetched_at INTEGER NOT NULL,
    PRIMARY KEY (symbol, timeframe, open_time)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_ohlcv_time
    ON ohlcv(symbol, timeframe, open_time DESC);

CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS symbol_earliest (
    symbol         TEXT    NOT NULL,
    timeframe      TEXT    NOT NULL,
    first_bar_ms   INTEGER NOT NULL,
    PRIMARY KEY (symbol, timeframe)
);
"""


def init_schema() -> None:
    """Create tables and seed schema_version if new DB."""
    conn = _conn()
    conn.executescript(_SCHEMA_SQL)
    current = conn.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()
    if current is None:
        conn.execute(
            "INSERT INTO meta (k, v) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )


def first_bar_ms(symbol: str, timeframe: str) -> int | None:
    row = _conn().execute(
        "SELECT first_bar_ms FROM symbol_earliest WHERE symbol=? AND timeframe=?",
        (symbol, timeframe),
    ).fetchone()
    return row[0] if row else None


def set_first_bar_ms(symbol: str, timeframe: str, value_ms: int) -> None:
    _conn().execute(
        "INSERT OR REPLACE INTO symbol_earliest (symbol, timeframe, first_bar_ms) VALUES (?, ?, ?)",
        (symbol, timeframe, value_ms),
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_storage.py -v`
Expected: 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add data/_storage.py tests/test_storage.py
git commit -m "feat(data): add SQLite storage foundation with thread-local conns and schema init"
```

---

### Task 6: `data/_storage.py` — `upsert_many` with validation

**Files:**
- Modify: `data/_storage.py`
- Modify: `tests/test_storage.py`

- [ ] **Step 1: Add failing tests for upsert_many**

Append to `tests/test_storage.py`:
```python
import time
from data.providers.base import Bar


def _mk_bar(open_time=1000, price=100.0, **overrides):
    defaults = dict(
        symbol="BTCUSDT", timeframe="1h", open_time=open_time,
        open=price, high=price * 1.02, low=price * 0.98, close=price, volume=10.0,
        provider="test", fetched_at=int(time.time() * 1000),
    )
    defaults.update(overrides)
    return Bar(**defaults)


class TestUpsertMany:
    def test_insert_new_bars(self, tmp_ohlcv_db):
        bars = [_mk_bar(open_time=t * 3600_000) for t in range(5)]
        n = _storage.upsert_many(bars)
        assert n == 5
        count = _storage._conn().execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]
        assert count == 5

    def test_upsert_overwrites_same_pk(self, tmp_ohlcv_db):
        _storage.upsert_many([_mk_bar(open_time=1000, price=100.0)])
        _storage.upsert_many([_mk_bar(open_time=1000, price=200.0)])
        row = _storage._conn().execute(
            "SELECT close FROM ohlcv WHERE open_time=1000").fetchone()
        assert row[0] == 200.0

    def test_drops_invalid_bars_high_lt_low(self, tmp_ohlcv_db, caplog):
        bad = _mk_bar(open_time=1000, price=100.0)
        bad = Bar(**{**bad.__dict__, "high": 50.0, "low": 150.0})  # swapped
        good = _mk_bar(open_time=2000, price=100.0)
        n = _storage.upsert_many([bad, good])
        assert n == 1
        count = _storage._conn().execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]
        assert count == 1

    def test_drops_invalid_bars_negative_volume(self, tmp_ohlcv_db):
        bad = _mk_bar(open_time=1000)
        bad = Bar(**{**bad.__dict__, "volume": -5.0})
        assert _storage.upsert_many([bad]) == 0

    def test_drops_invalid_bars_zero_price(self, tmp_ohlcv_db):
        bad = _mk_bar(open_time=1000)
        bad = Bar(**{**bad.__dict__, "open": 0.0})
        assert _storage.upsert_many([bad]) == 0

    def test_high_below_open_or_close_is_invalid(self, tmp_ohlcv_db):
        bad = _mk_bar(open_time=1000, price=100.0)
        bad = Bar(**{**bad.__dict__, "high": 99.0, "low": 95.0, "open": 100.0, "close": 98.0})
        # high (99) < open (100) → invalid
        assert _storage.upsert_many([bad]) == 0

    def test_empty_list_is_noop(self, tmp_ohlcv_db):
        assert _storage.upsert_many([]) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_storage.py::TestUpsertMany -v`
Expected: FAIL — `upsert_many` not defined.

- [ ] **Step 3: Add implementation to `data/_storage.py`**

Append to `data/_storage.py`:
```python
import logging

from data import metrics

log = logging.getLogger("data.market")


def _is_valid_bar(bar: Bar) -> bool:
    if bar.high < bar.low:
        return False
    if bar.high < max(bar.open, bar.close):
        return False
    if bar.low > min(bar.open, bar.close):
        return False
    if bar.volume < 0:
        return False
    if bar.open <= 0 or bar.close <= 0:
        return False
    return True


_UPSERT_SQL = """
INSERT OR REPLACE INTO ohlcv
    (symbol, timeframe, open_time, open, high, low, close, volume, provider, fetched_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def upsert_many(bars: list[Bar]) -> int:
    """Insert or replace a batch of bars. Returns count persisted.

    Invalid bars (failing _is_valid_bar) are dropped with a WARN log + metric.
    """
    if not bars:
        return 0
    valid = [b for b in bars if _is_valid_bar(b)]
    dropped = len(bars) - len(valid)
    if dropped:
        metrics.inc("invalid_bars_dropped_total", dropped)
        log.warning("Dropped %d invalid bars during upsert_many", dropped)
    if not valid:
        return 0
    conn = _conn()
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.executemany(_UPSERT_SQL, [b.as_tuple() for b in valid])
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    metrics.inc("bars_upserted_total", len(valid))
    return len(valid)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_storage.py::TestUpsertMany -v`
Expected: all 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add data/_storage.py tests/test_storage.py
git commit -m "feat(data): add upsert_many with bar validation and metrics"
```

---

### Task 7: `data/_storage.py` — query methods (tail, range, stats)

**Files:**
- Modify: `data/_storage.py`
- Modify: `tests/test_storage.py`

- [ ] **Step 1: Add failing tests for queries**

Append to `tests/test_storage.py`:
```python
class TestQueryMethods:
    def test_max_open_time_empty(self, tmp_ohlcv_db):
        assert _storage.max_open_time("BTCUSDT", "1h") is None

    def test_max_open_time_returns_latest(self, tmp_ohlcv_db):
        _storage.upsert_many([_mk_bar(open_time=t * 3600_000) for t in [1, 5, 3]])
        assert _storage.max_open_time("BTCUSDT", "1h") == 5 * 3600_000

    def test_count_tail(self, tmp_ohlcv_db):
        _storage.upsert_many([_mk_bar(open_time=t * 3600_000) for t in range(10)])
        # count bars with open_time <= 5*3600_000, up to 3
        assert _storage.count_tail("BTCUSDT", "1h", 5 * 3600_000, 3) == 3
        # no upper bound reached: returns all
        assert _storage.count_tail("BTCUSDT", "1h", 9 * 3600_000, 100) == 10

    def test_tail_returns_ascending(self, tmp_ohlcv_db):
        bars = [_mk_bar(open_time=t * 3600_000, price=float(t)) for t in [3, 1, 4, 1, 5, 9, 2, 6]]
        _storage.upsert_many(bars)
        df = _storage.tail("BTCUSDT", "1h", 3)
        # last 3 bars sorted ascending by open_time
        assert list(df["open_time"]) == [4 * 3600_000, 5 * 3600_000, 6 * 3600_000]

    def test_range_returns_filtered(self, tmp_ohlcv_db):
        _storage.upsert_many([_mk_bar(open_time=t * 3600_000) for t in range(10)])
        df = _storage.range_("BTCUSDT", "1h", 2 * 3600_000, 5 * 3600_000)
        assert list(df["open_time"]) == [t * 3600_000 for t in [2, 3, 4, 5]]

    def test_range_stats(self, tmp_ohlcv_db):
        _storage.upsert_many([_mk_bar(open_time=t * 3600_000) for t in [2, 3, 7, 8]])
        min_t, max_t, count = _storage.range_stats("BTCUSDT", "1h", 0, 10 * 3600_000)
        assert min_t == 2 * 3600_000
        assert max_t == 8 * 3600_000
        assert count == 4

    def test_range_stats_empty(self, tmp_ohlcv_db):
        min_t, max_t, count = _storage.range_stats("BTCUSDT", "1h", 0, 3600_000)
        assert min_t is None
        assert max_t is None
        assert count == 0

    def test_times_in_range(self, tmp_ohlcv_db):
        times_input = [1, 2, 5, 7]
        _storage.upsert_many([_mk_bar(open_time=t * 3600_000) for t in times_input])
        result = _storage.times_in_range("BTCUSDT", "1h", 0, 10 * 3600_000)
        assert set(result) == {t * 3600_000 for t in times_input}
```

- [ ] **Step 2: Run tests to verify fail**

Run: `python -m pytest tests/test_storage.py::TestQueryMethods -v`
Expected: FAIL — functions not defined.

- [ ] **Step 3: Add query methods to `data/_storage.py`**

Append:
```python
import pandas as pd


def max_open_time(symbol: str, timeframe: str) -> int | None:
    row = _conn().execute(
        "SELECT MAX(open_time) FROM ohlcv WHERE symbol=? AND timeframe=?",
        (symbol, timeframe),
    ).fetchone()
    return row[0] if row and row[0] is not None else None


def count_tail(symbol: str, timeframe: str, end_time_inclusive: int, limit: int) -> int:
    """Count bars with open_time <= end_time_inclusive, up to `limit`."""
    row = _conn().execute(
        """SELECT COUNT(*) FROM (
               SELECT 1 FROM ohlcv
               WHERE symbol=? AND timeframe=? AND open_time <= ?
               ORDER BY open_time DESC LIMIT ?
           )""",
        (symbol, timeframe, end_time_inclusive, limit),
    ).fetchone()
    return row[0]


_OHLCV_COLUMNS = ["open_time", "open", "high", "low", "close", "volume", "provider", "fetched_at"]


def tail(symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    """Last `limit` bars ordered ascending."""
    rows = _conn().execute(
        """SELECT open_time, open, high, low, close, volume, provider, fetched_at
           FROM ohlcv WHERE symbol=? AND timeframe=?
           ORDER BY open_time DESC LIMIT ?""",
        (symbol, timeframe, limit),
    ).fetchall()
    df = pd.DataFrame(rows, columns=_OHLCV_COLUMNS)
    return df.iloc[::-1].reset_index(drop=True)


def range_(symbol: str, timeframe: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    """Bars with open_time in [start_ms, end_ms] inclusive, ordered ascending."""
    rows = _conn().execute(
        """SELECT open_time, open, high, low, close, volume, provider, fetched_at
           FROM ohlcv WHERE symbol=? AND timeframe=? AND open_time BETWEEN ? AND ?
           ORDER BY open_time ASC""",
        (symbol, timeframe, start_ms, end_ms),
    ).fetchall()
    return pd.DataFrame(rows, columns=_OHLCV_COLUMNS)


def range_stats(symbol: str, timeframe: str, start_ms: int, end_ms: int) -> tuple[int | None, int | None, int]:
    """Return (min_open_time, max_open_time, count) for bars in [start_ms, end_ms] inclusive."""
    row = _conn().execute(
        """SELECT MIN(open_time), MAX(open_time), COUNT(*) FROM ohlcv
           WHERE symbol=? AND timeframe=? AND open_time BETWEEN ? AND ?""",
        (symbol, timeframe, start_ms, end_ms),
    ).fetchone()
    return (row[0], row[1], row[2] or 0)


def times_in_range(symbol: str, timeframe: str, start_ms: int, end_ms: int) -> list[int]:
    """List of open_time values present in [start_ms, end_ms] inclusive."""
    rows = _conn().execute(
        """SELECT open_time FROM ohlcv
           WHERE symbol=? AND timeframe=? AND open_time BETWEEN ? AND ?""",
        (symbol, timeframe, start_ms, end_ms),
    ).fetchall()
    return [r[0] for r in rows]
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_storage.py -v`
Expected: all storage tests PASS.

- [ ] **Step 5: Commit**

```bash
git add data/_storage.py tests/test_storage.py
git commit -m "feat(data): add OHLCV query methods (tail, range, stats, times_in_range)"
```

---

## Phase 3 — Provider adapters

### Task 8: `data/providers/binance.py` — BinanceAdapter

**Files:**
- Create: `data/providers/binance.py`
- Create: `tests/test_providers_binance.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_providers_binance.py`:
```python
import pytest
from unittest.mock import MagicMock, patch
from data.providers.base import (
    ProviderInvalidSymbol, ProviderRateLimited, ProviderTemporaryError,
)
from data.providers.binance import BinanceAdapter


def _mock_response(status_code=200, json_data=None):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_data if json_data is not None else []
    r.text = ""
    return r


class TestBinanceAdapter:
    def test_name_and_rate_limit(self):
        a = BinanceAdapter()
        assert a.name == "binance"
        assert a.rate_limit_per_min == 1200

    def test_fetch_klines_parses_response(self):
        raw = [
            # open_time, open, high, low, close, volume, close_time, quote_vol, trades, ...
            [1000, "100.0", "110.0", "95.0", "105.0", "50.0", 1999, "0", 0, "0", "0", "0"],
            [5000, "105.0", "115.0", "100.0", "110.0", "60.0", 5999, "0", 0, "0", "0", "0"],
        ]
        a = BinanceAdapter()
        with patch("data.providers.binance._http_get", return_value=_mock_response(json_data=raw)):
            bars = a.fetch_klines("BTCUSDT", "1h", 1000, 5000)
        assert len(bars) == 2
        assert bars[0].symbol == "BTCUSDT"
        assert bars[0].timeframe == "1h"
        assert bars[0].open_time == 1000
        assert bars[0].open == 100.0
        assert bars[0].high == 110.0
        assert bars[0].provider == "binance"

    def test_http_429_raises_rate_limited(self):
        a = BinanceAdapter()
        with patch("data.providers.binance._http_get", return_value=_mock_response(429)):
            with pytest.raises(ProviderRateLimited):
                a.fetch_klines("BTCUSDT", "1h", 0, 1000)

    def test_http_418_raises_rate_limited(self):
        a = BinanceAdapter()
        with patch("data.providers.binance._http_get", return_value=_mock_response(418)):
            with pytest.raises(ProviderRateLimited):
                a.fetch_klines("BTCUSDT", "1h", 0, 1000)

    def test_http_400_raises_invalid_symbol(self):
        a = BinanceAdapter()
        with patch("data.providers.binance._http_get", return_value=_mock_response(400)):
            with pytest.raises(ProviderInvalidSymbol):
                a.fetch_klines("FAKEUSDT", "1h", 0, 1000)

    def test_http_5xx_raises_temporary(self):
        a = BinanceAdapter()
        with patch("data.providers.binance._http_get", return_value=_mock_response(503)):
            with pytest.raises(ProviderTemporaryError):
                a.fetch_klines("BTCUSDT", "1h", 0, 1000)

    def test_timeout_raises_temporary(self):
        import requests
        a = BinanceAdapter()
        with patch("data.providers.binance._http_get", side_effect=requests.Timeout()):
            with pytest.raises(ProviderTemporaryError):
                a.fetch_klines("BTCUSDT", "1h", 0, 1000)

    def test_empty_response(self):
        a = BinanceAdapter()
        with patch("data.providers.binance._http_get", return_value=_mock_response(json_data=[])):
            bars = a.fetch_klines("BTCUSDT", "1h", 0, 1000)
        assert bars == []

    def test_is_healthy_true(self):
        a = BinanceAdapter()
        with patch("data.providers.binance._http_get", return_value=_mock_response(200)):
            assert a.is_healthy() is True

    def test_is_healthy_false(self):
        import requests
        a = BinanceAdapter()
        with patch("data.providers.binance._http_get", side_effect=requests.Timeout()):
            assert a.is_healthy() is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_providers_binance.py -v`
Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 3: Implement `data/providers/binance.py`**

```python
"""Binance spot klines adapter (primary provider)."""
import time
import requests

from data.providers.base import (
    Bar, ProviderInvalidSymbol, ProviderRateLimited, ProviderTemporaryError,
)


def _http_get(url, params=None, timeout=10):
    """Thin wrapper so tests can mock just this call."""
    return requests.get(url, params=params, timeout=timeout)


class BinanceAdapter:
    name = "binance"
    rate_limit_per_min = 1200
    BASE_URL = "https://api.binance.com"

    TF_MAP = {
        "5m": "5m", "15m": "15m", "30m": "30m",
        "1h": "1h", "4h": "4h", "1d": "1d", "1w": "1w",
    }

    def fetch_klines(self, symbol: str, timeframe: str, start_ms: int, end_ms: int) -> list[Bar]:
        params = {
            "symbol": symbol,
            "interval": self.TF_MAP[timeframe],
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": 1000,
        }
        try:
            r = _http_get(f"{self.BASE_URL}/api/v3/klines", params=params, timeout=10)
        except (requests.Timeout, requests.ConnectionError) as e:
            raise ProviderTemporaryError(f"{type(e).__name__}: {e}") from e

        if r.status_code in (429, 418):
            raise ProviderRateLimited(f"HTTP {r.status_code}: {r.text[:100]}")
        if r.status_code == 400:
            raise ProviderInvalidSymbol(f"{symbol}: {r.text[:200]}")
        if r.status_code >= 500:
            raise ProviderTemporaryError(f"HTTP {r.status_code}")
        if r.status_code != 200:
            raise ProviderTemporaryError(f"HTTP {r.status_code}: {r.text[:200]}")

        now_ms = int(time.time() * 1000)
        return [
            Bar(
                symbol=symbol, timeframe=timeframe, open_time=int(row[0]),
                open=float(row[1]), high=float(row[2]), low=float(row[3]),
                close=float(row[4]), volume=float(row[5]),
                provider=self.name, fetched_at=now_ms,
            )
            for row in r.json()
        ]

    def is_healthy(self) -> bool:
        try:
            r = _http_get(f"{self.BASE_URL}/api/v3/ping", timeout=3)
            return r.status_code == 200
        except Exception:
            return False
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_providers_binance.py -v`
Expected: all 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add data/providers/binance.py tests/test_providers_binance.py
git commit -m "feat(data): add BinanceAdapter with error taxonomy mapping"
```

---

### Task 9: `data/providers/bybit.py` — BybitAdapter

**Files:**
- Create: `data/providers/bybit.py`
- Create: `tests/test_providers_bybit.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_providers_bybit.py`:
```python
import pytest
from unittest.mock import MagicMock, patch
from data.providers.base import (
    ProviderInvalidSymbol, ProviderRateLimited, ProviderTemporaryError,
)
from data.providers.bybit import BybitAdapter


def _mock_response(status_code=200, json_data=None):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_data if json_data is not None else {"result": {"list": []}, "retCode": 0}
    r.text = ""
    return r


class TestBybitAdapter:
    def test_name_and_rate_limit(self):
        a = BybitAdapter()
        assert a.name == "bybit"
        assert a.rate_limit_per_min == 600

    def test_fetch_klines_parses_response(self):
        # Bybit v5 kline: list is DESCENDING by time; we must reverse.
        # fields: [startTime, open, high, low, close, volume, turnover]
        raw = {
            "retCode": 0,
            "result": {
                "list": [
                    ["5000", "105.0", "115.0", "100.0", "110.0", "60.0", "0"],
                    ["1000", "100.0", "110.0", "95.0", "105.0", "50.0", "0"],
                ]
            }
        }
        a = BybitAdapter()
        with patch("data.providers.bybit._http_get", return_value=_mock_response(json_data=raw)):
            bars = a.fetch_klines("BTCUSDT", "1h", 1000, 5000)
        assert len(bars) == 2
        # After reverse: ascending by open_time
        assert bars[0].open_time == 1000
        assert bars[1].open_time == 5000
        assert bars[0].provider == "bybit"

    def test_http_429_raises_rate_limited(self):
        a = BybitAdapter()
        with patch("data.providers.bybit._http_get", return_value=_mock_response(429)):
            with pytest.raises(ProviderRateLimited):
                a.fetch_klines("BTCUSDT", "1h", 0, 1000)

    def test_retcode_invalid_symbol(self):
        raw = {"retCode": 10001, "retMsg": "Invalid symbol"}
        a = BybitAdapter()
        with patch("data.providers.bybit._http_get", return_value=_mock_response(200, raw)):
            with pytest.raises(ProviderInvalidSymbol):
                a.fetch_klines("FAKEUSDT", "1h", 0, 1000)

    def test_http_5xx_raises_temporary(self):
        a = BybitAdapter()
        with patch("data.providers.bybit._http_get", return_value=_mock_response(502)):
            with pytest.raises(ProviderTemporaryError):
                a.fetch_klines("BTCUSDT", "1h", 0, 1000)

    def test_timeout_raises_temporary(self):
        import requests
        a = BybitAdapter()
        with patch("data.providers.bybit._http_get", side_effect=requests.Timeout()):
            with pytest.raises(ProviderTemporaryError):
                a.fetch_klines("BTCUSDT", "1h", 0, 1000)

    def test_empty_result_list(self):
        a = BybitAdapter()
        with patch("data.providers.bybit._http_get", return_value=_mock_response(200)):
            bars = a.fetch_klines("BTCUSDT", "1h", 0, 1000)
        assert bars == []

    def test_is_healthy_true(self):
        a = BybitAdapter()
        raw = {"retCode": 0, "result": {"timeSecond": str(int(1e9))}}
        with patch("data.providers.bybit._http_get", return_value=_mock_response(200, raw)):
            assert a.is_healthy() is True

    def test_is_healthy_false(self):
        import requests
        a = BybitAdapter()
        with patch("data.providers.bybit._http_get", side_effect=requests.Timeout()):
            assert a.is_healthy() is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_providers_bybit.py -v`
Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 3: Implement `data/providers/bybit.py`**

```python
"""Bybit v5 kline adapter (fallback provider)."""
import time
import requests

from data.providers.base import (
    Bar, ProviderInvalidSymbol, ProviderRateLimited, ProviderTemporaryError,
)


def _http_get(url, params=None, timeout=10):
    return requests.get(url, params=params, timeout=timeout)


class BybitAdapter:
    name = "bybit"
    rate_limit_per_min = 600
    BASE_URL = "https://api.bybit.com"

    TF_MAP = {
        "5m": "5", "15m": "15", "30m": "30",
        "1h": "60", "4h": "240", "1d": "D", "1w": "W",
    }

    # Bybit retCodes that indicate invalid symbol (non-exhaustive).
    _INVALID_SYMBOL_CODES = {10001}

    def fetch_klines(self, symbol: str, timeframe: str, start_ms: int, end_ms: int) -> list[Bar]:
        params = {
            "category": "spot",
            "symbol": symbol,
            "interval": self.TF_MAP[timeframe],
            "start": start_ms,
            "end": end_ms,
            "limit": 1000,
        }
        try:
            r = _http_get(f"{self.BASE_URL}/v5/market/kline", params=params, timeout=10)
        except (requests.Timeout, requests.ConnectionError) as e:
            raise ProviderTemporaryError(f"{type(e).__name__}: {e}") from e

        if r.status_code in (429, 418):
            raise ProviderRateLimited(f"HTTP {r.status_code}")
        if r.status_code >= 500:
            raise ProviderTemporaryError(f"HTTP {r.status_code}")
        if r.status_code != 200:
            raise ProviderTemporaryError(f"HTTP {r.status_code}: {r.text[:200]}")

        body = r.json() or {}
        ret_code = body.get("retCode", 0)
        if ret_code in self._INVALID_SYMBOL_CODES:
            raise ProviderInvalidSymbol(f"{symbol}: {body.get('retMsg', '')}")
        if ret_code != 0:
            raise ProviderTemporaryError(f"Bybit retCode {ret_code}: {body.get('retMsg', '')}")

        items = ((body.get("result") or {}).get("list") or [])
        now_ms = int(time.time() * 1000)
        bars = [
            Bar(
                symbol=symbol, timeframe=timeframe, open_time=int(row[0]),
                open=float(row[1]), high=float(row[2]), low=float(row[3]),
                close=float(row[4]), volume=float(row[5]),
                provider=self.name, fetched_at=now_ms,
            )
            for row in items
        ]
        # Bybit returns DESCENDING by time; normalize to ascending.
        bars.sort(key=lambda b: b.open_time)
        return bars

    def is_healthy(self) -> bool:
        try:
            r = _http_get(f"{self.BASE_URL}/v5/market/time", timeout=3)
            return r.status_code == 200 and (r.json() or {}).get("retCode", -1) == 0
        except Exception:
            return False
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_providers_bybit.py -v`
Expected: all 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add data/providers/bybit.py tests/test_providers_bybit.py
git commit -m "feat(data): add BybitAdapter with retCode-based error classification"
```

---

## Phase 4 — Fetcher orchestration

### Task 10: `data/_fetcher.py` — rate limiter wiring + lock registry

**Files:**
- Create: `data/_fetcher.py` (initial)
- Create: `tests/test_fetcher.py` (initial)

- [ ] **Step 1: Write failing tests for lock registry**

Create `tests/test_fetcher.py`:
```python
import threading
import pytest
from data import _fetcher


class TestLockRegistry:
    def test_returns_lock_instance(self):
        lock = _fetcher._get_or_create_lock("BTCUSDT", "1h")
        assert hasattr(lock, "acquire") and hasattr(lock, "release")

    def test_same_key_returns_same_lock(self):
        a = _fetcher._get_or_create_lock("BTCUSDT", "1h")
        b = _fetcher._get_or_create_lock("BTCUSDT", "1h")
        assert a is b

    def test_different_keys_different_locks(self):
        a = _fetcher._get_or_create_lock("BTCUSDT", "1h")
        b = _fetcher._get_or_create_lock("ETHUSDT", "1h")
        c = _fetcher._get_or_create_lock("BTCUSDT", "5m")
        assert a is not b
        assert a is not c
        assert b is not c

    def test_thread_safe_registry(self):
        # Concurrent creation of the same lock must return the same object
        results = []
        def worker():
            results.append(_fetcher._get_or_create_lock("CONCURRENT", "1h"))
        threads = [threading.Thread(target=worker) for _ in range(16)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert len(set(id(r) for r in results)) == 1
```

- [ ] **Step 2: Run tests to verify fail**

Run: `python -m pytest tests/test_fetcher.py -v`
Expected: FAIL with ImportError.

- [ ] **Step 3: Implement initial `data/_fetcher.py`**

```python
"""Fetcher: orchestrates providers with failover, dedup, rate limiting."""
import threading
import time
import logging

from data.providers.base import (
    ProviderAdapter, ProviderError, ProviderInvalidSymbol,
    ProviderRateLimited, ProviderTemporaryError, AllProvidersFailedError, Bar,
)
from data.providers.binance import BinanceAdapter
from data.providers.bybit import BybitAdapter
from data import metrics, _storage
from data.timeframes import delta_ms, last_closed_bar_time


log = logging.getLogger("data.market")


# ─── Provider registry ──────────────────────────────────────────────────────
_PROVIDERS: list[ProviderAdapter] = [BinanceAdapter(), BybitAdapter()]


# ─── Failover state (module-level, guarded) ─────────────────────────────────
_state_lock = threading.Lock()
_active_idx: int = 0
_consecutive_failures: int = 0
_last_probe_ms: int = 0

FAILOVER_THRESHOLD = 3
RECOVERY_PROBE_INTERVAL_MS = 300_000   # 5 minutes


# ─── Per-(symbol, timeframe) lock registry ──────────────────────────────────
_fetch_locks: dict[tuple[str, str], threading.Lock] = {}
_registry_guard = threading.Lock()


def _get_or_create_lock(symbol: str, timeframe: str) -> threading.Lock:
    """Return per-(symbol, timeframe) lock for in-process fetch dedup."""
    key = (symbol, timeframe)
    with _registry_guard:
        return _fetch_locks.setdefault(key, threading.Lock())


# ─── Rate limiter (minimal token bucket; compatible with existing project API) ──
class _RateLimiter:
    """Per-key token bucket. If the existing project rate limiter is available,
    substitute it here. This simple version refills tokens proportionally by
    elapsed time and blocks with a short sleep when empty."""

    def __init__(self):
        self._lock = threading.Lock()
        self._tokens: dict[str, float] = {}
        self._last_refill: dict[str, float] = {}

    def acquire(self, key: str, limit_per_min: int) -> None:
        while True:
            with self._lock:
                now = time.time()
                refill_rate = limit_per_min / 60.0  # tokens per second
                last = self._last_refill.get(key, now)
                self._tokens[key] = min(
                    limit_per_min,
                    self._tokens.get(key, limit_per_min) + (now - last) * refill_rate,
                )
                self._last_refill[key] = now
                if self._tokens[key] >= 1.0:
                    self._tokens[key] -= 1.0
                    return
                deficit = 1.0 - self._tokens[key]
                sleep_for = deficit / refill_rate
            time.sleep(min(sleep_for, 1.0))


_rate_limiter = _RateLimiter()
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_fetcher.py::TestLockRegistry -v`
Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add data/_fetcher.py tests/test_fetcher.py
git commit -m "feat(data): add fetcher scaffolding with lock registry and rate limiter"
```

---

### Task 11: `_fetcher.fetch_with_failover` — state machine + recovery probe

**Files:**
- Modify: `data/_fetcher.py`
- Modify: `tests/test_fetcher.py`

- [ ] **Step 1: Add failing tests for failover behavior**

Append to `tests/test_fetcher.py`:
```python
from data.providers.base import (
    ProviderRateLimited, ProviderTemporaryError, ProviderInvalidSymbol,
    AllProvidersFailedError,
)
from tests._fakes import make_bar


class TestFetchWithFailover:
    def test_primary_success(self, fake_providers):
        primary, fallback = fake_providers
        bars = [make_bar("BTCUSDT", "1h", 1000)]
        primary.set_bars("BTCUSDT", "1h", bars)
        result = _fetcher.fetch_with_failover("BTCUSDT", "1h", 0, 2000)
        assert len(result) == 1
        assert len(primary.calls) == 1
        assert len(fallback.calls) == 0

    def test_primary_temporary_error_triggers_counter(self, fake_providers):
        primary, fallback = fake_providers
        primary.set_error("BTCUSDT", "1h", ProviderTemporaryError("503"))
        fallback.set_bars("BTCUSDT", "1h", [make_bar("BTCUSDT", "1h", 1000)])
        result = _fetcher.fetch_with_failover("BTCUSDT", "1h", 0, 2000)
        assert len(result) == 1
        assert len(fallback.calls) == 1
        # One failure — not yet at threshold
        assert _fetcher._consecutive_failures == 0  # reset after fallback succeeded

    def test_threshold_triggers_sticky_switch(self, fake_providers):
        primary, fallback = fake_providers
        primary.set_error("BTCUSDT", "1h", ProviderRateLimited("429"))
        fallback.set_bars("BTCUSDT", "1h", [make_bar("BTCUSDT", "1h", 1000)])
        for _ in range(_fetcher.FAILOVER_THRESHOLD):
            _fetcher.fetch_with_failover("BTCUSDT", "1h", 0, 2000)
        assert _fetcher._active_idx == 1  # switched to fallback

    def test_invalid_symbol_does_not_trigger_failover(self, fake_providers):
        primary, fallback = fake_providers
        primary.set_error("FAKE", "1h", ProviderInvalidSymbol("not found"))
        with pytest.raises(ProviderInvalidSymbol):
            _fetcher.fetch_with_failover("FAKE", "1h", 0, 2000)
        assert _fetcher._active_idx == 0
        assert _fetcher._consecutive_failures == 0

    def test_all_providers_fail_raises(self, fake_providers):
        primary, fallback = fake_providers
        primary.set_error("BTCUSDT", "1h", ProviderTemporaryError("503"))
        fallback.set_error("BTCUSDT", "1h", ProviderTemporaryError("504"))
        with pytest.raises(AllProvidersFailedError):
            _fetcher.fetch_with_failover("BTCUSDT", "1h", 0, 2000)

    def test_recovery_probe_reverts_active(self, fake_providers, monkeypatch):
        primary, fallback = fake_providers
        # Force active_idx = 1 (fallback) and simulate probe interval elapsed
        _fetcher._active_idx = 1
        _fetcher._last_probe_ms = 0
        primary.healthy = True
        fallback.set_bars("BTCUSDT", "1h", [make_bar("BTCUSDT", "1h", 1000)])
        primary.set_bars("BTCUSDT", "1h", [make_bar("BTCUSDT", "1h", 1000)])
        _fetcher.fetch_with_failover("BTCUSDT", "1h", 0, 2000)
        assert _fetcher._active_idx == 0  # recovered
```

- [ ] **Step 2: Run tests to verify fail**

Run: `python -m pytest tests/test_fetcher.py::TestFetchWithFailover -v`
Expected: FAIL — `fetch_with_failover` not defined.

- [ ] **Step 3: Implement `fetch_with_failover` + recovery probe**

Append to `data/_fetcher.py`:
```python
def _maybe_probe_primary_recovery() -> None:
    """If we're on a fallback, probe primary health periodically; revert on success."""
    global _active_idx, _last_probe_ms
    with _state_lock:
        if _active_idx == 0:
            return
        now_ms = int(time.time() * 1000)
        if now_ms - _last_probe_ms < RECOVERY_PROBE_INTERVAL_MS:
            return
        _last_probe_ms = now_ms
        primary_to_probe = _PROVIDERS[0]

    healthy = False
    try:
        healthy = primary_to_probe.is_healthy()
    except Exception:
        pass
    if healthy:
        with _state_lock:
            _active_idx = 0
        metrics.inc("provider_recoveries_total", labels={"provider": primary_to_probe.name})
        log.info("Primary provider %s recovered — reverting active", primary_to_probe.name)


def fetch_with_failover(symbol: str, timeframe: str, start_ms: int, end_ms: int) -> list[Bar]:
    """Try providers in priority order (sticky). On failure thresholds, switch active."""
    global _active_idx, _consecutive_failures

    _maybe_probe_primary_recovery()

    with _state_lock:
        ordering = list(range(_active_idx, len(_PROVIDERS))) + list(range(_active_idx))
        primary_name = _PROVIDERS[ordering[0]].name

    for position, idx in enumerate(ordering):
        provider = _PROVIDERS[idx]
        try:
            _rate_limiter.acquire(provider.name, provider.rate_limit_per_min)
            t0 = time.time()
            bars = provider.fetch_klines(symbol, timeframe, start_ms, end_ms)
            latency_ms = int((time.time() - t0) * 1000)
            metrics.observe("fetch_latency_ms", latency_ms, labels={"provider": provider.name})
            metrics.inc("fetches_total", labels={"provider": provider.name, "tf": timeframe})
            with _state_lock:
                _consecutive_failures = 0
            if position > 0:
                metrics.inc(
                    "fallback_fetches_total",
                    labels={"from": primary_name, "to": provider.name},
                )
            return bars
        except ProviderInvalidSymbol:
            raise
        except (ProviderRateLimited, ProviderTemporaryError) as e:
            metrics.inc(
                "provider_errors_total",
                labels={"provider": provider.name, "kind": type(e).__name__},
            )
            log.warning("%s failed (%s): %s", provider.name, type(e).__name__, e)
            if position == 0:
                with _state_lock:
                    _consecutive_failures += 1
                    if _consecutive_failures >= FAILOVER_THRESHOLD:
                        new_idx = (idx + 1) % len(_PROVIDERS)
                        metrics.inc(
                            "provider_switches_total",
                            labels={"from": provider.name, "to": _PROVIDERS[new_idx].name},
                        )
                        log.warning(
                            "Switching active provider %s → %s after %d consecutive failures",
                            provider.name, _PROVIDERS[new_idx].name, _consecutive_failures,
                        )
                        _active_idx = new_idx
                        _consecutive_failures = 0
            continue

    raise AllProvidersFailedError(
        f"All providers failed for {symbol} {timeframe} [{start_ms}, {end_ms}]"
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_fetcher.py -v`
Expected: all fetcher tests PASS.

- [ ] **Step 5: Commit**

```bash
git add data/_fetcher.py tests/test_fetcher.py
git commit -m "feat(data): add fetch_with_failover with sticky state and recovery probe"
```

---

### Task 12: `_fetcher.ensure_fresh` + `_backfill_range` + `_fill_internal_gaps`

**Files:**
- Modify: `data/_fetcher.py`
- Modify: `tests/test_fetcher.py`

- [ ] **Step 1: Add failing tests for orchestration functions**

Append to `tests/test_fetcher.py`:
```python
class TestEnsureFresh:
    def test_cold_fetches_limit_bars(self, tmp_ohlcv_db, fake_provider):
        bars = [make_bar("BTCUSDT", "1h", t * 3600_000) for t in range(10)]
        fake_provider.set_bars("BTCUSDT", "1h", bars)
        _fetcher.ensure_fresh("BTCUSDT", "1h", limit=5, cached_max=None, expected_max=9 * 3600_000)
        stored = _storage._conn().execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]
        # Requested last 5 bars: open_times 5..9 inclusive
        assert stored == 5
        got = _storage.tail("BTCUSDT", "1h", 100)
        assert list(got["open_time"]) == [5 * 3600_000, 6 * 3600_000, 7 * 3600_000, 8 * 3600_000, 9 * 3600_000]

    def test_warm_fetches_only_increment(self, tmp_ohlcv_db, fake_provider):
        bars = [make_bar("BTCUSDT", "1h", t * 3600_000) for t in range(10)]
        fake_provider.set_bars("BTCUSDT", "1h", bars)
        _storage.upsert_many([bars[i] for i in range(5)])  # cached up to 4
        _fetcher.ensure_fresh("BTCUSDT", "1h", limit=10, cached_max=4 * 3600_000, expected_max=9 * 3600_000)
        # Only bars 5..9 were newly requested
        assert fake_provider.calls[-1] == ("BTCUSDT", "1h", 5 * 3600_000, 9 * 3600_000)

    def test_double_checked_lock_dedup(self, tmp_ohlcv_db, fake_provider):
        bars = [make_bar("BTCUSDT", "1h", t * 3600_000) for t in range(10)]
        fake_provider.set_bars("BTCUSDT", "1h", bars)
        # Two threads calling ensure_fresh simultaneously
        results = []
        def worker():
            _fetcher.ensure_fresh("BTCUSDT", "1h", limit=5, cached_max=None, expected_max=9 * 3600_000)
            results.append("done")
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert len(results) == 4
        # With double-checked locking, first thread fetches; others see fresh cache and return
        assert len(fake_provider.calls) == 1


class TestBackfillRange:
    def test_full_backfill(self, tmp_ohlcv_db, fake_provider):
        bars = [make_bar("BTCUSDT", "1h", t * 3600_000) for t in range(100)]
        fake_provider.set_bars("BTCUSDT", "1h", bars)
        n = _fetcher._backfill_range("BTCUSDT", "1h", 0, 99 * 3600_000)
        assert n == 100
        assert _storage.max_open_time("BTCUSDT", "1h") == 99 * 3600_000

    def test_chunks_respect_size(self, tmp_ohlcv_db, fake_provider, monkeypatch):
        # CHUNK_SIZE=1000 → 1500 bars = 2 chunks
        bars = [make_bar("BTCUSDT", "1h", t * 3600_000) for t in range(1500)]
        fake_provider.set_bars("BTCUSDT", "1h", bars)
        _fetcher._backfill_range("BTCUSDT", "1h", 0, 1499 * 3600_000)
        # 2 chunks = 2 provider calls
        assert len(fake_provider.calls) == 2

    def test_pre_listing_stops_and_marks_earliest(self, tmp_ohlcv_db, fake_provider):
        # Provider has data starting at t=500*3600_000 only
        bars = [make_bar("BTCUSDT", "1h", t * 3600_000) for t in range(500, 600)]
        fake_provider.set_bars("BTCUSDT", "1h", bars)
        _fetcher._backfill_range("BTCUSDT", "1h", 0, 100 * 3600_000)
        # Our requested range [0, 100] is entirely pre-listing → empty response → stop + mark earliest
        assert _storage.first_bar_ms("BTCUSDT", "1h") is not None


class TestFillInternalGaps:
    def test_fills_single_gap(self, tmp_ohlcv_db, fake_provider):
        all_bars = [make_bar("BTCUSDT", "1h", t * 3600_000) for t in range(10)]
        fake_provider.set_bars("BTCUSDT", "1h", all_bars)
        # Seed storage with bars 0-3 and 7-9 (gap in the middle: 4-6)
        _storage.upsert_many([all_bars[i] for i in [0, 1, 2, 3, 7, 8, 9]])
        fake_provider.calls.clear()
        _fetcher._fill_internal_gaps("BTCUSDT", "1h", 0, 9 * 3600_000)
        assert _storage.max_open_time("BTCUSDT", "1h") == 9 * 3600_000
        count = _storage._conn().execute(
            "SELECT COUNT(*) FROM ohlcv WHERE symbol='BTCUSDT' AND timeframe='1h'").fetchone()[0]
        assert count == 10
        # Should have fetched only the gap range (4..6 inclusive)
        assert fake_provider.calls[0][2] == 4 * 3600_000
        assert fake_provider.calls[0][3] == 6 * 3600_000
```

- [ ] **Step 2: Run tests to verify fail**

Run: `python -m pytest tests/test_fetcher.py::TestEnsureFresh tests/test_fetcher.py::TestBackfillRange tests/test_fetcher.py::TestFillInternalGaps -v`
Expected: FAIL — functions not defined.

- [ ] **Step 3: Add orchestration functions to `data/_fetcher.py`**

Append:
```python
CHUNK_SIZE = 1000


def ensure_fresh(
    symbol: str, timeframe: str, limit: int,
    cached_max: int | None, expected_max: int,
) -> None:
    """Fetch incremental bars if cache is stale, using double-checked locking."""
    lock = _get_or_create_lock(symbol, timeframe)
    with lock:
        # Re-check cache inside lock — another thread may have just filled it
        new_cached_max = _storage.max_open_time(symbol, timeframe)
        new_count = _storage.count_tail(symbol, timeframe, expected_max, limit)
        if (
            new_cached_max is not None
            and new_cached_max >= expected_max
            and new_count >= limit
        ):
            metrics.inc("double_checked_hits_total")
            return

        delta = delta_ms(timeframe)
        if new_cached_max is None:
            start_ms = expected_max - (limit - 1) * delta
        else:
            start_ms = new_cached_max + delta
        end_ms = expected_max

        if start_ms > end_ms:
            return

        bars = fetch_with_failover(symbol, timeframe, start_ms, end_ms)
        if bars:
            _storage.upsert_many(bars)


def _backfill_range(symbol: str, timeframe: str, start_ms: int, end_ms: int) -> int:
    """Bulk fetch + persist of [start_ms, end_ms] inclusive in CHUNK_SIZE-bar chunks."""
    delta = delta_ms(timeframe)
    earliest = _storage.first_bar_ms(symbol, timeframe)
    if earliest is not None:
        start_ms = max(start_ms, earliest)
    if start_ms > end_ms:
        return 0

    cur = start_ms
    total = 0
    estimated = max(1, (end_ms - start_ms) // delta + 1)
    while cur <= end_ms:
        chunk_end = min(cur + (CHUNK_SIZE - 1) * delta, end_ms)
        bars = fetch_with_failover(symbol, timeframe, cur, chunk_end)
        if not bars:
            # Empty response — mark pre-listing and stop
            _storage.set_first_bar_ms(symbol, timeframe, chunk_end + delta)
            break
        persisted = _storage.upsert_many(bars)
        total += persisted
        cur = bars[-1].open_time + delta
        if total > 0 and total % 1000 == 0:
            log.info(
                "Backfill %s %s: %d/%d (%.1f%%)",
                symbol, timeframe, total, estimated, total / estimated * 100.0,
            )
    metrics.inc("backfill_bars_total", total, labels={"symbol": symbol, "tf": timeframe})
    return total


def _fill_internal_gaps(symbol: str, timeframe: str, start_ms: int, end_ms: int) -> int:
    """Detect and fill holes inside [start_ms, end_ms] inclusive."""
    delta = delta_ms(timeframe)
    existing = set(_storage.times_in_range(symbol, timeframe, start_ms, end_ms))
    total = 0
    gap_start = None
    cur = start_ms
    while cur <= end_ms:
        if cur not in existing:
            if gap_start is None:
                gap_start = cur
        else:
            if gap_start is not None:
                total += _backfill_range(symbol, timeframe, gap_start, cur - delta)
                gap_start = None
        cur += delta
    if gap_start is not None:
        total += _backfill_range(symbol, timeframe, gap_start, end_ms)
    return total
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_fetcher.py -v`
Expected: all fetcher tests PASS.

- [ ] **Step 5: Commit**

```bash
git add data/_fetcher.py tests/test_fetcher.py
git commit -m "feat(data): add ensure_fresh, _backfill_range, _fill_internal_gaps"
```

---

## Phase 5 — Public API

### Task 13: `data/market_data.py` — `get_klines` + `get_klines_live`

**Files:**
- Create: `data/market_data.py` (initial)
- Create: `tests/test_market_data.py` (initial)

- [ ] **Step 1: Write failing tests**

Create `tests/test_market_data.py`:
```python
from datetime import datetime, timezone
import pytest
from data import market_data as md
from data import _storage, _fetcher
from data.timeframes import last_closed_bar_time, delta_ms
from tests._fakes import make_bar


def _seed(fake, symbol, tf, count, delta_hours=1):
    bars = [make_bar(symbol, tf, t * delta_hours * 3600_000, price=100.0 + t) for t in range(count)]
    fake.set_bars(symbol, tf, bars)
    return bars


class TestGetKlines:
    def test_cold_fetches_limit(self, tmp_ohlcv_db, fake_provider, monkeypatch):
        # Freeze "now" such that expected_max = 9 * 3600_000 (last closed 1h bar)
        def fake_last_closed(tf, now=None):
            return 9 * 3600_000
        monkeypatch.setattr(md, "last_closed_bar_time", fake_last_closed)
        monkeypatch.setattr(_fetcher, "last_closed_bar_time", fake_last_closed)
        _seed(fake_provider, "BTCUSDT", "1h", 10)
        df = md.get_klines("BTCUSDT", "1h", 5)
        assert len(df) == 5
        assert list(df["open_time"]) == [5 * 3600_000, 6 * 3600_000, 7 * 3600_000, 8 * 3600_000, 9 * 3600_000]

    def test_warm_no_fetch(self, tmp_ohlcv_db, fake_provider, monkeypatch):
        monkeypatch.setattr(md, "last_closed_bar_time", lambda tf, now=None: 9 * 3600_000)
        monkeypatch.setattr(_fetcher, "last_closed_bar_time", lambda tf, now=None: 9 * 3600_000)
        bars = _seed(fake_provider, "BTCUSDT", "1h", 10)
        _storage.upsert_many(bars)
        fake_provider.calls.clear()
        df = md.get_klines("BTCUSDT", "1h", 5)
        assert len(df) == 5
        assert fake_provider.calls == []

    def test_force_refresh_bypasses_cache(self, tmp_ohlcv_db, fake_provider, monkeypatch):
        monkeypatch.setattr(md, "last_closed_bar_time", lambda tf, now=None: 9 * 3600_000)
        monkeypatch.setattr(_fetcher, "last_closed_bar_time", lambda tf, now=None: 9 * 3600_000)
        bars = _seed(fake_provider, "BTCUSDT", "1h", 10)
        _storage.upsert_many(bars)
        fake_provider.calls.clear()
        md.get_klines("BTCUSDT", "1h", 5, force_refresh=True)
        assert len(fake_provider.calls) >= 1


class TestGetKlinesLive:
    def test_bypasses_cache_includes_current(self, tmp_ohlcv_db, fake_provider, monkeypatch):
        # In-progress bar is "current"; live returns everything provider gives.
        bars = [make_bar("BTCUSDT", "1h", t * 3600_000) for t in range(5)]
        fake_provider.set_bars("BTCUSDT", "1h", bars)
        df = md.get_klines_live("BTCUSDT", "1h", 5)
        assert len(df) == 5
        # Nothing was persisted to the DB
        count = _storage._conn().execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]
        assert count == 0
```

- [ ] **Step 2: Run tests to verify fail**

Run: `python -m pytest tests/test_market_data.py -v`
Expected: FAIL with ImportError.

- [ ] **Step 3: Implement initial `data/market_data.py`**

```python
"""Market Data Layer — public API.

All functions in this module are the only supported entrypoints.
Underscore-prefixed modules are private implementation.
"""
from datetime import datetime, timezone
from typing import Iterable
import logging

import pandas as pd

from data import _storage, _fetcher, metrics
from data.timeframes import TIMEFRAMES, delta_ms, last_closed_bar_time


log = logging.getLogger("data.market")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _ensure_schema_once():
    _storage.init_schema()


def get_klines(
    symbol: str,
    timeframe: str,
    limit: int,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """Last `limit` CLOSED bars for (symbol, timeframe). Never includes in-progress bar.

    Serves from cache; fetches incremental bars when stale. Column schema:
    ['open_time', 'open', 'high', 'low', 'close', 'volume', 'provider', 'fetched_at'].
    """
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unknown timeframe: {timeframe}")
    if limit <= 0:
        raise ValueError("limit must be positive")
    _ensure_schema_once()

    expected_max = last_closed_bar_time(timeframe, _utcnow())
    cached_max = _storage.max_open_time(symbol, timeframe)
    cached_count = _storage.count_tail(symbol, timeframe, expected_max, limit)
    sufficient = (
        cached_max is not None
        and cached_max >= expected_max
        and cached_count >= limit
    )
    if force_refresh or not sufficient:
        _fetcher.ensure_fresh(symbol, timeframe, limit, cached_max, expected_max)
    else:
        metrics.inc("cache_hits_total", labels={"tf": timeframe})

    return _storage.tail(symbol, timeframe, limit)


def get_klines_live(
    symbol: str,
    timeframe: str,
    limit: int,
) -> pd.DataFrame:
    """Last `limit` bars INCLUDING the in-progress bar. Bypasses cache fully.

    Only legitimate consumer: /ohlcv endpoint for animated chart. Does NOT persist.
    """
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unknown timeframe: {timeframe}")
    if limit <= 0:
        raise ValueError("limit must be positive")

    d = delta_ms(timeframe)
    now_ms = int(_utcnow().timestamp() * 1000)
    # Current (in-progress) bar open_time:
    current_open_time = (now_ms // d) * d
    start_ms = current_open_time - (limit - 1) * d
    end_ms = current_open_time

    bars = _fetcher.fetch_with_failover(symbol, timeframe, start_ms, end_ms)
    return _bars_to_df(bars)


def _bars_to_df(bars) -> pd.DataFrame:
    cols = ["open_time", "open", "high", "low", "close", "volume", "provider", "fetched_at"]
    return pd.DataFrame(
        [(b.open_time, b.open, b.high, b.low, b.close, b.volume, b.provider, b.fetched_at) for b in bars],
        columns=cols,
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_market_data.py -v`
Expected: all get_klines + get_klines_live tests PASS.

- [ ] **Step 5: Commit**

```bash
git add data/market_data.py tests/test_market_data.py
git commit -m "feat(data): add public get_klines and get_klines_live"
```

---

### Task 14: `data/market_data.py` — `get_klines_range` with gap detection

**Files:**
- Modify: `data/market_data.py`
- Modify: `tests/test_market_data.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_market_data.py`:
```python
class TestGetKlinesRange:
    def test_cache_hit_no_fetch(self, tmp_ohlcv_db, fake_provider, monkeypatch):
        monkeypatch.setattr(md, "last_closed_bar_time", lambda tf, now=None: 9 * 3600_000)
        bars = [make_bar("BTCUSDT", "1h", t * 3600_000) for t in range(10)]
        _storage.upsert_many(bars)
        fake_provider.calls.clear()
        df = md.get_klines_range(
            "BTCUSDT", "1h",
            datetime(1970, 1, 1, 0, 0, tzinfo=timezone.utc),
            datetime(1970, 1, 1, 9, 0, tzinfo=timezone.utc),
        )
        assert len(df) == 10
        assert fake_provider.calls == []

    def test_cold_backfills_whole_range(self, tmp_ohlcv_db, fake_provider, monkeypatch):
        monkeypatch.setattr(md, "last_closed_bar_time", lambda tf, now=None: 9 * 3600_000)
        bars = [make_bar("BTCUSDT", "1h", t * 3600_000) for t in range(10)]
        fake_provider.set_bars("BTCUSDT", "1h", bars)
        df = md.get_klines_range(
            "BTCUSDT", "1h",
            datetime(1970, 1, 1, 0, 0, tzinfo=timezone.utc),
            datetime(1970, 1, 1, 9, 0, tzinfo=timezone.utc),
        )
        assert len(df) == 10

    def test_left_edge_gap_filled(self, tmp_ohlcv_db, fake_provider, monkeypatch):
        monkeypatch.setattr(md, "last_closed_bar_time", lambda tf, now=None: 9 * 3600_000)
        all_bars = [make_bar("BTCUSDT", "1h", t * 3600_000) for t in range(10)]
        fake_provider.set_bars("BTCUSDT", "1h", all_bars)
        # Cache has only bars 5..9
        _storage.upsert_many(all_bars[5:])
        fake_provider.calls.clear()
        df = md.get_klines_range(
            "BTCUSDT", "1h",
            datetime(1970, 1, 1, 0, 0, tzinfo=timezone.utc),
            datetime(1970, 1, 1, 9, 0, tzinfo=timezone.utc),
        )
        assert len(df) == 10
        # Left edge fetch: [0, 4]
        assert fake_provider.calls[0][2] == 0
        assert fake_provider.calls[0][3] == 4 * 3600_000
```

- [ ] **Step 2: Run tests to verify fail**

Run: `python -m pytest tests/test_market_data.py::TestGetKlinesRange -v`
Expected: FAIL — `get_klines_range` not defined.

- [ ] **Step 3: Add `get_klines_range` to `data/market_data.py`**

Append:
```python
def get_klines_range(
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    """Closed bars with open_time in [start, end] inclusive (clamped to last closed bar).

    Auto-detects gaps in the cache and backfills only what's missing.
    Raises AllProvidersFailedError if a gap cannot be filled.
    """
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unknown timeframe: {timeframe}")
    _ensure_schema_once()

    d = delta_ms(timeframe)
    start_ms = _to_ms(start)
    end_ms = last_closed_bar_time(timeframe, end)

    # Clamp start to known first bar
    earliest = _storage.first_bar_ms(symbol, timeframe)
    if earliest is not None and start_ms < earliest:
        start_ms = earliest

    if start_ms > end_ms:
        return _storage.range_(symbol, timeframe, start_ms, end_ms)

    expected_count = (end_ms - start_ms) // d + 1
    min_t, max_t, count = _storage.range_stats(symbol, timeframe, start_ms, end_ms)

    if count == expected_count:
        return _storage.range_(symbol, timeframe, start_ms, end_ms)

    if count == 0:
        _fetcher._backfill_range(symbol, timeframe, start_ms, end_ms)
    else:
        if min_t > start_ms:
            _fetcher._backfill_range(symbol, timeframe, start_ms, min_t - d)
        if max_t < end_ms:
            _fetcher._backfill_range(symbol, timeframe, max_t + d, end_ms)
        # Re-check; run internal gap fill if still short
        _, _, count2 = _storage.range_stats(symbol, timeframe, start_ms, end_ms)
        if count2 < expected_count:
            _fetcher._fill_internal_gaps(symbol, timeframe, start_ms, end_ms)

    return _storage.range_(symbol, timeframe, start_ms, end_ms)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_market_data.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add data/market_data.py tests/test_market_data.py
git commit -m "feat(data): add get_klines_range with gap detection and auto-backfill"
```

---

### Task 15: `data/market_data.py` — `prefetch`, `backfill`, `repair`

**Files:**
- Modify: `data/market_data.py`
- Modify: `tests/test_market_data.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_market_data.py`:
```python
class TestPrefetch:
    def test_parallel_cache_fill(self, tmp_ohlcv_db, fake_provider, monkeypatch):
        monkeypatch.setattr(md, "last_closed_bar_time", lambda tf, now=None: 9 * 3600_000)
        monkeypatch.setattr(_fetcher, "last_closed_bar_time", lambda tf, now=None: 9 * 3600_000)
        for sym in ["BTCUSDT", "ETHUSDT"]:
            _seed(fake_provider, sym, "1h", 10)
            _seed(fake_provider, sym, "4h", 10)
        md.prefetch(["BTCUSDT", "ETHUSDT"], ["1h", "4h"], limit=5)
        # After prefetch, each (sym, tf) should have data cached
        for sym in ["BTCUSDT", "ETHUSDT"]:
            for tf in ["1h", "4h"]:
                assert _storage.max_open_time(sym, tf) is not None

    def test_exception_does_not_abort_batch(self, tmp_ohlcv_db, fake_provider, monkeypatch):
        from data.providers.base import ProviderInvalidSymbol
        monkeypatch.setattr(md, "last_closed_bar_time", lambda tf, now=None: 9 * 3600_000)
        monkeypatch.setattr(_fetcher, "last_closed_bar_time", lambda tf, now=None: 9 * 3600_000)
        _seed(fake_provider, "GOODCOIN", "1h", 10)
        fake_provider.set_error("BADCOIN", "1h", ProviderInvalidSymbol("not listed"))
        md.prefetch(["GOODCOIN", "BADCOIN"], ["1h"], limit=5)
        assert _storage.max_open_time("GOODCOIN", "1h") is not None
        assert _storage.max_open_time("BADCOIN", "1h") is None


class TestBackfill:
    def test_idempotent(self, tmp_ohlcv_db, fake_provider):
        bars = [make_bar("BTCUSDT", "1h", t * 3600_000) for t in range(50)]
        fake_provider.set_bars("BTCUSDT", "1h", bars)
        md.backfill("BTCUSDT", "1h",
                    datetime(1970, 1, 1, 0, 0, tzinfo=timezone.utc),
                    datetime(1970, 1, 1, 49, 0, tzinfo=timezone.utc) - (datetime(1970, 1, 1, 49, 0, tzinfo=timezone.utc) - datetime(1970, 1, 1, 49, 0, tzinfo=timezone.utc)))
        count1 = _storage._conn().execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]
        fake_provider.calls.clear()
        md.backfill("BTCUSDT", "1h",
                    datetime(1970, 1, 1, 0, 0, tzinfo=timezone.utc),
                    datetime(1970, 1, 1, 49, 0, tzinfo=timezone.utc))
        count2 = _storage._conn().execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]
        assert count1 == count2  # no duplicates


class TestRepair:
    def test_overwrites_existing_bars(self, tmp_ohlcv_db, fake_provider):
        original = [make_bar("BTCUSDT", "1h", t * 3600_000, price=100.0) for t in range(10)]
        revised = [make_bar("BTCUSDT", "1h", t * 3600_000, price=200.0) for t in range(10)]
        _storage.upsert_many(original)
        fake_provider.set_bars("BTCUSDT", "1h", revised)
        md.repair("BTCUSDT", "1h",
                  datetime(1970, 1, 1, 0, 0, tzinfo=timezone.utc),
                  datetime(1970, 1, 1, 9, 0, tzinfo=timezone.utc))
        rows = _storage._conn().execute(
            "SELECT close FROM ohlcv WHERE symbol='BTCUSDT' AND timeframe='1h' ORDER BY open_time").fetchall()
        assert all(r[0] == 200.0 for r in rows)
```

- [ ] **Step 2: Run tests to verify fail**

Run: `python -m pytest tests/test_market_data.py -v`
Expected: FAIL — functions not defined.

- [ ] **Step 3: Add functions to `data/market_data.py`**

Append:
```python
from concurrent.futures import ThreadPoolExecutor, as_completed


MAX_PARALLEL_FETCH = 5


def prefetch(
    symbols: Iterable[str],
    timeframes: Iterable[str],
    limit: int = 210,
) -> None:
    """Batch-prefetch cache entries for all (symbol, timeframe) combinations in parallel.

    Internal workers call get_klines, so all freshness/locking semantics are preserved.
    Per-(sym, tf) failures are logged and recorded as metrics but do NOT abort the batch.
    """
    tasks = [(s, tf) for s in symbols for tf in timeframes]
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_FETCH) as ex:
        futures = {ex.submit(get_klines, s, tf, limit): (s, tf) for s, tf in tasks}
        for fut in as_completed(futures):
            s, tf = futures[fut]
            try:
                fut.result()
            except Exception as e:
                log.warning("Prefetch failed for %s/%s: %s", s, tf, e)
                metrics.inc("prefetch_errors_total", labels={"symbol": s, "tf": tf})


def backfill(
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime | None = None,
) -> int:
    """Explicit bulk historical fetch + persist. Idempotent, resumable, pre-listing-aware."""
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unknown timeframe: {timeframe}")
    _ensure_schema_once()

    end = end or _utcnow()
    end_ms = last_closed_bar_time(timeframe, end)
    start_ms = _to_ms(start)
    return _fetcher._backfill_range(symbol, timeframe, start_ms, end_ms)


def repair(
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime | None = None,
) -> int:
    """Force re-fetch + overwrite of a range. Use when data anomaly is detected.

    Internally reuses _backfill_range; INSERT OR REPLACE semantics overwrite existing bars.
    """
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unknown timeframe: {timeframe}")
    _ensure_schema_once()

    end = end or _utcnow()
    end_ms = last_closed_bar_time(timeframe, end)
    start_ms = _to_ms(start)

    # Clear symbol_earliest record so we re-fetch even if the range overlaps pre-listing marker
    # (we trust the explicit repair intent)
    metrics.inc("repairs_requested_total", labels={"symbol": symbol, "tf": timeframe})
    before_count = _storage.range_stats(symbol, timeframe, start_ms, end_ms)[2]
    persisted = _fetcher._backfill_range(symbol, timeframe, start_ms, end_ms)
    after_count = _storage.range_stats(symbol, timeframe, start_ms, end_ms)[2]
    metrics.inc("bars_overwritten_total", max(0, persisted - (after_count - before_count)),
                labels={"symbol": symbol, "tf": timeframe})
    return persisted


def get_stats() -> dict:
    """Snapshot of market data metrics. Exposed via /status endpoint integration."""
    return metrics.get_stats()
```

- [ ] **Step 4: Fix test for backfill that had a datetime arithmetic typo**

Edit `tests/test_market_data.py` `TestBackfill.test_idempotent` to be cleaner:
```python
    def test_idempotent(self, tmp_ohlcv_db, fake_provider):
        bars = [make_bar("BTCUSDT", "1h", t * 3600_000) for t in range(50)]
        fake_provider.set_bars("BTCUSDT", "1h", bars)
        start = datetime(1970, 1, 1, 0, 0, tzinfo=timezone.utc)
        end = datetime(1970, 1, 1, 49, 0, tzinfo=timezone.utc)
        md.backfill("BTCUSDT", "1h", start, end)
        count1 = _storage._conn().execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]
        md.backfill("BTCUSDT", "1h", start, end)
        count2 = _storage._conn().execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]
        assert count1 == count2
        assert count1 >= 1
```

- [ ] **Step 5: Run tests to verify pass**

Run: `python -m pytest tests/test_market_data.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add data/market_data.py tests/test_market_data.py
git commit -m "feat(data): add prefetch, backfill, repair, get_stats"
```

---

### Task 16: Public re-exports, CLI module, and integration tests

**Files:**
- Modify: `data/__init__.py`
- Create: `data/cli.py`
- Create: `tests/test_market_data_integration.py`

- [ ] **Step 1: Update `data/__init__.py`**

```python
"""Market Data Layer — unified OHLCV cache + fetch for all modules.

Public entrypoints:
    get_klines(symbol, timeframe, limit, force_refresh=False) -> DataFrame
    get_klines_range(symbol, timeframe, start, end)          -> DataFrame
    get_klines_live(symbol, timeframe, limit)                -> DataFrame
    prefetch(symbols, timeframes, limit=210)                 -> None
    backfill(symbol, timeframe, start, end=None)             -> int
    repair(symbol, timeframe, start, end=None)               -> int

Utilities:
    get_stats()                                              -> dict
    last_closed_bar_time(timeframe, now=None)                -> int ms

See docs/superpowers/specs/en/2026-04-18-market-data-layer-design.md
"""
from data.market_data import (
    get_klines,
    get_klines_range,
    get_klines_live,
    prefetch,
    backfill,
    repair,
    get_stats,
)
from data.timeframes import last_closed_bar_time

__all__ = [
    "get_klines", "get_klines_range", "get_klines_live",
    "prefetch", "backfill", "repair",
    "get_stats", "last_closed_bar_time",
]
```

- [ ] **Step 2: Create `data/cli.py`**

```python
"""Convenience CLI: python -m data.cli {backfill, repair, stats, init}"""
import argparse
import json
import sys
from datetime import datetime, timezone

from data import market_data as md
from data import _storage


def _parse_date(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc) if "+" not in s and "Z" not in s \
        else datetime.fromisoformat(s.replace("Z", "+00:00"))


def cmd_backfill(args):
    start = _parse_date(args.start)
    end = _parse_date(args.end) if args.end else None
    n = md.backfill(args.symbol, args.timeframe, start, end)
    print(f"Backfilled {n} bars for {args.symbol} {args.timeframe}")


def cmd_repair(args):
    start = _parse_date(args.start)
    end = _parse_date(args.end) if args.end else None
    n = md.repair(args.symbol, args.timeframe, start, end)
    print(f"Repaired {n} bars for {args.symbol} {args.timeframe}")


def cmd_stats(args):
    stats = md.get_stats()
    print(json.dumps(stats, indent=2, default=str))


def cmd_init(args):
    _storage.init_schema()
    print(f"Schema initialized at {_storage.DB_PATH}")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m data.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p_b = sub.add_parser("backfill", help="Bulk historical fetch")
    p_b.add_argument("symbol"); p_b.add_argument("timeframe")
    p_b.add_argument("start"); p_b.add_argument("end", nargs="?")
    p_b.set_defaults(func=cmd_backfill)

    p_r = sub.add_parser("repair", help="Force re-fetch overwriting a range")
    p_r.add_argument("symbol"); p_r.add_argument("timeframe")
    p_r.add_argument("start"); p_r.add_argument("end", nargs="?")
    p_r.set_defaults(func=cmd_repair)

    p_s = sub.add_parser("stats", help="Print metrics snapshot")
    p_s.set_defaults(func=cmd_stats)

    p_i = sub.add_parser("init", help="Create ohlcv.db with schema (usually auto)")
    p_i.set_defaults(func=cmd_init)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
```

- [ ] **Step 3: Create `tests/test_market_data_integration.py`**

```python
"""End-to-end scenarios using FakeProvider + tmp_ohlcv_db."""
from datetime import datetime, timezone
import threading
import pytest
from data import market_data as md
from data import _storage, _fetcher
from tests._fakes import make_bar


class TestScannerCycleSimulation:
    def test_prefetch_then_get_klines_cache_hit(self, tmp_ohlcv_db, fake_provider, monkeypatch):
        monkeypatch.setattr(md, "last_closed_bar_time", lambda tf, now=None: 9 * 3600_000)
        monkeypatch.setattr(_fetcher, "last_closed_bar_time", lambda tf, now=None: 9 * 3600_000)
        for sym in ["BTCUSDT", "ETHUSDT"]:
            for tf in ["1h", "4h"]:
                bars = [make_bar(sym, tf, t * 3600_000) for t in range(10)]
                fake_provider.set_bars(sym, tf, bars)
        md.prefetch(["BTCUSDT", "ETHUSDT"], ["1h", "4h"], limit=5)
        fake_provider.calls.clear()
        for sym in ["BTCUSDT", "ETHUSDT"]:
            for tf in ["1h", "4h"]:
                df = md.get_klines(sym, tf, 5)
                assert len(df) == 5
        # Zero fetches after prefetch
        assert len(fake_provider.calls) == 0


class TestBackfillAndRangeQuery:
    def test_backfill_then_range_cache_hit(self, tmp_ohlcv_db, fake_provider):
        bars = [make_bar("BTCUSDT", "1h", t * 3600_000) for t in range(100)]
        fake_provider.set_bars("BTCUSDT", "1h", bars)
        md.backfill(
            "BTCUSDT", "1h",
            datetime(1970, 1, 1, tzinfo=timezone.utc),
            datetime(1970, 1, 1, 99, 0, tzinfo=timezone.utc),
        )
        fake_provider.calls.clear()
        df = md.get_klines_range(
            "BTCUSDT", "1h",
            datetime(1970, 1, 1, 10, 0, tzinfo=timezone.utc),
            datetime(1970, 1, 1, 90, 0, tzinfo=timezone.utc),
        )
        assert len(df) == 81
        assert len(fake_provider.calls) == 0


class TestConcurrentScanCycles:
    def test_many_threads_dedup_fetches(self, tmp_ohlcv_db, fake_provider, monkeypatch):
        monkeypatch.setattr(md, "last_closed_bar_time", lambda tf, now=None: 9 * 3600_000)
        monkeypatch.setattr(_fetcher, "last_closed_bar_time", lambda tf, now=None: 9 * 3600_000)
        bars = [make_bar("BTCUSDT", "1h", t * 3600_000) for t in range(10)]
        fake_provider.set_bars("BTCUSDT", "1h", bars)
        results = []
        def worker():
            df = md.get_klines("BTCUSDT", "1h", 5)
            results.append(len(df))
        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert all(n == 5 for n in results)
        # Cold start — expected 1 actual fetch, dedup handles the rest
        assert len(fake_provider.calls) == 1


class TestResumableBackfill:
    def test_partial_backfill_then_restart(self, tmp_ohlcv_db, fake_provider):
        bars = [make_bar("BTCUSDT", "1h", t * 3600_000) for t in range(50)]
        fake_provider.set_bars("BTCUSDT", "1h", bars)
        # Simulate partial: seed only bars 0..24
        _storage.upsert_many(bars[:25])
        fake_provider.calls.clear()
        md.backfill(
            "BTCUSDT", "1h",
            datetime(1970, 1, 1, tzinfo=timezone.utc),
            datetime(1970, 1, 1, 49, 0, tzinfo=timezone.utc),
        )
        total = _storage._conn().execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]
        assert total == 50


class TestCLI:
    def test_init_creates_db(self, tmp_ohlcv_db):
        from data import cli
        cli.main(["init"])
        import os
        assert os.path.exists(_storage.DB_PATH)

    def test_stats_prints_json(self, tmp_ohlcv_db, capsys):
        from data import cli
        cli.main(["stats"])
        out = capsys.readouterr().out
        import json
        data = json.loads(out)
        assert "counters" in data
```

- [ ] **Step 4: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: all tests PASS. Confirm coverage if using coverage tools (optional, requires `pip install coverage`):
```bash
coverage run -m pytest tests/ && coverage report --include='data/*'
```

- [ ] **Step 5: Commit**

```bash
git add data/__init__.py data/cli.py tests/test_market_data_integration.py
git commit -m "feat(data): add public re-exports, CLI module, integration tests"
```

---

## Phase 6 — Scanner migration

### Task 17: Wire `data.market_data` into `btc_scanner.py`

**Files:**
- Modify: `btc_scanner.py`

- [ ] **Step 1: Read current fetch logic in scanner**

```bash
grep -n '^def get_klines\|^def _get_klines\|_active_provider' btc_scanner.py | head -20
```

Note the line numbers of `get_klines`, `_active_provider`, `_get_klines_binance`, `_get_klines_bybit`.

- [ ] **Step 2: Add `data.market_data` import and prefetch call at scan cycle start**

At the top imports section, add:
```python
from data import market_data as md
```

Locate `scanner_loop()` or the main scan loop. At the very beginning of each scan iteration, add:
```python
# Warm cache in parallel so subsequent per-symbol get_klines are cache-hits
try:
    md.prefetch(symbols, ["5m", "1h", "4h"], limit=210)
except Exception as e:
    log.warning("prefetch batch failed: %s", e)
```

(`symbols` is the list that `scanner_loop` is iterating over — the dynamic top-N.)

- [ ] **Step 3: Replace internal `get_klines` calls**

Find every call site in `btc_scanner.py` that uses the module-internal `get_klines` (line 289 in current state). These include lines around 432-434 where df5/df1h/df4h are fetched.

Replace:
```python
df5  = get_klines(symbol, "5m",  limit=210)
df1h = get_klines(symbol, "1h",  limit=210)
df4h = get_klines(symbol, "4h",  limit=150)
```

With:
```python
df5  = md.get_klines(symbol, "5m",  limit=210)
df1h = md.get_klines(symbol, "1h",  limit=210)
df4h = md.get_klines(symbol, "4h",  limit=150)
```

Do the same for any other call site (CoinGecko symbol discovery helpers, /ohlcv hooks — grep for `get_klines(` in this file).

- [ ] **Step 4: Run scanner tests**

```bash
python -m pytest tests/test_scanner.py -v
```

If tests fail because they patched the internal get_klines, update them to patch `data.market_data.get_klines` instead (or use the `fake_provider` fixture). Expected: after fixtures update, all scanner tests PASS.

- [ ] **Step 5: Commit**

```bash
git add btc_scanner.py tests/test_scanner.py
git commit -m "refactor(scanner): consume data.market_data for OHLCV fetches"
```

---

### Task 18: Remove dead fetch code from `btc_scanner.py`

**Files:**
- Modify: `btc_scanner.py`

- [ ] **Step 1: Delete internal fetch helpers**

Remove these symbols from `btc_scanner.py`:
- `_active_provider` global (near line 165)
- `_provider_lock` global (near line 166)
- `_provider_fail_count` if present
- Function `get_klines` (around line 289) — now provided by `data.market_data`
- Functions `_get_klines_binance`, `_get_klines_bybit` (helper wrappers)
- Any imports only used by those helpers (`requests` may still be needed elsewhere — grep first)

Verify:
```bash
grep -n '_active_provider\|_get_klines_binance\|_get_klines_bybit' btc_scanner.py
```
Expected: no matches.

- [ ] **Step 2: Run full test suite**

```bash
python -m pytest tests/ -v --tb=short
```
Expected: all tests PASS.

- [ ] **Step 3: Manual smoke test**

```bash
# Run scanner once for 1 minute and confirm no errors
python btc_scanner.py &
PID=$!
sleep 60
kill $PID
tail -30 logs/signals_log.txt
```

Expected: signal log looks normal; no tracebacks in stderr.

- [ ] **Step 4: Commit**

```bash
git add btc_scanner.py
git commit -m "refactor(scanner): remove internal fetch helpers, delegated to data layer"
```

---

### Task 19: Golden-diff validation

**Files:**
- Create: `scripts/golden_diff_scanner.py` (temporary, remove after validation)

- [ ] **Step 1: Write a small script comparing pre/post outputs**

Create `scripts/golden_diff_scanner.py`:
```python
"""Run the scanner once end-to-end and dump the `/symbols` equivalent output to stdout.

Compare the output of this script against the equivalent run on the `main` branch
(pre-migration). Diff should be empty except for timestamps and provider metadata.
"""
import json
import sys

sys.path.insert(0, ".")
from btc_scanner import scan_all  # or the equivalent entrypoint in the module


if __name__ == "__main__":
    result = scan_all()   # returns dict: {symbol: report}
    # Strip fields that are expected to differ across runs
    def strip(r):
        for k in ("timestamp", "fetched_at"):
            r.pop(k, None)
        return r
    print(json.dumps({s: strip(r) for s, r in result.items()}, sort_keys=True, indent=2))
```

(Adjust the entrypoint name if `scan_all` does not exist — use whatever single-pass scan function is available; read the `btc_scanner.py` module to confirm.)

- [ ] **Step 2: Capture output from `main` (pre-migration) branch**

```bash
git stash
git checkout main
python scripts/golden_diff_scanner.py > /tmp/golden_before.json
git checkout -
git stash pop 2>/dev/null || true
```

- [ ] **Step 3: Capture output from the feature branch (post-migration)**

```bash
python scripts/golden_diff_scanner.py > /tmp/golden_after.json
```

- [ ] **Step 4: Diff and inspect**

```bash
diff /tmp/golden_before.json /tmp/golden_after.json | head -80
```

Expected: empty diff, or differences only in `provider` field (due to cache population from different code paths) and in timing-sensitive numerical noise (last digit of computed indicators due to incrementally cached vs. freshly fetched data — should be rare).

If diff shows substantive differences in signal states, scores, or recommendations: **investigate before continuing**. The migration should preserve signal semantics.

- [ ] **Step 5: Remove temporary script and commit**

```bash
rm scripts/golden_diff_scanner.py
git add scripts/
git commit -m "chore: scanner migration validated with golden diff (no-op commit if empty)" --allow-empty
```

---

## Phase 7 — Other consumers

### Task 20: Migrate `backtest.py`

**Files:**
- Modify: `backtest.py`

- [ ] **Step 1: Identify current fetch calls in backtest**

```bash
grep -n 'get_klines\|requests.get\|binance\|bybit' backtest.py | head -30
```

Note all the fetch call sites.

- [ ] **Step 2: Add import and replace fetch calls**

At the top of `backtest.py`:
```python
from data import market_data as md
```

For historical range queries, replace the bidirectional-cache-driven fetch with:
```python
df = md.get_klines_range(symbol, timeframe, start_date, end_date)
```

At the start of a backtest run (after parsing CLI args), add an explicit backfill to pre-populate the cache with a clear progress log:
```python
for sym in symbols:
    for tf in timeframes_needed:   # e.g., ["1h", "4h", "1d"]
        md.backfill(sym, tf, start_date, end_date)
```

- [ ] **Step 3: Remove now-dead bidirectional cache code**

Delete the internal cache logic from commit `9d02bcd` — `data.market_data` now owns caching. Grep for cache-related variables in `backtest.py` and remove.

- [ ] **Step 4: Run backtest tests**

```bash
python -m pytest tests/test_backtest_dual.py -v
```
Expected: PASS. Fix any test that relied on the old cache internals.

- [ ] **Step 5: Manual smoke run**

```bash
python backtest.py --symbol BTCUSDT --start 2024-01-01 --end 2024-02-01
```
Expected: backtest completes, P&L output reasonable.

- [ ] **Step 6: Commit**

```bash
git add backtest.py tests/test_backtest_dual.py
git commit -m "refactor(backtest): consume data.market_data for historical ranges"
```

---

### Task 21: Migrate `auto_tune.py` and `grid_search_tf.py`

**Files:**
- Modify: `auto_tune.py`
- Modify: `grid_search_tf.py`

- [ ] **Step 1: Identify fetch call sites**

```bash
grep -n 'get_klines\|binance\|bybit\|requests' auto_tune.py grid_search_tf.py | head -40
```

- [ ] **Step 2: Replace with `md.get_klines_range`**

In each script, at the top add:
```python
from data import market_data as md
```

Replace all manual fetch logic with `md.get_klines_range(symbol, timeframe, start, end)` for historical windows. If the script needs the latest tail (e.g., current month), use `md.get_klines(symbol, timeframe, limit)`.

Before the main loop, pre-backfill the needed range:
```python
md.backfill(symbol, timeframe, start, end)
```

- [ ] **Step 3: Delete dead fetch helpers in those files**

Anything that was doing independent HTTP calls or caching for these scripts can be removed.

- [ ] **Step 4: Run associated tests**

```bash
python -m pytest tests/test_auto_tune.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add auto_tune.py grid_search_tf.py tests/test_auto_tune.py
git commit -m "refactor(auto-tune,grid-search): consume data.market_data for all fetches"
```

---

### Task 22: Migrate `btc_report.py` and `optimize_new_tokens.py`

**Files:**
- Modify: `btc_report.py`
- Modify: `optimize_new_tokens.py`

- [ ] **Step 1: Identify fetch call sites**

```bash
grep -n 'get_klines\|binance\|bybit\|requests' btc_report.py optimize_new_tokens.py | head -30
```

- [ ] **Step 2: Replace with `md.*` calls**

Same pattern: import `from data import market_data as md`, replace per the usage (tail vs range). Remove dead helpers.

- [ ] **Step 3: Smoke-run each script**

```bash
python btc_report.py --symbol BTCUSDT > /tmp/report.html
head -40 /tmp/report.html
```
Expected: valid HTML, recent data.

- [ ] **Step 4: Commit**

```bash
git add btc_report.py optimize_new_tokens.py
git commit -m "refactor(report,optimize): consume data.market_data, remove duplicated fetch"
```

---

### Task 23: `/ohlcv` endpoint uses `get_klines_live`; `/status` exposes market data stats

**Files:**
- Modify: `btc_api.py`

- [ ] **Step 1: Update `/ohlcv` endpoint**

Find the existing `/ohlcv` handler (around `btc_api.py:1613`). Replace its fetch logic:
```python
from data import market_data as md

@app.get("/ohlcv", summary="Velas OHLCV para graficar")
def ohlcv(symbol: str, interval: str = "1h", limit: int = 200):
    df = md.get_klines_live(symbol, interval, limit)
    return {
        "symbol": symbol,
        "interval": interval,
        "candles": df.to_dict(orient="records"),
    }
```

- [ ] **Step 2: Expose market_data stats in `/status`**

Locate the `/status` handler. Add:
```python
response["market_data"] = md.get_stats()
```

to the response dict before returning.

- [ ] **Step 3: Run API tests**

```bash
python -m pytest tests/test_api.py -v
```
Expected: PASS (update any test that hits `/ohlcv` or `/status` if needed to accept the new payload shape).

- [ ] **Step 4: Smoke-test endpoints**

```bash
python btc_api.py &
PID=$!
sleep 3
curl -s 'http://localhost:8000/ohlcv?symbol=BTCUSDT&interval=1h&limit=10' | head -50
curl -s 'http://localhost:8000/status' | python -m json.tool | grep -A 5 market_data
kill $PID
```
Expected: candles come back; `/status` shows `market_data` keys with counters.

- [ ] **Step 5: Commit**

```bash
git add btc_api.py tests/test_api.py
git commit -m "feat(api): /ohlcv uses get_klines_live, /status exposes market_data metrics"
```

---

### Task 24: Phase 7 close-out — full regression suite + deploy

**Files:** none (verification only)

- [ ] **Step 1: Run complete test suite with verbose output**

```bash
python -m pytest tests/ -v --tb=short
```
Expected: ALL tests PASS.

- [ ] **Step 2: Measure test coverage on the `data/` package**

```bash
pip install coverage 2>/dev/null || true
coverage run -m pytest tests/ 2>&1 | tail -5
coverage report --include='data/*'
```
Expected: ≥85% line coverage for `data/` files. If below, add targeted tests to cover the gap before moving on.

- [ ] **Step 3: Stage the deploy**

```bash
git log --oneline main..HEAD
```

Confirm commits are clean, messages descriptive. Push the branch and open a PR:
```bash
git push -u origin $(git branch --show-current)
```

Open PR referencing spec + #125. Wait for CI green. Merge once approved.

- [ ] **Step 4: Production observation window**

After merge, monitor production for ≥1 week:
- Check `/status` endpoint daily — verify `market_data.counters.fetches_total` grows at expected ~2.2 req/min rate.
- Check `fallback_fetches_total` stays near zero.
- Check `invalid_bars_dropped_total` stays at zero.
- Check scanner signal output is unchanged via normal logs.

Only after this observation window, proceed to Phase 8.

- [ ] **Step 5: Record observation period completion**

Write a short entry in project log (or CHANGELOG) when the observation window closes cleanly:
```bash
echo "$(date -Iseconds) market data layer stable in prod for 7 days — ready for #125" >> docs/deploy-log.md
git add docs/deploy-log.md
git commit -m "docs: record market data layer production stability"
```

---

## Phase 8 — Issue #125 consumer (GATED: wait ≥1 week after Phase 6 in production)

### Task 25: `annualized_vol_yang_zhang` function + tests

**Files:**
- Modify: `btc_scanner.py`
- Create: `tests/test_vol_calc.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_vol_calc.py`:
```python
import numpy as np
import pandas as pd
import pytest


def _daily_df(opens, highs, lows, closes):
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows, "close": closes,
    })


class TestYangZhangVol:
    def test_zero_variance_bars_returns_tiny_floor(self):
        from btc_scanner import annualized_vol_yang_zhang
        # All bars identical → variance zero → result near zero
        df = _daily_df([100.0] * 30, [100.0] * 30, [100.0] * 30, [100.0] * 30)
        vol = annualized_vol_yang_zhang(df)
        assert 0.0 <= vol < 0.01

    def test_short_series_returns_fallback(self):
        from btc_scanner import annualized_vol_yang_zhang, TARGET_VOL_ANNUAL
        df = _daily_df([100.0] * 3, [101.0] * 3, [99.0] * 3, [100.0] * 3)
        vol = annualized_vol_yang_zhang(df)
        assert vol == TARGET_VOL_ANNUAL

    def test_typical_crypto_volatility_in_range(self):
        from btc_scanner import annualized_vol_yang_zhang
        # Simulate ~2% daily range, 1% daily drift noise
        rng = np.random.default_rng(42)
        n = 30
        closes = 100.0 * np.exp(rng.normal(0, 0.02, n).cumsum())
        opens = np.concatenate([[closes[0]], closes[:-1]])
        highs = np.maximum(opens, closes) * (1 + np.abs(rng.normal(0, 0.01, n)))
        lows = np.minimum(opens, closes) * (1 - np.abs(rng.normal(0, 0.01, n)))
        df = _daily_df(opens, highs, lows, closes)
        vol = annualized_vol_yang_zhang(df)
        # Expect roughly 20-50% annualized for such a series
        assert 0.1 <= vol <= 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_vol_calc.py -v`
Expected: FAIL — `annualized_vol_yang_zhang` not defined.

- [ ] **Step 3: Implement in `btc_scanner.py`**

Add near the top of `btc_scanner.py` (near other constants):
```python
# ── Volatility-normalized sizing (#125) ─────────────────────────────────────
TARGET_VOL_ANNUAL = 0.15   # 15% target portfolio contribution per position
VOL_LOOKBACK_DAYS = 30
VOL_MIN_FLOOR = 0.05       # clamp for assets with near-zero vol
VOL_MAX_CEIL = 0.20        # never risk less than 20% of base per position


def annualized_vol_yang_zhang(df_daily: pd.DataFrame) -> float:
    """Yang-Zhang annualized vol over daily bars.

    Crypto note: 24/7 markets collapse the overnight term toward zero, but YZ still
    correctly weights open-close and Rogers-Satchell components.
    Returns TARGET_VOL_ANNUAL if too few bars (neutral sizing fallback).
    """
    if len(df_daily) < 5:
        return TARGET_VOL_ANNUAL
    o = df_daily["open"].astype(float)
    h = df_daily["high"].astype(float)
    l = df_daily["low"].astype(float)
    c = df_daily["close"].astype(float)
    log_ho = np.log(h / o)
    log_lo = np.log(l / o)
    log_co = np.log(c / o)
    log_oc_prev = np.log(o / c.shift(1)).dropna()
    n = len(df_daily) - 1
    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    sigma_on = log_oc_prev.var(ddof=1) if len(log_oc_prev) >= 2 else 0.0
    sigma_oc = log_co.var(ddof=1)
    sigma_rs = (log_ho * (log_ho - log_co) + log_lo * (log_lo - log_co)).mean()
    var_daily = max(sigma_on + k * sigma_oc + (1 - k) * sigma_rs, 1e-10)
    return float(np.sqrt(var_daily * 365))
```

(Ensure `import numpy as np` is already at the top of the file.)

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_vol_calc.py -v`
Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add btc_scanner.py tests/test_vol_calc.py
git commit -m "feat(scanner): add Yang-Zhang annualized volatility estimator"
```

---

### Task 26: Apply `vol_mult` in scanner sizing

**Files:**
- Modify: `btc_scanner.py`

- [ ] **Step 1: Locate the sizing block in `assess_signal`**

Around `btc_scanner.py:959` (per current line numbers, may have shifted):
```python
capital    = 1000.0
risk_usd   = capital * 0.01
```

- [ ] **Step 2: Replace with vol-normalized sizing**

```python
capital = 1000.0

# Vol-normalized risk (#125): fetch daily bars, compute vol, scale risk per symbol
try:
    df_daily = md.get_klines(symbol, "1d", VOL_LOOKBACK_DAYS + 5)
    asset_vol = annualized_vol_yang_zhang(df_daily)
except Exception as e:
    log.warning("Vol calc failed for %s: %s — using neutral sizing", symbol, e)
    asset_vol = TARGET_VOL_ANNUAL

vol_mult = max(VOL_MAX_CEIL, min(1.0, TARGET_VOL_ANNUAL / max(asset_vol, VOL_MIN_FLOOR)))
risk_usd = capital * 0.01 * vol_mult
```

- [ ] **Step 3: Add vol fields to the report dict**

Locate the `rep.update({...})` or equivalent section that builds the `sizing_1h` block. Add:
```python
"asset_vol": round(asset_vol, 4),
"vol_mult": round(vol_mult, 3),
"target_vol": TARGET_VOL_ANNUAL,
```

- [ ] **Step 4: Add a test for the integration**

Append to `tests/test_scanner.py` (or the appropriate existing test module):
```python
def test_vol_mult_applied_to_risk(monkeypatch, tmp_ohlcv_db, fake_provider):
    from btc_scanner import annualized_vol_yang_zhang, TARGET_VOL_ANNUAL
    from tests._fakes import make_bar
    import pandas as pd, numpy as np

    # Seed daily bars with high volatility to force vol_mult < 1
    n = 35
    rng = np.random.default_rng(0)
    prices = 100.0 * np.exp(rng.normal(0, 0.05, n).cumsum())
    bars = []
    for i, p in enumerate(prices):
        bars.append(make_bar("BTCUSDT", "1d", i * 86_400_000, price=float(p)))
    fake_provider.set_bars("BTCUSDT", "1d", bars)

    # Assert vol calculated and reasonable
    df = pd.DataFrame({
        "open": prices, "high": prices * 1.03, "low": prices * 0.97, "close": prices,
    })
    vol = annualized_vol_yang_zhang(df)
    assert vol > TARGET_VOL_ANNUAL  # high-vol series → mult < 1
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/test_scanner.py tests/test_vol_calc.py -v
```
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add btc_scanner.py tests/test_scanner.py
git commit -m "feat(scanner): apply vol-normalized sizing in assess_signal (#125)"
```

---

### Task 27: Apply `vol_mult` in `backtest.py`

**Files:**
- Modify: `backtest.py`

- [ ] **Step 1: Import the vol helper**

At top of `backtest.py`:
```python
from btc_scanner import annualized_vol_yang_zhang, TARGET_VOL_ANNUAL, VOL_LOOKBACK_DAYS, VOL_MIN_FLOOR, VOL_MAX_CEIL
from data import market_data as md
```

- [ ] **Step 2: Pre-backfill 1d for vol calculation in backtest setup**

In the backtest CLI entry (after parsing args, before the simulation loop), add:
```python
from datetime import timedelta
for sym in symbols:
    md.backfill(
        sym, "1d",
        start_date - timedelta(days=VOL_LOOKBACK_DAYS + 5),
        end_date,
    )
```

- [ ] **Step 3: Apply vol_mult in the position open logic**

Locate the position-open block (around `backtest.py:315` in current state where `risk_amount = capital * RISK_PER_TRADE * position["size_mult"]`).

Replace with:
```python
# Vol-normalized sizing (#125). No look-ahead: only bars with open_time < bar_time.
df_daily_slice = md.get_klines_range(
    symbol, "1d",
    bar_time - pd.Timedelta(days=VOL_LOOKBACK_DAYS + 5),
    bar_time,
)
asset_vol = annualized_vol_yang_zhang(df_daily_slice)
vol_mult = max(VOL_MAX_CEIL, min(1.0, TARGET_VOL_ANNUAL / max(asset_vol, VOL_MIN_FLOOR)))
risk_amount = capital * RISK_PER_TRADE * position["size_mult"] * vol_mult
```

Do the same for any other call site in the file where `risk_amount` is computed (e.g., around `backtest.py:536`).

- [ ] **Step 4: Run backtest tests**

```bash
python -m pytest tests/test_backtest_dual.py -v
```
Expected: tests still PASS. If numerics changed because sizing changed, that's expected — tests that assert specific P&L numbers may need updates reflecting the new sizing.

- [ ] **Step 5: Commit**

```bash
git add backtest.py tests/test_backtest_dual.py
git commit -m "feat(backtest): apply vol-normalized sizing (#125)"
```

---

### Task 28: Comparative backtest + document results

**Files:**
- Create: `docs/superpowers/specs/es/2026-04-18-vol-normalized-resultados.md`

- [ ] **Step 1: Baseline backtest (before vol sizing)**

Check out the commit just before Task 27 (or revert the `vol_mult` application temporarily):
```bash
git stash
git checkout HEAD~1   # pre-vol-sizing
python backtest.py --start 2022-01-01 --end 2026-04-18 --output /tmp/baseline.json
git checkout -
git stash pop 2>/dev/null || true
```

Capture key metrics: total return, max drawdown, Sharpe, per-symbol P&L contribution.

- [ ] **Step 2: Run backtest with vol sizing**

```bash
python backtest.py --start 2022-01-01 --end 2026-04-18 --output /tmp/vol_sized.json
```

- [ ] **Step 3: Write the results document**

Create `docs/superpowers/specs/es/2026-04-18-vol-normalized-resultados.md`:
```markdown
# Resultados de Vol-Normalized Position Sizing — #125

**Fecha:** 2026-04-XX
**Spec:** `docs/superpowers/specs/en/2026-04-18-market-data-layer-design.md`
**Issue:** #125

## Comparativa

| Métrica | Baseline (sin vol) | Con vol sizing | Delta |
|---|---|---|---|
| Total P&L ($) | [FILL] | [FILL] | [FILL] |
| Max drawdown (%) | [FILL] | [FILL] | [FILL] |
| Sharpe | [FILL] | [FILL] | [FILL] |
| Profit Factor | [FILL] | [FILL] | [FILL] |
| Trades | [FILL] | [FILL] | [FILL] |

## Contribución por símbolo

[Tabla con BTC/ETH/ADA/AVAX/DOGE/UNI/XLM/PENDLE/JUP/RUNE — P&L antes y después]

## Conclusión

- Si el swing se acerca al objetivo del epic #121 (-$14,655 → +$25,000–$40,000): VALIDADO
- Si no: ajustar clamps (`VOL_MIN_FLOOR`, `VOL_MAX_CEIL`), lookback, o target_vol

## Próximos pasos

- [ ] Observación en producción (4 semanas) antes de ajustar capital real
- [ ] Revisión de épica #121
```

Fill in the `[FILL]` slots with real numbers from the comparative run.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/es/2026-04-18-vol-normalized-resultados.md
git commit -m "docs: vol-normalized sizing comparative backtest results (#125)"
```

- [ ] **Step 5: Close issue #125 if results validate**

```bash
gh issue close 125 --comment "Resuelto por la capa de market data + vol-normalized sizing en btc_scanner.py y backtest.py. Resultados comparativos: docs/superpowers/specs/es/2026-04-18-vol-normalized-resultados.md"
```

---

## Self-Review

**1. Spec coverage:**

| Spec section | Task(s) |
|---|---|
| Package structure | Task 1 |
| Storage schema | Tasks 5–7 |
| Public API (6 funcs + utilities) | Tasks 13–15, 16 |
| Internal flows: get_klines, ensure_fresh | Tasks 12, 13 |
| Internal flows: get_klines_range, gap detection | Task 14 |
| Internal flows: backfill chunking, internal gaps | Task 12 |
| Internal flows: concurrency + lock registry | Tasks 10, 12 |
| Failover state machine + recovery probe | Task 11 |
| Provider adapters (Binance, Bybit) | Tasks 8, 9 |
| Rate limiter wiring | Task 10 |
| Metrics + observability | Task 3; integrated throughout |
| `/status` integration | Task 23 |
| Error taxonomy | Task 4 |
| Testing strategy (fixtures, units, integration) | Tasks 1, 2–16 (each paired) |
| CLI helper | Task 16 |
| Scanner migration | Tasks 17–19 |
| Backtest + ad-hoc migration | Tasks 20–22 |
| API endpoint migration | Task 23 |
| #125 consumer (vol sizing) | Tasks 25–28 |
| Phase 8 production-stability gate | Task 24 |

No uncovered spec sections.

**2. Placeholder scan:** No "TBD" / "implement later" / "similar to Task N" strings. Every code block contains the actual code to run. The `[FILL]` markers in Task 28 are explicit data-capture slots for the comparative backtest results, not implementation placeholders.

**3. Type consistency:**
- `Bar` dataclass fields used consistently across tasks.
- `_storage.range_(...)` (with trailing underscore) used in Tasks 7, 14, 15 — consistent to avoid shadowing Python's `range` builtin.
- `_fetcher._PROVIDERS`, `_active_idx`, `_consecutive_failures`, `_last_probe_ms` consistent between Tasks 10, 11 and the fixture resets in `tests/conftest.py` (Task 1).
- `get_stats()` exported via `data/__init__.py` (Task 16) matches the implementation added in Task 15.

**Execution Handoff:**

Plan complete and saved to `docs/superpowers/plans/2026-04-18-market-data-layer.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
