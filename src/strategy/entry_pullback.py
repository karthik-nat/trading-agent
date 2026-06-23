"""Trend-pullback entry engine (Rulebook §5) — the primary buy trigger.

Among names already passing the §3 universe gate and §4 regime, a BUY candidate
is generated when the pullback-and-resume pattern confirms. Confluence of four
conditions (all required by default; tunable via `entry.confluence_required`):

  E1 pullback-to-MA  : close within `pullback_proximity_pct` of the 20-SMA,
                       OR a low tagged the 50-SMA in the last 1-3 days.
  E2 RSI turn        : RSI(14) recently in the 40-50 zone AND turning up today.
  E3 MACD resume     : bullish MACD/signal cross, OR histogram up-tick (rising).
  E4 volume confirm  : resumption-day volume >= `volume_confirm_mult` x 20-day avg.

Anti-chase guard: reject if price is > `anti_chase_max_ext_pct` above the 20-SMA.

Ranking (when more candidates than open slots): a weight-free **rank-sum** composite
of relative strength vs the market and reward/risk (tighter stop = better). This is
the chosen interpretation of the rulebook's qualitative "composite"; ranking weights
are not in config, so no weight is invented — see rank_candidates().

All thresholds/periods come from config. No sizing or order logic here.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..config_loader import Config
from ..indicators.technicals import macd, rsi, sma, volume_avg

# "in the last 1-3 days" pullback window (rulebook E1 language); reused for the
# E2 "dipped into the zone during the pullback" check rather than inventing a knob.
PULLBACK_LOOKBACK = 3


@dataclass(frozen=True)
class EntrySignal:
    ticker: str | None
    triggered: bool
    confluence_count: int
    e1_pullback: bool
    e2_rsi_turn: bool
    e3_macd_resume: bool
    e4_volume: bool
    anti_chase: bool
    # transparency values
    close: float
    sma_short: float
    extension_pct: float       # (close - 20SMA)/20SMA * 100
    rsi: float
    rsi_prev: float
    macd: float
    macd_signal: float
    macd_hist: float
    macd_above_zero: bool
    volume: float
    volume_avg: float
    volume_ratio: float


def _pos(asof: int, n: int) -> int:
    return asof if asof >= 0 else n + asof


def _f(x) -> float:
    return float(x) if pd.notna(x) else float("nan")


def evaluate_entry(
    df: pd.DataFrame, cfg: Config, *, asof: int = -1, ticker: str | None = None
) -> EntrySignal:
    """Evaluate the §5 confluence at `asof` (positional index, default last)."""
    e = cfg.entry
    close = df["close"].reset_index(drop=True)
    low = df["low"].reset_index(drop=True)
    volume = df["volume"].reset_index(drop=True)
    n = len(close)
    i = _pos(asof, n)

    sma_short = sma(close, e.trend_ma_short)
    sma_mid = sma(close, cfg.regime.trend_ma_mid)
    rsi_s = rsi(close, e.rsi_period)
    mac = macd(close, e.macd_fast, e.macd_slow, e.macd_signal)
    vavg = volume_avg(volume, e.volume_avg_period)

    # Insufficient history -> no signal (fail safe).
    if i < 1:
        return _blank_signal(ticker, _f(close.iloc[i]))
    ss = sma_short.iloc[i]
    if pd.isna(ss) or pd.isna(rsi_s.iloc[i]) or pd.isna(rsi_s.iloc[i - 1]) \
            or pd.isna(mac["hist"].iloc[i]) or pd.isna(mac["hist"].iloc[i - 1]) \
            or pd.isna(vavg.iloc[i]):
        return _blank_signal(ticker, _f(close.iloc[i]))

    c = float(close.iloc[i])
    ss = float(ss)
    extension_pct = (c - ss) / ss * 100.0

    # E1: near 20-SMA, or a recent low tagged the 50-SMA.
    near_short = abs(c - ss) / ss * 100.0 <= e.pullback_proximity_pct
    tagged_mid = False
    for j in range(max(0, i - (PULLBACK_LOOKBACK - 1)), i + 1):
        sm = sma_mid.iloc[j]
        if pd.notna(sm) and low.iloc[j] <= sm:
            tagged_mid = True
            break
    e1 = bool(near_short or tagged_mid)

    # E2: RSI recently in [low, high] zone AND turning up today.
    rsi_today = float(rsi_s.iloc[i])
    rsi_prev = float(rsi_s.iloc[i - 1])
    in_zone = False
    for j in range(max(0, i - (PULLBACK_LOOKBACK - 1)), i + 1):
        rj = rsi_s.iloc[j]
        if pd.notna(rj) and e.rsi_turn_low <= rj <= e.rsi_turn_high:
            in_zone = True
            break
    turning_up = (rsi_today > rsi_prev) if e.rsi_must_turn_up else True
    e2 = bool(in_zone and turning_up)

    # E3: MACD resume = bullish cross OR histogram up-tick (rulebook §5 Notes:
    # "bullish cross or histogram up-tick"). The up-tick (hist rising vs prior
    # bar) is the momentum-resuming event on the resumption day after a pullback.
    macd_t, macd_p = float(mac["macd"].iloc[i]), float(mac["macd"].iloc[i - 1])
    sig_t, sig_p = float(mac["signal"].iloc[i]), float(mac["signal"].iloc[i - 1])
    hist_t, hist_p = float(mac["hist"].iloc[i]), float(mac["hist"].iloc[i - 1])
    cross_up = (macd_p <= sig_p) and (macd_t > sig_t)
    hist_uptick = hist_t > hist_p
    e3 = bool(cross_up or hist_uptick)

    # E4: volume confirmation.
    vol_t = float(volume.iloc[i])
    vol_avg_t = float(vavg.iloc[i])
    volume_ratio = vol_t / vol_avg_t if vol_avg_t > 0 else 0.0
    e4 = bool(vol_t >= e.volume_confirm_mult * vol_avg_t)

    anti_chase = bool(extension_pct > e.anti_chase_max_ext_pct)
    count = int(e1) + int(e2) + int(e3) + int(e4)
    triggered = bool(count >= e.confluence_required and not anti_chase)

    return EntrySignal(
        ticker=ticker,
        triggered=triggered,
        confluence_count=count,
        e1_pullback=e1,
        e2_rsi_turn=e2,
        e3_macd_resume=e3,
        e4_volume=e4,
        anti_chase=anti_chase,
        close=c,
        sma_short=ss,
        extension_pct=extension_pct,
        rsi=rsi_today,
        rsi_prev=rsi_prev,
        macd=macd_t,
        macd_signal=sig_t,
        macd_hist=hist_t,
        macd_above_zero=bool(macd_t > 0.0),
        volume=vol_t,
        volume_avg=vol_avg_t,
        volume_ratio=volume_ratio,
    )


def _blank_signal(ticker: str | None, close: float) -> EntrySignal:
    nan = float("nan")
    return EntrySignal(
        ticker=ticker, triggered=False, confluence_count=0,
        e1_pullback=False, e2_rsi_turn=False, e3_macd_resume=False, e4_volume=False,
        anti_chase=False, close=close, sma_short=nan, extension_pct=nan,
        rsi=nan, rsi_prev=nan, macd=nan, macd_signal=nan, macd_hist=nan,
        macd_above_zero=False, volume=nan, volume_avg=nan, volume_ratio=nan,
    )


def relative_strength(
    df: pd.DataFrame, index_df: pd.DataFrame, cfg: Config, *, asof: int = -1
) -> float:
    """Relative strength vs the market over `rs_lookback_days`: name return minus
    index return (higher = stronger). Frames are assumed date-aligned (same last
    bar); evaluated positionally. NaN if either lacks enough history."""
    lb = cfg.entry.rs_lookback_days
    nc = df["close"].reset_index(drop=True)
    ic = index_df["close"].reset_index(drop=True)
    i = _pos(asof, len(nc))
    j = _pos(asof, len(ic))
    if i - lb < 0 or j - lb < 0:
        return float("nan")
    name_ret = nc.iloc[i] / nc.iloc[i - lb] - 1.0
    idx_ret = ic.iloc[j] / ic.iloc[j - lb] - 1.0
    return float(name_ret - idx_ret)


# --------------------------------------------------------------------------- #
# Ranking — weight-free rank-sum composite of (RS, reward/risk)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RankedCandidate:
    key: str
    rs: float
    reward_risk: float
    rs_rank: int
    rr_rank: int
    composite: int  # rs_rank + rr_rank, lower = better
    rank: int       # final 1..n


def rank_candidates(items: list[tuple[str, float, float]]) -> list[RankedCandidate]:
    """Rank candidates by a weight-free Borda rank-sum of two 'higher is better'
    factors: relative strength and reward/risk (e.g. 1/stop_distance_pct).

    items: list of (key, rs, reward_risk). Returns ranked best-first.
    No weights are invented (ranking weights are not in config); each factor
    contributes its ordinal rank equally, ties broken by RS then key.
    """
    if not items:
        return []
    rs_order = sorted(items, key=lambda t: (-t[1], t[0]))
    rr_order = sorted(items, key=lambda t: (-t[2], t[0]))
    rs_rank = {t[0]: r for r, t in enumerate(rs_order, start=1)}
    rr_rank = {t[0]: r for r, t in enumerate(rr_order, start=1)}

    scored = [
        (key, rs, rr, rs_rank[key], rr_rank[key], rs_rank[key] + rr_rank[key])
        for (key, rs, rr) in items
    ]
    scored.sort(key=lambda s: (s[5], -s[1], s[0]))  # composite, then RS, then key
    return [
        RankedCandidate(
            key=key, rs=rs, reward_risk=rr, rs_rank=rsr, rr_rank=rrr,
            composite=comp, rank=final,
        )
        for final, (key, rs, rr, rsr, rrr, comp) in enumerate(scored, start=1)
    ]
