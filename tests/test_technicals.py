"""Indicator tests — hand-checked fixtures that encode intent (Rulebook §5/§8).

Convention under test: SMA-seeded Wilder (RSI/ATR), SMA-seeded EMA (MACD).
Each indicator is pinned with a literal, fully hand-computed example plus
structural invariants and an independent reference implementation.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.config_loader import load_config
from src.indicators import technicals as T


# --------------------------------------------------------------------------- #
# Independent reference implementations (separate code path from the module)
# --------------------------------------------------------------------------- #
def _ref_wilder(s: pd.Series, period: int) -> pd.Series:
    arr = s.to_numpy(dtype=float)
    out = [np.nan] * len(arr)
    valid = [i for i, x in enumerate(arr) if not np.isnan(x)]
    if len(valid) < period:
        return pd.Series(out, index=s.index)
    start = valid[0]
    seed = start + period - 1
    out[seed] = float(np.mean(arr[start : seed + 1]))
    for i in range(seed + 1, len(arr)):
        out[i] = (out[i - 1] * (period - 1) + arr[i]) / period
    return pd.Series(out, index=s.index)


def _ref_ema(s: pd.Series, span: int) -> pd.Series:
    arr = s.to_numpy(dtype=float)
    out = [np.nan] * len(arr)
    valid = [i for i, x in enumerate(arr) if not np.isnan(x)]
    if len(valid) < span:
        return pd.Series(out, index=s.index)
    a = 2.0 / (span + 1.0)
    start = valid[0]
    seed = start + span - 1
    out[seed] = float(np.mean(arr[start : seed + 1]))
    for i in range(seed + 1, len(arr)):
        out[i] = a * arr[i] + (1 - a) * out[i - 1]
    return pd.Series(out, index=s.index)


# --------------------------------------------------------------------------- #
# SMA — literal hand check
# --------------------------------------------------------------------------- #
def test_sma_literal():
    s = pd.Series([10.0, 11.0, 12.0, 13.0])
    out = T.sma(s, 3)
    assert np.isnan(out.iloc[0]) and np.isnan(out.iloc[1])
    assert out.iloc[2] == pytest.approx(11.0)  # (10+11+12)/3
    assert out.iloc[3] == pytest.approx(12.0)  # (11+12+13)/3


def test_sma_rejects_bad_period():
    s = pd.Series([1.0, 2.0, 3.0])
    for bad in (0, -1, 2.5, True):
        with pytest.raises(ValueError):
            T.sma(s, bad)  # type: ignore[arg-type]


def test_volume_avg_is_sma():
    v = pd.Series([100.0, 200.0, 300.0, 400.0])
    pd.testing.assert_series_equal(T.volume_avg(v, 2), T.sma(v, 2))


# --------------------------------------------------------------------------- #
# RSI — literal hand check + invariants
# --------------------------------------------------------------------------- #
def test_rsi_literal_period2():
    # close 10,11,10,11,12 ; period 2 (worked out by hand in the test docstring)
    #   changes:  +1,-1,+1,+1
    #   idx2: ag=mean(1,0)=.5  al=mean(0,1)=.5  -> RS=1   -> RSI=50
    #   idx3: ag=(.5+1)/2=.75  al=(.5+0)/2=.25  -> RS=3   -> RSI=75
    #   idx4: ag=(.75+1)/2=.875 al=(.25+0)/2=.125 -> RS=7 -> RSI=87.5
    close = pd.Series([10.0, 11.0, 10.0, 11.0, 12.0])
    out = T.rsi(close, 2)
    assert np.isnan(out.iloc[0]) and np.isnan(out.iloc[1])
    assert out.iloc[2] == pytest.approx(50.0)
    assert out.iloc[3] == pytest.approx(75.0)
    assert out.iloc[4] == pytest.approx(87.5)


def test_rsi_matches_reference_classic_series():
    close = pd.Series(
        [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
         45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64]
    )
    got = T.rsi(close, 14)
    d = close.diff()
    ref = 100 - 100 / (
        1 + _ref_wilder(d.clip(lower=0), 14) / _ref_wilder((-d).clip(lower=0), 14)
    )
    common = got.dropna().index
    assert np.allclose(got.loc[common], ref.loc[common], atol=1e-9)
    # first RSI lands at index 14 and is a healthy-uptrend reading
    assert np.isnan(got.iloc[13]) and not np.isnan(got.iloc[14])
    assert 65.0 < got.iloc[14] < 75.0


def test_rsi_all_gains_is_100():
    out = T.rsi(pd.Series(np.arange(1, 30, dtype=float)), 14).dropna()
    assert np.allclose(out.values, 100.0)


def test_rsi_all_losses_is_0():
    out = T.rsi(pd.Series(np.arange(30, 1, -1, dtype=float)), 14).dropna()
    assert np.allclose(out.values, 0.0)


def test_rsi_bounded_0_100():
    rng = np.sin(np.arange(100) / 4.0) * 5 + 50
    out = T.rsi(pd.Series(rng), 14).dropna()
    assert out.min() >= 0.0 and out.max() <= 100.0


# --------------------------------------------------------------------------- #
# ATR — literal hand check + invariant
# --------------------------------------------------------------------------- #
def test_atr_literal_period2():
    # high 12,14,11 ; low 8,9,7 ; close 10,11,9 ; period 2
    #   TR0 = 12-8 = 4
    #   TR1 = max(14-9, |14-10|, |9-10|) = 5
    #   TR2 = max(11-7, |11-11|, |7-11|) = 4
    #   ATR seed idx1 = mean(4,5) = 4.5 ; ATR2 = (4.5+4)/2 = 4.25
    high = pd.Series([12.0, 14.0, 11.0])
    low = pd.Series([8.0, 9.0, 7.0])
    close = pd.Series([10.0, 11.0, 9.0])
    out = T.atr(high, low, close, 2)
    assert np.isnan(out.iloc[0])
    assert out.iloc[1] == pytest.approx(4.5)
    assert out.iloc[2] == pytest.approx(4.25)


def test_atr_constant_range_converges_to_range():
    n = 60
    close = pd.Series(np.full(n, 100.0))
    out = T.atr(close + 1.0, close - 1.0, close, 14).dropna()
    assert out.iloc[-1] == pytest.approx(2.0, abs=1e-9)


def test_atr_matches_reference():
    rng = np.random.default_rng(7)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, 60)))
    high = close + rng.uniform(0.2, 1.0, 60)
    low = close - rng.uniform(0.2, 1.0, 60)
    got = T.atr(high, low, close, 14)
    ref = _ref_wilder(T.true_range(high, low, close), 14)
    common = got.dropna().index
    assert np.allclose(got.loc[common], ref.loc[common], atol=1e-9)


# --------------------------------------------------------------------------- #
# MACD — EMA reference + hist invariant
# --------------------------------------------------------------------------- #
def test_macd_matches_reference_and_hist_invariant():
    close = pd.Series(100 + np.cumsum(np.sin(np.arange(80) / 3.0)))
    out = T.macd(close, 12, 26, 9)
    line = _ref_ema(close, 12) - _ref_ema(close, 26)
    sig = _ref_ema(line, 9)
    common = out.dropna().index
    assert np.allclose(out["macd"].loc[common], line.loc[common], atol=1e-9)
    assert np.allclose(out["signal"].loc[common], sig.loc[common], atol=1e-9)
    assert np.allclose(
        out["hist"].loc[common], (out["macd"] - out["signal"]).loc[common], atol=1e-12
    )


def test_macd_rejects_fast_ge_slow():
    close = pd.Series(np.arange(50, dtype=float))
    with pytest.raises(ValueError, match="fast"):
        T.macd(close, 26, 12, 9)


# --------------------------------------------------------------------------- #
# add_indicators — enriched frame contract
# --------------------------------------------------------------------------- #
def _ohlcv(n: int = 260) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    close = pd.Series(100 + np.cumsum(rng.normal(0.05, 1.0, n)))
    return pd.DataFrame(
        {
            "date": pd.bdate_range("2023-01-02", periods=n),
            "open": close.shift(1).fillna(close),
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "adj_close": close,
            "volume": pd.Series(np.full(n, 1_000_000.0)),
        }
    )


def test_add_indicators_adds_expected_columns():
    cfg = load_config()
    out = T.add_indicators(_ohlcv(), cfg)
    for p in T.sma_periods(cfg):
        assert T.sma_col(p) in out.columns
    for col in ("rsi", "macd", "macd_signal", "macd_hist", "atr", "vol_avg"):
        assert col in out.columns
    assert set(T.sma_periods(cfg)) >= {20, 50, 200}
    last = out.iloc[-1]
    assert last[["rsi", "macd", "atr", "vol_avg", "sma_200"]].notna().all()
    assert last["vol_avg"] == pytest.approx(1_000_000.0)


def test_add_indicators_requires_ohlcv():
    bad = pd.DataFrame({"close": [1.0, 2.0]})
    with pytest.raises(ValueError, match="missing OHLCV"):
        T.add_indicators(bad, load_config())
