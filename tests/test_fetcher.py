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


from data.providers.base import (
    ProviderRateLimited, ProviderTemporaryError, ProviderInvalidSymbol,
    AllProvidersFailedError,
)
from _fakes import make_bar


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
        # Counter accumulates on primary failure — fallback success does NOT
        # reset it, otherwise the threshold could never trigger.
        assert _fetcher._consecutive_failures == 1

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


from data import _storage


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
