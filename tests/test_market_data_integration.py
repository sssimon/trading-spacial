"""End-to-end scenarios using FakeProvider + tmp_ohlcv_db."""
from datetime import datetime, timezone, timedelta
import threading
import pytest
from data import market_data as md
from data import _storage, _fetcher
from _fakes import make_bar


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
            datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(hours=100),
        )
        fake_provider.calls.clear()
        # end=91h so last_closed_bar_time caps at 90h; range [10h, 90h] = 81 bars
        df = md.get_klines_range(
            "BTCUSDT", "1h",
            datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(hours=10),
            datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(hours=91),
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
            datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(hours=50),
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
