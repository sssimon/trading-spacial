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
