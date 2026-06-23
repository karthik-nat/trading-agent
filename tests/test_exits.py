"""Exit-rule tests (Rulebook §8) — initial stop, scale, breakeven, trail, breaks."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.config_loader import load_config
from src.strategy.exits import (
    EXIT,
    HOLD,
    TRIM,
    Position,
    compute_initial_stop,
    evaluate_exit,
    first_scale_target,
    is_time_stop,
    is_trend_break,
    r_per_share,
    trailing_stop,
    unrealized_r,
)

CFG = load_config()  # atr_mult 1.5, first_scale_r 2.0, breakeven 1.0, trail 20/2.5, break 50, time 20


def _ohlc(close: np.ndarray, rng: float = 1.0) -> pd.DataFrame:
    close = np.asarray(close, dtype=float)
    return pd.DataFrame(
        {"open": close, "high": close + rng, "low": close - rng,
         "close": close, "volume": np.full(len(close), 1e6)}
    )


# --------------------------------------------------------------------------- #
# Initial stop: tighter of ATR-stop vs swing low
# --------------------------------------------------------------------------- #
def test_initial_stop_swing_low_binds_when_higher():
    df = _ohlc(np.full(30, 100.0))           # ATR == 2 -> atr_stop = 97; swing low = 99
    r = compute_initial_stop(df, 100.0, CFG)
    assert r.atr == pytest.approx(2.0)
    assert r.atr_stop == pytest.approx(97.0)
    assert r.swing_low == pytest.approx(99.0)
    assert r.method == "swing_low"
    assert r.stop == pytest.approx(99.0)     # tighter (higher) wins
    assert r.r_per_share == pytest.approx(1.0)


def test_initial_stop_atr_binds_when_swing_low_lower():
    close = np.full(30, 100.0)
    df = _ohlc(close)
    df.loc[27, "low"] = 94.0                 # a deep recent swing low (below atr_stop)
    r = compute_initial_stop(df, 100.0, CFG)
    assert r.swing_low == pytest.approx(94.0)
    assert r.method == "atr"
    assert r.stop == pytest.approx(r.atr_stop)
    assert r.stop < 100.0


# --------------------------------------------------------------------------- #
# R math + scale target
# --------------------------------------------------------------------------- #
def test_r_and_unrealized():
    assert r_per_share(100, 95) == 5.0
    assert unrealized_r(100, 95, 105) == pytest.approx(1.0)
    assert unrealized_r(100, 95, 95) == pytest.approx(-1.0)
    with pytest.raises(ValueError):
        r_per_share(100, 100)


def test_first_scale_target_is_plus_2r():
    assert first_scale_target(100, 95, CFG) == pytest.approx(110.0)  # 100 + 2*5


def test_trailing_stop_takes_higher_of_ma_and_atr_trail():
    df = _ohlc(np.full(40, 100.0))           # sma20 = 100, close-2.5*ATR = 95 -> 100
    assert trailing_stop(df, CFG) == pytest.approx(100.0)


# --------------------------------------------------------------------------- #
# evaluate_exit — each exit pathway
# --------------------------------------------------------------------------- #
def test_stop_hit_exits():
    close = np.r_[np.full(24, 100.0), 94.0]  # last close below the 95 stop
    pos = Position("S", entry_price=100.0, initial_stop=95.0, entry_index=20)
    d = evaluate_exit(pos, _ohlc(close), CFG)
    assert d.action == EXIT
    assert "stop_hit" in d.reason_codes


def test_breakeven_then_pullback_exits_at_entry():
    # rises to +1R (raises stop to entry), then falls back below entry
    close = np.r_[np.full(20, 100.0), [102.0, 104.0, 106.0, 103.0, 99.0]]
    pos = Position("BE", entry_price=100.0, initial_stop=95.0, entry_index=20)
    d = evaluate_exit(pos, _ohlc(close), CFG)
    assert d.breakeven_active is True
    assert d.suggested_stop == pytest.approx(100.0)   # raised to breakeven
    assert d.action == EXIT and "stop_hit" in d.reason_codes


def test_reached_plus_2r_trims():
    close = np.linspace(100, 115, 40)        # max high ~116 -> max_r > 2
    pos = Position("TR", entry_price=100.0, initial_stop=95.0, entry_index=0)
    d = evaluate_exit(pos, _ohlc(close), CFG)
    assert d.max_r >= CFG.exits.first_scale_r
    assert d.action == TRIM
    assert "scale_first_tranche" in d.reason_codes


def test_healthy_trend_holds_with_breakeven():
    close = np.r_[np.full(20, 100.0), [103.0, 106.0, 107.0, 106.0, 106.0]]  # +1.4R, sits
    pos = Position("H", entry_price=100.0, initial_stop=95.0, entry_index=20)
    d = evaluate_exit(pos, _ohlc(close), CFG)
    assert d.action == HOLD
    assert d.breakeven_active is True
    assert d.suggested_stop == pytest.approx(100.0)


def test_time_stop_exits_when_no_progress():
    close = np.full(25, 100.0)               # never reaches +1R, 24 bars held
    pos = Position("T", entry_price=100.0, initial_stop=95.0, entry_index=0)
    d = evaluate_exit(pos, _ohlc(close), CFG)
    assert d.bars_held >= CFG.exits.time_stop_days
    assert d.max_r < CFG.exits.time_stop_min_r
    assert d.action == EXIT and "time_stop" in d.reason_codes


def test_trend_break_below_50sma_exits():
    close = np.r_[np.linspace(100, 140, 50), [135.0, 128.0, 120.0, 110.0]]
    df = _ohlc(close)
    broke, reasons = is_trend_break(df, CFG)
    assert broke and "close_below_50sma" in reasons
    pos = Position("TB", entry_price=100.0, initial_stop=95.0, entry_index=10)
    d = evaluate_exit(pos, df, CFG)
    assert d.action == EXIT and "close_below_50sma" in d.reason_codes


# --------------------------------------------------------------------------- #
# Unit predicates
# --------------------------------------------------------------------------- #
def test_is_time_stop_predicate():
    assert is_time_stop(20, 0.5, CFG) is True
    assert is_time_stop(19, 0.5, CFG) is False    # not enough bars
    assert is_time_stop(25, 1.0, CFG) is False    # reached +1R
