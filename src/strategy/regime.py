"""Regime filter — trend gate + market filter (Rulebook §4).

Long-only, uptrends only. A NAME passes the per-name trend gate when ALL hold:
  * price > long SMA (default 200),
  * mid SMA (50) > long SMA (200),
  * long SMA is rising over the slope lookback (default 20 days).

Separately, the MARKET filter is a global switch: no NEW longs when the index
(default ^GSPC) is below its own long SMA (default 200). Existing positions are
still managed when the filter is OFF — that gating lives in the engine.

All periods come from config. Evaluated at a point in time (`asof`, default last
bar) so the same logic serves both live recommendations and future backtests.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..config_loader import Config
from ..indicators.technicals import sma


@dataclass(frozen=True)
class RegimeResult:
    ticker: str | None
    passed: bool
    price_above_long: bool
    mid_above_long: bool
    long_slope_rising: bool
    close: float
    sma_mid: float
    sma_long: float
    sma_long_prev: float


@dataclass(frozen=True)
class MarketFilter:
    symbol: str
    on: bool
    close: float
    sma: float


def _pos(asof: int, n: int) -> int:
    """Normalise a possibly-negative positional index to 0..n-1."""
    return asof if asof >= 0 else n + asof


def evaluate_regime(
    df: pd.DataFrame, cfg: Config, *, asof: int = -1, ticker: str | None = None
) -> RegimeResult:
    """Evaluate the per-name trend gate at `asof` (positional index, default last)."""
    close = df["close"].reset_index(drop=True)
    n = len(close)
    i = _pos(asof, n)

    long_p = cfg.regime.trend_ma_long
    mid_p = cfg.regime.trend_ma_mid
    lookback = cfg.regime.trend_slope_lookback

    sma_long = sma(close, long_p)
    sma_mid = sma(close, mid_p)

    c = float(close.iloc[i])
    sl = sma_long.iloc[i]
    sm = sma_mid.iloc[i]
    sl_prev = sma_long.iloc[i - lookback] if i - lookback >= 0 else float("nan")

    price_above = bool(pd.notna(sl) and c > sl)
    mid_above = bool(pd.notna(sm) and pd.notna(sl) and sm > sl)
    slope_rising = bool(pd.notna(sl) and pd.notna(sl_prev) and sl > sl_prev)
    passed = price_above and mid_above and slope_rising

    return RegimeResult(
        ticker=ticker,
        passed=passed,
        price_above_long=price_above,
        mid_above_long=mid_above,
        long_slope_rising=slope_rising,
        close=c,
        sma_mid=float(sm) if pd.notna(sm) else float("nan"),
        sma_long=float(sl) if pd.notna(sl) else float("nan"),
        sma_long_prev=float(sl_prev) if pd.notna(sl_prev) else float("nan"),
    )


def evaluate_market_filter(
    index_df: pd.DataFrame, cfg: Config, *, asof: int = -1
) -> MarketFilter:
    """Market filter: index close > its long SMA. OFF (no new longs) if undefined."""
    close = index_df["close"].reset_index(drop=True)
    n = len(close)
    i = _pos(asof, n)
    ma = sma(close, cfg.regime.market_filter_ma)
    c = float(close.iloc[i])
    v = ma.iloc[i]
    on = bool(pd.notna(v) and c > v)
    return MarketFilter(
        symbol=cfg.regime.market_filter_symbol,
        on=on,
        close=c,
        sma=float(v) if pd.notna(v) else float("nan"),
    )
