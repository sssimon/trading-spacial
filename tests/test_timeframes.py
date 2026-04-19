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
