"""Regime gate tests (Rulebook §4) — series that MUST and MUST NOT pass."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.config_loader import load_config
from src.strategy.regime import evaluate_market_filter, evaluate_regime

CFG = load_config()


def _df(close: np.ndarray) -> pd.DataFrame:
    c = pd.Series(close, dtype=float)
    return pd.DataFrame(
        {"open": c, "high": c + 1.0, "low": c - 1.0, "close": c,
         "volume": pd.Series(np.full(len(c), 1e6))}
    )


# --------------------------------------------------------------------------- #
# Per-name trend gate
# --------------------------------------------------------------------------- #
def test_clean_uptrend_passes():
    r = evaluate_regime(_df(np.linspace(50, 150, 260)), CFG, ticker="UP")
    assert r.passed
    assert r.price_above_long and r.mid_above_long and r.long_slope_rising
    assert r.close > r.sma_long and r.sma_mid > r.sma_long


def test_clean_downtrend_fails_all():
    r = evaluate_regime(_df(np.linspace(150, 50, 260)), CFG, ticker="DOWN")
    assert not r.passed
    assert not r.price_above_long
    assert not r.mid_above_long
    assert not r.long_slope_rising


def test_uptrend_with_recent_pullback_still_passes():
    close = np.linspace(50, 150, 260)
    close[-3:] -= np.array([2.0, 4.0, 6.0])  # small dip into the rising 20-SMA
    r = evaluate_regime(_df(close), CFG)
    assert r.passed  # price still above 200-SMA, 50>200, slope rising


def test_flat_series_does_not_pass():
    # no trend: price == SMA == prev SMA -> none of the strict '>' conditions hold
    r = evaluate_regime(_df(np.full(260, 100.0)), CFG)
    assert not r.passed
    assert not r.price_above_long and not r.long_slope_rising


def test_slope_flag_tracks_direction():
    up = evaluate_regime(_df(np.linspace(50, 150, 260)), CFG)
    down = evaluate_regime(_df(np.linspace(150, 50, 260)), CFG)
    assert up.long_slope_rising is True
    assert down.long_slope_rising is False


def test_insufficient_history_fails_safe():
    r = evaluate_regime(_df(np.linspace(50, 60, 30)), CFG)  # < 200 bars
    assert not r.passed
    assert np.isnan(r.sma_long)


def test_asof_evaluates_in_the_past():
    # rising then crashing; early bar is mid-uptrend, last bar is post-crash
    close = np.concatenate([np.linspace(50, 150, 230), np.linspace(150, 80, 30)])
    df = _df(close)
    late = evaluate_regime(df, CFG, asof=-1)
    early = evaluate_regime(df, CFG, asof=229)
    assert early.passed and not late.price_above_long


# --------------------------------------------------------------------------- #
# Market filter (global switch)
# --------------------------------------------------------------------------- #
def test_market_filter_on_when_index_above_ma():
    mf = evaluate_market_filter(_df(np.linspace(3000, 5000, 260)), CFG)
    assert mf.on is True
    assert mf.symbol == CFG.regime.market_filter_symbol
    assert mf.close > mf.sma


def test_market_filter_off_when_index_below_ma():
    mf = evaluate_market_filter(_df(np.linspace(5000, 3000, 260)), CFG)
    assert mf.on is False


def test_market_filter_off_when_insufficient_history():
    mf = evaluate_market_filter(_df(np.linspace(3000, 3100, 50)), CFG)
    assert mf.on is False
    assert np.isnan(mf.sma)
