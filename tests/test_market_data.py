from datetime import datetime, timezone
import pytest
from data import market_data as md
from data import _storage, _fetcher
from data.timeframes import last_closed_bar_time, delta_ms
from _fakes import make_bar


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
        # Pin "now" so the requested range aligns with the seeded bars; the
        # FakeProvider filters by open_time range.
        pinned_now = datetime(2026, 1, 1, 5, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(md, "_utcnow", lambda: pinned_now)
        d = 3600_000
        pinned_ms = int(pinned_now.timestamp() * 1000)
        current = (pinned_ms // d) * d
        bars = [make_bar("BTCUSDT", "1h", current - (4 - i) * d) for i in range(5)]
        fake_provider.set_bars("BTCUSDT", "1h", bars)
        df = md.get_klines_live("BTCUSDT", "1h", 5)
        assert len(df) == 5
        # Nothing was persisted to the DB
        count = _storage._conn().execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]
        assert count == 0


from datetime import timedelta


class TestGetKlinesRange:
    def test_cache_hit_no_fetch(self, tmp_ohlcv_db, fake_provider, monkeypatch):
        monkeypatch.setattr(md, "last_closed_bar_time", lambda tf, now=None: 9 * 3600_000)
        bars = [make_bar("BTCUSDT", "1h", t * 3600_000) for t in range(10)]
        _storage.upsert_many(bars)
        fake_provider.calls.clear()
        df = md.get_klines_range(
            "BTCUSDT", "1h",
            datetime(1970, 1, 1, 0, 0, tzinfo=timezone.utc),
            datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(hours=9),
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
            datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(hours=9),
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
            datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(hours=9),
        )
        assert len(df) == 10
        # Left edge fetch: [0, 4]
        assert fake_provider.calls[0][2] == 0
        assert fake_provider.calls[0][3] == 4 * 3600_000
