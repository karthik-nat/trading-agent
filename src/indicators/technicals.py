"""Technical indicators (Rulebook §5 / §8) — hand-rolled, single consistent convention.

We implement the small indicator set directly (no third-party TA lib) so that:
  * every value is independently hand-verifiable (the Phase 1 gate requires
    hand-checked fixtures, and stops/sizes/R-multiples depend on ATR exactly), and
  * one consistent **classic** convention is used everywhere, matching the
    charting platforms a user cross-references (TradingView / StockCharts).

Conventions (pinned by literal fixtures in tests/test_technicals.py):
  * SMA   : simple rolling mean.
  * RSI   : Wilder, **SMA-seeded** — first avg gain/loss = mean of the first
            `period` changes, then Wilder recursion. First value at index `period`.
  * ATR   : Wilder, **SMA-seeded** RMA of True Range. First value at index `period-1`.
  * MACD  : EMA(fast) - EMA(slow); each EMA **SMA-seeded** (first value = SMA of the
            first `span`), alpha = 2/(span+1). signal = EMA(signal) of the MACD line;
            hist = MACD - signal.

Note: seeding affects only the warm-up region; with `min_history_days` (250) of
history the values used for live decisions are fully converged. All functions are
PURE — they take a series + an explicit period (sourced from config, never
hardcoded) and return a series aligned to the input index, NaN where undefined.
No strategy logic here.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from ..config_loader import Config


# --------------------------------------------------------------------------- #
# Low-level smoothers (SMA-seeded recursions)
# --------------------------------------------------------------------------- #
def _wilder_rma(s: pd.Series, period: int) -> pd.Series:
    """Wilder's RMA, SMA-seeded. Assumes values are contiguous after the first
    non-NaN (true for diff/TR series used here). First output at start+period-1."""
    arr = s.to_numpy(dtype=float)
    out = np.full(arr.shape, np.nan)
    valid = np.flatnonzero(~np.isnan(arr))
    if valid.size < period:
        return pd.Series(out, index=s.index, name=s.name)
    start = int(valid[0])
    seed = start + period - 1
    out[seed] = arr[start : seed + 1].mean()
    for i in range(seed + 1, arr.size):
        out[i] = (out[i - 1] * (period - 1) + arr[i]) / period
    return pd.Series(out, index=s.index, name=s.name)


def _ema(s: pd.Series, span: int) -> pd.Series:
    """EMA, SMA-seeded (first value = SMA of first `span`), alpha = 2/(span+1)."""
    arr = s.to_numpy(dtype=float)
    out = np.full(arr.shape, np.nan)
    valid = np.flatnonzero(~np.isnan(arr))
    if valid.size < span:
        return pd.Series(out, index=s.index, name=s.name)
    alpha = 2.0 / (span + 1.0)
    start = int(valid[0])
    seed = start + span - 1
    out[seed] = arr[start : seed + 1].mean()
    for i in range(seed + 1, arr.size):
        out[i] = alpha * arr[i] + (1.0 - alpha) * out[i - 1]
    return pd.Series(out, index=s.index, name=s.name)


# --------------------------------------------------------------------------- #
# Pure indicator functions
# --------------------------------------------------------------------------- #
def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple moving average; NaN until `period` observations exist."""
    _require_period(period)
    return series.rolling(window=period, min_periods=period).mean()


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """True Range. First bar (no prior close) = high - low."""
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.rename("true_range")


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """Average True Range — SMA-seeded Wilder RMA of True Range."""
    _require_period(period)
    return _wilder_rma(true_range(high, low, close), period).rename("atr")


def rsi(close: pd.Series, period: int) -> pd.Series:
    """Wilder's RSI, SMA-seeded. all-gains -> 100, all-losses -> 0."""
    _require_period(period)
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = _wilder_rma(gain, period)
    avg_loss = _wilder_rma(loss, period)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss            # avg_loss==0 -> inf -> rsi 100
        out = 100.0 - 100.0 / (1.0 + rs)
    return out.rename("rsi")


def macd(close: pd.Series, fast: int, slow: int, signal: int) -> pd.DataFrame:
    """MACD as a DataFrame with columns ['macd', 'signal', 'hist']."""
    for p in (fast, slow, signal):
        _require_period(p)
    if not fast < slow:
        raise ValueError(f"macd: fast ({fast}) must be < slow ({slow})")
    line = (_ema(close, fast) - _ema(close, slow)).rename("macd")
    sig = _ema(line, signal).rename("signal")
    hist = (line - sig).rename("hist")
    return pd.DataFrame({"macd": line, "signal": sig, "hist": hist}, index=close.index)


def volume_avg(volume: pd.Series, period: int) -> pd.Series:
    """Rolling average volume (used for the §5 E4 volume-confirm test)."""
    return sma(volume, period)


# --------------------------------------------------------------------------- #
# Enriched-frame builder used by the engine
# --------------------------------------------------------------------------- #
def sma_periods(cfg: "Config") -> list[int]:
    """All distinct SMA periods the config references, ascending."""
    periods = {
        cfg.entry.trend_ma_short,
        cfg.regime.trend_ma_mid,
        cfg.regime.trend_ma_long,
        cfg.exits.trail_ma,
        cfg.exits.trend_break_ma,
    }
    return sorted(periods)


def sma_col(period: int) -> str:
    """Canonical column name for an SMA of a given period (e.g. 'sma_200')."""
    return f"sma_{period}"


def add_indicators(df: pd.DataFrame, cfg: "Config") -> pd.DataFrame:
    """Return a copy of a canonical OHLCV frame with indicator columns added.

    Columns added (periods all sourced from config):
      sma_<p> for every distinct period in sma_periods(cfg),
      rsi, macd, macd_signal, macd_hist, atr, vol_avg.
    """
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"add_indicators: missing OHLCV columns {sorted(missing)}")

    out = df.copy()
    close, high, low, vol = out["close"], out["high"], out["low"], out["volume"]

    for p in sma_periods(cfg):
        out[sma_col(p)] = sma(close, p)

    out["rsi"] = rsi(close, cfg.entry.rsi_period)
    mac = macd(close, cfg.entry.macd_fast, cfg.entry.macd_slow, cfg.entry.macd_signal)
    out["macd"] = mac["macd"]
    out["macd_signal"] = mac["signal"]
    out["macd_hist"] = mac["hist"]
    out["atr"] = atr(high, low, close, cfg.exits.atr_period)
    out["vol_avg"] = volume_avg(vol, cfg.entry.volume_avg_period)
    return out


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _require_period(period: int) -> None:
    if not isinstance(period, int) or isinstance(period, bool) or period <= 0:
        raise ValueError(f"indicator period must be a positive int, got {period!r}")
