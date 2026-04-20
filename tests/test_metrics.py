import json
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
        assert stats["counters"]["fetches_total"] == {"": 4}

    def test_inc_with_labels(self):
        metrics.inc("fetches_total", labels={"provider": "binance"})
        metrics.inc("fetches_total", labels={"provider": "binance"})
        metrics.inc("fetches_total", labels={"provider": "bybit"})
        stats = metrics.get_stats()
        assert stats["counters"]["fetches_total"]["provider=binance"] == 2
        assert stats["counters"]["fetches_total"]["provider=bybit"] == 1

    def test_inc_thread_safety(self):
        def worker():
            for _ in range(1000):
                metrics.inc("race_counter")
        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()
        stats = metrics.get_stats()
        assert stats["counters"]["race_counter"][""] == 8000


class TestLatencyHistogram:
    def test_observe_and_percentiles(self):
        for v in [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]:
            metrics.observe("fetch_latency_ms", v)
        stats = metrics.get_stats()
        assert "fetch_latency_ms" in stats["latency_p50_ms"]
        # p50 of 1..10 scaled to 10..100: median is 50 or 60 depending on interpolation
        assert 40 <= stats["latency_p50_ms"]["fetch_latency_ms"][""] <= 70
        assert 85 <= stats["latency_p95_ms"]["fetch_latency_ms"][""] <= 100

    def test_observe_bounded_deque(self):
        # maxlen=100; adding 250 values should retain only the last 100
        for v in range(250):
            metrics.observe("bounded", v)
        stats = metrics.get_stats()
        # Median of last 100 values (150..249) is ~199
        assert 190 <= stats["latency_p50_ms"]["bounded"][""] <= 210

    def test_observe_with_labels_nests_by_name(self):
        metrics.observe("fetch_ms", 100.0, labels={"provider": "binance"})
        metrics.observe("fetch_ms", 200.0, labels={"provider": "bybit"})
        stats = metrics.get_stats()
        assert set(stats["latency_p50_ms"]["fetch_ms"].keys()) == {
            "provider=binance", "provider=bybit"
        }


class TestGetStats:
    def test_snapshot_is_plain_dict(self):
        metrics.inc("x")
        metrics.observe("lat", 5.0)
        stats = metrics.get_stats()
        # No mutable references leak
        assert isinstance(stats, dict)
        assert isinstance(stats["counters"], dict)
        assert isinstance(stats["latency_p50_ms"], dict)

    def test_snapshot_is_json_serializable(self):
        # Regression for #153: FastAPI cannot serialize tuple dict keys.
        metrics.inc("fetches_total", labels={"provider": "binance", "tf": "1h"})
        metrics.inc("fetches_total")
        metrics.observe("fetch_ms", 12.5, labels={"symbol": "BTC"})
        metrics.observe("fetch_ms", 7.5)
        stats = metrics.get_stats()
        # Would raise TypeError on any non-string dict key
        json.dumps(stats)
