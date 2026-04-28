import pandas as pd
import pytest
from btc_scanner import _compute_price_score


def _df_daily(closes, highs=None, lows=None):
    n = len(closes)
    return pd.DataFrame({
        "open":  closes,
        "high":  highs if highs is not None else [c + 1 for c in closes],
        "low":   lows if lows is not None else [c - 1 for c in closes],
        "close": closes,
        "volume": [1000] * n,
    }, index=pd.date_range("2020-01-01", periods=n, freq="D"))


class TestComputePriceScore:
    def test_death_cross_price_below_sma_negative_return(self):
        """SMA50 < SMA200, price < SMA200, 30d return < -10% → 100-40-30-20=10."""
        closes = [100.0] * 170 + [100.0 - i for i in range(30)] + [50.0] * 10
        df = _df_daily(closes)
        assert _compute_price_score(df) == 10

    def test_only_death_cross(self):
        """SMA50 < SMA200, price > SMA200, ret30 positive → 100-40=60."""
        closes = [200.0] * 150 + [160.0] * 30 + [140.0] * 15 + [210.0] * 15
        df = _df_daily(closes)
        score = _compute_price_score(df)
        assert 55 <= score <= 75

    def test_bull_market_clean(self):
        """SMA50 > SMA200, price > SMA200, ret30 positive → 100."""
        closes = list(range(100, 310))
        df = _df_daily(closes)
        assert _compute_price_score(df) == 100

    def test_transition_mild(self):
        """SMA50 > SMA200, price < SMA200, ret30 slightly negative → 100-30-10=60."""
        closes = [100.0] * 150 + [105.0] * 40 + [95.0] * 20
        df = _df_daily(closes)
        score = _compute_price_score(df)
        assert 55 <= score <= 70

    def test_insufficient_data_returns_100(self):
        """< 200 bars → fallback 100 (bullish assumption)."""
        df = _df_daily([100.0] * 150)
        assert _compute_price_score(df) == 100

    def test_empty_dataframe_returns_100(self):
        """Empty df → 100."""
        df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        assert _compute_price_score(df) == 100

    def test_nan_prices_graceful(self):
        """NaN prices don't crash."""
        closes = [100.0] * 200
        closes[50] = float("nan")
        df = _df_daily(closes)
        score = _compute_price_score(df)
        assert 0 <= score <= 100


class TestComputeFngScore:
    def test_pass_through_zero(self):
        from btc_scanner import _compute_fng_score
        assert _compute_fng_score(0) == 0

    def test_pass_through_50(self):
        from btc_scanner import _compute_fng_score
        assert _compute_fng_score(50) == 50

    def test_pass_through_100(self):
        from btc_scanner import _compute_fng_score
        assert _compute_fng_score(100) == 100


class TestComputeFundingScore:
    def test_rate_minus_one_percent(self):
        from btc_scanner import _compute_funding_score
        assert _compute_funding_score(-0.01) == 0

    def test_rate_zero(self):
        from btc_scanner import _compute_funding_score
        assert _compute_funding_score(0) == 50

    def test_rate_plus_one_percent(self):
        from btc_scanner import _compute_funding_score
        assert _compute_funding_score(0.01) == 100

    def test_extreme_positive_clamped(self):
        from btc_scanner import _compute_funding_score
        assert _compute_funding_score(0.05) == 100

    def test_extreme_negative_clamped(self):
        from btc_scanner import _compute_funding_score
        assert _compute_funding_score(-0.05) == 0


class TestComputeRsiScore:
    def test_rsi_30_gives_70(self):
        from btc_scanner import _compute_rsi_score
        assert _compute_rsi_score(30) == 70

    def test_rsi_50_neutral(self):
        from btc_scanner import _compute_rsi_score
        assert _compute_rsi_score(50) == 50

    def test_rsi_70_gives_30(self):
        from btc_scanner import _compute_rsi_score
        assert _compute_rsi_score(70) == 30

    def test_rsi_20_oversold_bullish(self):
        from btc_scanner import _compute_rsi_score
        assert _compute_rsi_score(20) == 80

    def test_rsi_80_overbought_bearish(self):
        from btc_scanner import _compute_rsi_score
        assert _compute_rsi_score(80) == 20


class TestComputeAdxScore:
    def test_adx_below_20_ranging(self):
        from btc_scanner import _compute_adx_score
        assert _compute_adx_score(15) == 75

    def test_adx_20_30_medium(self):
        from btc_scanner import _compute_adx_score
        assert _compute_adx_score(25) == 50

    def test_adx_above_30_trending(self):
        from btc_scanner import _compute_adx_score
        assert _compute_adx_score(35) == 25

    def test_adx_strong_trend(self):
        from btc_scanner import _compute_adx_score
        assert _compute_adx_score(50) == 25


class TestComposeLocalRegime:
    def test_hybrid_mode_composition(self):
        """hybrid mode: 50% price + 25% F&G + 25% funding.

        Series: death cross (SMA50=172 < SMA200=193) but price=250 > SMA200 and ret30>0.
        price_score = 60. composite = 60*0.5 + 60*0.25 + 60*0.25 = 60.
        """
        from btc_scanner import _compute_local_regime
        closes = [200.0] * 160 + [120.0] * 30 + [250.0] * 20
        df = _df_daily(closes)
        result = _compute_local_regime(
            symbol="BTCUSDT", mode="hybrid",
            df_daily_sym=df,
            fng_score=60, funding_score=60,
        )
        assert "regime" in result
        assert 55 <= result["score"] <= 65
        assert result["mode"] == "hybrid"
        assert result["symbol"] == "BTCUSDT"

    def test_hybrid_mode_bear(self):
        """All scores low → BEAR."""
        from btc_scanner import _compute_local_regime
        closes = [150.0] * 100 + [80.0] * 60 + [60.0] * 50
        df = _df_daily(closes)
        result = _compute_local_regime(
            symbol="DOGEUSDT", mode="hybrid",
            df_daily_sym=df,
            fng_score=20, funding_score=20,
        )
        assert result["score"] < 40
        assert result["regime"] == "BEAR"

    def test_hybrid_momentum_uses_rsi_adx(self):
        """hybrid_momentum includes RSI and ADX components."""
        from btc_scanner import _compute_local_regime
        closes = list(range(100, 310))
        df = _df_daily(closes)
        result = _compute_local_regime(
            symbol="BTCUSDT", mode="hybrid_momentum",
            df_daily_sym=df,
            fng_score=70, funding_score=60,
            rsi_score=50, adx_score=75,
        )
        # 0.30*100 + 0.15*50 + 0.20*75 + 0.20*70 + 0.15*60 = 30+7.5+15+14+9 = 75.5
        assert 70 <= result["score"] <= 80
        assert result["regime"] == "BULL"
        assert "rsi" in result["components"]
        assert "adx" in result["components"]

    def test_global_mode_uses_40_30_30_weights(self):
        """mode='global' uses 40/30/30 weights."""
        from btc_scanner import _compute_local_regime
        closes = list(range(100, 310))
        df = _df_daily(closes)
        result = _compute_local_regime(
            symbol=None, mode="global",
            df_daily_sym=df,
            fng_score=50, funding_score=50,
        )
        # 0.40*100 + 0.30*50 + 0.30*50 = 70
        assert 65 <= result["score"] <= 75


class TestDetectRegimeForSymbol:
    def test_global_mode_delegates_to_legacy(self, monkeypatch):
        """mode='global' delegates to detect_regime() unchanged."""
        import strategy.regime as _regime_mod
        from btc_scanner import detect_regime_for_symbol
        expected = {"ts": "2026-01-01T00:00:00Z", "regime": "NEUTRAL", "score": 50.0}
        # PR6: patch the home module (strategy.regime) not the re-export (btc_scanner)
        monkeypatch.setattr(_regime_mod, "detect_regime", lambda: expected)
        monkeypatch.setattr(_regime_mod, "_regime_cache", {})
        result = detect_regime_for_symbol(symbol=None, mode="global")
        assert result["regime"] == "NEUTRAL"

    def test_invalid_mode_falls_back_to_global(self, monkeypatch):
        """Invalid mode → falls back to 'global'."""
        import strategy.regime as _regime_mod
        from btc_scanner import detect_regime_for_symbol
        expected = {"ts": "2026-01-01T00:00:00Z", "regime": "BULL", "score": 80.0}
        # PR6: patch the home module (strategy.regime) not the re-export (btc_scanner)
        monkeypatch.setattr(_regime_mod, "detect_regime", lambda: expected)
        monkeypatch.setattr(_regime_mod, "_regime_cache", {})
        result = detect_regime_for_symbol(symbol="BTCUSDT", mode="garbage_mode")
        assert result["regime"] == "BULL"


class TestCacheKeyResolution:
    def test_cache_key_global(self):
        from btc_scanner import _regime_cache_key
        assert _regime_cache_key(None, "global") == "global"

    def test_cache_key_hybrid(self):
        from btc_scanner import _regime_cache_key
        assert _regime_cache_key("BTCUSDT", "hybrid") == "hybrid:BTCUSDT"

    def test_cache_key_hybrid_momentum(self):
        from btc_scanner import _regime_cache_key
        assert _regime_cache_key("DOGEUSDT", "hybrid_momentum") == "hybrid_momentum:DOGEUSDT"

    def test_legacy_cache_soft_migration(self, tmp_path, monkeypatch):
        """Legacy flat format {ts, regime, score} loads wrapped in {'global': {...}}."""
        import json
        import strategy.regime as _regime_mod
        from btc_scanner import _load_regime_cache
        legacy_path = tmp_path / "regime_cache.json"
        legacy_path.write_text(json.dumps({
            "ts": "2026-01-01T00:00:00Z",
            "regime": "NEUTRAL",
            "score": 50.0,
        }))
        # PR6: patch the home module (strategy.regime) not the re-export (btc_scanner)
        monkeypatch.setattr(_regime_mod, "_REGIME_CACHE_PATH", str(legacy_path))
        data = _load_regime_cache()
        assert "global" in data
        assert data["global"]["regime"] == "NEUTRAL"
