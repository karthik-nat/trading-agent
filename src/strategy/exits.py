"""Exit rules — defined in R-multiples (Rulebook §8). 1R = entry - initial_stop.

  * Initial stop : max(atr_mult*ATR below entry, recent swing low) — the TIGHTER
                   (higher) of the two; never wider than atr_mult*ATR.
  * First scale  : sell `first_scale_fraction` at +`first_scale_r`R.
  * Breakeven    : once +`breakeven_trigger_r`R is reached, raise stop to entry.
  * Trail        : max(trail_ma SMA, close - trail_atr_mult*ATR), ratcheted up.
  * Trend break  : close < trend_break_ma SMA, OR bearish MACD cross + price rolling over.
  * Time stop    : exit if not +`time_stop_min_r`R within `time_stop_days` bars.

Stateless: every call recomputes from the price path between entry and `asof`,
using intraday highs to detect "reached +NR". All numbers come from config.

The "recent swing low" window is `exits.swing_lookback_days` (config). The
hard-event (earnings) exit is deferred — it needs an earnings calendar (a later
data-source concern).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ..config_loader import Config
from ..indicators.technicals import atr as atr_ind
from ..indicators.technicals import macd, sma

# actions
HOLD = "HOLD"
TRIM = "TRIM"
EXIT = "EXIT"


@dataclass(frozen=True)
class Position:
    ticker: str
    entry_price: float
    initial_stop: float
    entry_index: int            # positional index in the price frame at entry
    shares: float = 0.0
    scaled: bool = False        # has the +NR first tranche already been sold?


@dataclass(frozen=True)
class InitialStopResult:
    stop: float
    atr_stop: float
    swing_low: float
    atr: float
    r_per_share: float
    method: str                 # "atr" | "swing_low"


@dataclass(frozen=True)
class ExitDecision:
    action: str                 # HOLD | TRIM | EXIT
    reason_codes: list[str] = field(default_factory=list)
    current_r: float = float("nan")
    max_r: float = float("nan")
    suggested_stop: float = float("nan")
    scale_target: float = float("nan")
    breakeven_active: bool = False
    bars_held: int = 0


def _pos(asof: int, n: int) -> int:
    return asof if asof >= 0 else n + asof


# --------------------------------------------------------------------------- #
# Building blocks
# --------------------------------------------------------------------------- #
def swing_low(df: pd.DataFrame, *, asof: int = -1, lookback: int) -> float:
    low = df["low"].reset_index(drop=True)
    i = _pos(asof, len(low))
    start = max(0, i - lookback + 1)
    return float(low.iloc[start : i + 1].min())


def compute_initial_stop(
    df: pd.DataFrame, entry_price: float, cfg: Config, *, asof: int = -1
) -> InitialStopResult:
    """Initial stop = tighter (higher) of (entry - atr_mult*ATR) and recent swing low."""
    close, high, low = df["close"], df["high"], df["low"]
    i = _pos(asof, len(close))
    a = atr_ind(high, low, close, cfg.exits.atr_period).iloc[i]
    if pd.isna(a):
        raise ValueError("compute_initial_stop: ATR undefined (insufficient history)")
    a = float(a)
    atr_stop = entry_price - cfg.exits.initial_stop_atr_mult * a

    sl = (
        swing_low(df, asof=asof, lookback=cfg.exits.swing_lookback_days)
        if cfg.exits.use_swing_low
        else float("-inf")
    )
    chosen = max(atr_stop, sl)
    method = "swing_low" if (cfg.exits.use_swing_low and sl > atr_stop) else "atr"
    if chosen >= entry_price:        # swing low above entry -> fall back to ATR stop
        chosen, method = atr_stop, "atr"
    return InitialStopResult(
        stop=float(chosen), atr_stop=float(atr_stop),
        swing_low=float(sl) if sl != float("-inf") else float("nan"),
        atr=a, r_per_share=float(entry_price - chosen), method=method,
    )


def r_per_share(entry_price: float, initial_stop: float) -> float:
    r = entry_price - initial_stop
    if r <= 0:
        raise ValueError(f"1R must be > 0 (entry {entry_price} <= stop {initial_stop})")
    return float(r)


def unrealized_r(entry_price: float, initial_stop: float, price: float) -> float:
    return float((price - entry_price) / r_per_share(entry_price, initial_stop))


def first_scale_target(entry_price: float, initial_stop: float, cfg: Config) -> float:
    return float(entry_price + cfg.exits.first_scale_r * r_per_share(entry_price, initial_stop))


def trailing_stop(df: pd.DataFrame, cfg: Config, *, asof: int = -1) -> float:
    close, high, low = df["close"], df["high"], df["low"]
    i = _pos(asof, len(close))
    sma_trail = sma(close, cfg.exits.trail_ma).iloc[i]
    a = atr_ind(high, low, close, cfg.exits.atr_period).iloc[i]
    if pd.isna(sma_trail) or pd.isna(a):
        return float("nan")
    return float(max(float(sma_trail), float(close.iloc[i]) - cfg.exits.trail_atr_mult * float(a)))


def is_trend_break(df: pd.DataFrame, cfg: Config, *, asof: int = -1) -> tuple[bool, list[str]]:
    """Close < trend_break_ma SMA, OR bearish MACD cross with price rolling over."""
    close = df["close"]
    i = _pos(asof, len(close))
    reasons: list[str] = []
    sma_break = sma(close, cfg.exits.trend_break_ma).iloc[i]
    if pd.notna(sma_break) and float(close.iloc[i]) < float(sma_break):
        reasons.append("close_below_50sma")
    if i >= 1:
        mac = macd(close, cfg.entry.macd_fast, cfg.entry.macd_slow, cfg.entry.macd_signal)
        mt, mp = mac["macd"].iloc[i], mac["macd"].iloc[i - 1]
        st, sp = mac["signal"].iloc[i], mac["signal"].iloc[i - 1]
        rolling_over = float(close.iloc[i]) < float(close.iloc[i - 1])
        if pd.notna(mt) and pd.notna(mp):
            bearish_cross = (float(mp) >= float(sp)) and (float(mt) < float(st))
            if bearish_cross and rolling_over:
                reasons.append("bearish_macd_cross")
    return (len(reasons) > 0, reasons)


def is_time_stop(bars_held: int, max_r: float, cfg: Config) -> bool:
    return bars_held >= cfg.exits.time_stop_days and max_r < cfg.exits.time_stop_min_r


# --------------------------------------------------------------------------- #
# Full position evaluation
# --------------------------------------------------------------------------- #
def evaluate_exit(
    position: Position, df: pd.DataFrame, cfg: Config, *, asof: int = -1
) -> ExitDecision:
    """Evaluate all §8 exit conditions for an open position at `asof`."""
    close = df["close"].reset_index(drop=True)
    high = df["high"].reset_index(drop=True)
    i = _pos(asof, len(close))
    entry_i = position.entry_index
    bars_held = i - entry_i

    R = r_per_share(position.entry_price, position.initial_stop)
    close_i = float(close.iloc[i])
    current_r = (close_i - position.entry_price) / R

    # max favourable excursion since entry (intraday highs)
    seg_high = high.iloc[entry_i : i + 1]
    max_high = float(seg_high.max()) if len(seg_high) else close_i
    max_r = (max_high - position.entry_price) / R

    reached_1r = max_r >= cfg.exits.breakeven_trigger_r
    reached_scale = max_r >= cfg.exits.first_scale_r
    scale_target = first_scale_target(position.entry_price, position.initial_stop, cfg)

    # ratcheted stop ladder
    stop = position.initial_stop
    if reached_1r:
        stop = max(stop, position.entry_price)                 # breakeven
    if position.scaled or reached_scale:
        ts = trailing_stop(df, cfg, asof=asof)
        if pd.notna(ts):
            stop = max(stop, ts)                               # trail the runner
    suggested_stop = float(stop)

    reasons: list[str] = []
    exit_now = False
    if close_i <= suggested_stop:
        reasons.append("stop_hit")
        exit_now = True
    tb, tb_reasons = is_trend_break(df, cfg, asof=asof)
    if tb:
        reasons += tb_reasons
        exit_now = True
    if is_time_stop(bars_held, max_r, cfg):
        reasons.append("time_stop")
        exit_now = True

    if exit_now:
        action = EXIT
    elif reached_scale and not position.scaled:
        action = TRIM
        reasons.append("scale_first_tranche")
    else:
        action = HOLD

    return ExitDecision(
        action=action,
        reason_codes=reasons,
        current_r=float(current_r),
        max_r=float(max_r),
        suggested_stop=suggested_stop,
        scale_target=float(scale_target),
        breakeven_active=bool(reached_1r),
        bars_held=int(bars_held),
    )
