import math
import numpy as np
import pandas as pd
from tools.cost_diagnosis.liquidity import liquidity_series, liquidity_at


def _df(n, close=100.0, volume=600.0, start="2026-04-01"):
    idx = pd.date_range(start, periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"close": [close] * n, "volume": [volume] * n}, index=idx)


def test_series_matches_backtest_formula():
    # close*volume/60 = 100*600/60 = 1000 per bar; rolling mean settles at 1000.
    s = liquidity_series(_df(200))
    assert math.isclose(float(s.iloc[-1]), 1000.0, rel_tol=1e-9)


def test_min_periods_gives_nan_early():
    s = liquidity_series(_df(200))
    # bar 0..118 (< min_periods=120) are NaN
    assert np.isnan(float(s.iloc[50]))


def test_liquidity_at_picks_last_bar_at_or_before_ts():
    df = _df(200)
    s = liquidity_series(df)
    ts = df.index[150]
    assert math.isclose(liquidity_at(s, ts), 1000.0, rel_tol=1e-9)


def test_liquidity_at_before_series_is_nan():
    s = liquidity_series(_df(200, start="2026-04-01"))
    assert math.isnan(liquidity_at(s, "2026-01-01T00:00:00+00:00"))


def _df_naive(n, close=100.0, volume=600.0, start="2026-04-01"):
    # tz-NAIVE index, as backtest.get_cached_data returns (datetime64[ms]).
    idx = pd.date_range(start, periods=n, freq="1h")
    return pd.DataFrame({"close": [close] * n, "volume": [volume] * n}, index=idx)


def test_liquidity_at_naive_index_with_tzaware_ts():
    # Regression: real run had a tz-naive bar index + tz-aware live entry_ts.
    # Must not raise "Cannot compare tz-naive and tz-aware".
    s = liquidity_series(_df_naive(200))
    ts = "2026-04-07T00:00:00+00:00"  # tz-aware, within range
    assert math.isclose(liquidity_at(s, ts), 1000.0, rel_tol=1e-9)


def test_liquidity_at_tzaware_index_with_naive_ts():
    # Inverse case: tz-aware index, tz-naive ts string.
    s = liquidity_series(_df(200))
    assert math.isclose(liquidity_at(s, "2026-04-07T00:00:00"), 1000.0, rel_tol=1e-9)
