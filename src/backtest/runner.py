"""Event-driven portfolio backtester (Plan Phase 2).

Replays the EXACT `engine.py` funnel day by day over history — so the backtest
validates the same code the live system runs, with the full §6/§9 portfolio caps,
§5 ranking, §7 risk sizing and §8 exits intact (a single-asset framework cannot
express those). It is deliberately a custom loop rather than backtesting.py /
vectorbt for that fidelity.

Modeling choices (documented assumptions):
  * No lookahead: signals are computed from bars up to and including day d's close;
    the resulting orders fill at day d+1's OPEN. Equity is marked at each close.
  * Costs: slippage `model_slippage_bps` and `model_commission_per_trade` (both
    from rulebook `protocol`). Buys fill at open*(1+slip), sells at open*(1-slip),
    plus commission per fill.
  * Stops/exits are evaluated on the close (per §10's close-based monitor) and
    filled next open — intraday stop fills are NOT modeled (a later refinement).
  * Per-trade R = realized P&L / initial risk dollars (handles +2R scaling).

Produces a trade log + equity curve for metrics/performance.py. No real orders.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config_loader import Config
from ..engine import (
    BUY,
    EXIT,
    HOLD,
    TRIM,
    AccountState,
    HeldPosition,
    run_engine,
)


@dataclass
class _OpenPosition:
    ticker: str
    sector: str
    entry_date: pd.Timestamp
    entry_index: int                 # positional index in the ticker's full history
    entry_price: float               # actual fill price
    initial_stop: float
    shares: float                    # remaining shares
    original_shares: float
    initial_risk: float              # original_shares * (entry - initial_stop)
    realized_pnl: float = 0.0        # P&L banked from partial scales
    fees: float = 0.0
    scaled: bool = False
    max_high: float = 0.0            # high-water for MFE


@dataclass(frozen=True)
class TradeRecord:
    ticker: str
    sector: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float                # share-weighted average exit
    shares: int
    initial_stop: float
    r_multiple: float
    pnl: float
    fees: float
    holding_days: int
    exit_reason: str
    scaled: bool
    mfe_r: float                     # max favourable excursion in R


@dataclass(frozen=True)
class BacktestResult:
    equity_curve: pd.Series          # indexed by date
    daily_returns: pd.Series
    trades: list[TradeRecord]
    start: pd.Timestamp
    end: pd.Timestamp
    initial_equity: float
    final_equity: float


@dataclass
class _Pending:
    ticker: str
    action: str                      # BUY | EXIT | TRIM
    shares: float
    stop: float
    sector: str
    reason: str


class _Book:
    """Per-ticker fast access by date with a date->position map (no lookahead)."""

    def __init__(self, prices: dict[str, pd.DataFrame]):
        self.frames = prices
        self.dates: dict[str, np.ndarray] = {}
        self.pos: dict[str, dict[pd.Timestamp, int]] = {}
        for t, df in prices.items():
            d = pd.to_datetime(df["date"]).to_numpy()
            self.dates[t] = d
            self.pos[t] = {pd.Timestamp(x): i for i, x in enumerate(d)}

    def index_at(self, ticker: str, day: pd.Timestamp) -> int | None:
        return self.pos[ticker].get(day)

    def slice_to(self, ticker: str, day: pd.Timestamp) -> pd.DataFrame | None:
        """Frame for `ticker` with bars up to and including `day` (last bar = day)."""
        arr = self.dates[ticker]
        p = int(np.searchsorted(arr, np.datetime64(day), side="right"))
        if p <= 0:
            return None
        return self.frames[ticker].iloc[:p]

    def price(self, ticker: str, day: pd.Timestamp, col: str) -> float | None:
        i = self.index_at(ticker, day)
        if i is None:
            return None
        return float(self.frames[ticker][col].iloc[i])


def run_backtest(
    cfg: Config,
    prices: dict[str, pd.DataFrame],
    index_df: pd.DataFrame,
    *,
    initial_equity: float,
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp | None = None,
    warmup_days: int | None = None,
    sectors: dict[str, str] | None = None,
    log_every: int | None = None,
) -> BacktestResult:
    """Run the event-driven backtest over the index's trading calendar."""
    sectors = sectors or {}
    book = _Book(prices)
    slip = cfg.protocol.model_slippage_bps / 10_000.0
    commission = cfg.protocol.model_commission_per_trade
    warmup = warmup_days if warmup_days is not None else cfg.universe.min_history_days

    cal = pd.to_datetime(index_df["date"]).reset_index(drop=True)
    if start is not None:
        cal = cal[cal >= pd.Timestamp(start)]
    if end is not None:
        cal = cal[cal <= pd.Timestamp(end)]
    cal = cal.sort_values().reset_index(drop=True)

    cash = float(initial_equity)
    open_positions: dict[str, _OpenPosition] = {}
    pending: list[_Pending] = []
    trades: list[TradeRecord] = []
    equity_dates: list[pd.Timestamp] = []
    equity_values: list[float] = []

    for day in cal:
        # ---- 1) fill yesterday's orders at today's OPEN ---------------------- #
        still_pending: list[_Pending] = []
        for od in pending:
            open_px = book.price(od.ticker, day, "open")
            if open_px is None:
                still_pending.append(od)        # no bar today; try next session
                continue
            cash = _execute(od, day, open_px, slip, commission, open_positions,
                            book, cash, trades)
        pending = still_pending

        # ---- 2) mark-to-market at today's CLOSE ------------------------------ #
        invested = 0.0
        for t, p in open_positions.items():
            c = book.price(t, day, "close")
            invested += p.shares * (c if c is not None else p.entry_price)
            if c is not None:
                hi = book.price(t, day, "high")
                p.max_high = max(p.max_high, hi if hi is not None else c)
        equity = cash + invested
        equity_dates.append(day)
        equity_values.append(equity)
        if log_every and len(equity_values) % log_every == 0:
            print(f"[backtest] {len(equity_values)}/{len(cal)} days  {day.date()}  "
                  f"equity={equity:,.0f}  open={len(open_positions)}  trades={len(trades)}",
                  flush=True)

        # ---- 3) generate tomorrow's orders from data through today's close --- #
        # Warmup gates on available HISTORY (slice length), not days-since-start,
        # so an out-of-sample run still uses full pre-start history for indicators.
        sliced: dict[str, pd.DataFrame] = {}
        for t in prices:
            s = book.slice_to(t, day)
            if s is not None and len(s) >= 2:
                sliced[t] = s
        idx_slice = _slice_index(index_df, day)
        if idx_slice is None or len(idx_slice) <= warmup:
            continue

        held = [
            HeldPosition(
                ticker=p.ticker, sector=p.sector, entry_price=p.entry_price,
                initial_stop=p.initial_stop, current_stop=p.initial_stop,
                shares=p.shares, entry_index=p.entry_index, scaled=p.scaled,
            )
            for p in open_positions.values() if p.ticker in sliced
        ]
        acct = AccountState(equity=equity, cash=cash, new_positions_today=0)
        result = run_engine(cfg, sliced, idx_slice, acct, held, sectors)

        pending = _orders_from_recs(result, open_positions, cfg, sectors)

    eq = pd.Series(equity_values, index=pd.DatetimeIndex(equity_dates), name="equity")
    return BacktestResult(
        equity_curve=eq,
        daily_returns=eq.pct_change().dropna(),
        trades=trades,
        start=cal.iloc[0] if len(cal) else pd.NaT,
        end=cal.iloc[-1] if len(cal) else pd.NaT,
        initial_equity=float(initial_equity),
        final_equity=float(eq.iloc[-1]) if len(eq) else float(initial_equity),
    )


def _slice_index(index_df: pd.DataFrame, day: pd.Timestamp) -> pd.DataFrame | None:
    d = pd.to_datetime(index_df["date"]).to_numpy()
    p = int(np.searchsorted(d, np.datetime64(day), side="right"))
    return index_df.iloc[:p] if p > 0 else None


def _orders_from_recs(
    result, open_positions: dict[str, _OpenPosition], cfg: Config, sectors: dict[str, str]
) -> list[_Pending]:
    orders: list[_Pending] = []
    for r in result.recommendations:
        if r.action == EXIT and r.ticker in open_positions:
            p = open_positions[r.ticker]
            orders.append(_Pending(r.ticker, EXIT, p.shares, r.stop or 0.0,
                                   p.sector, ",".join(r.reason_codes)))
        elif r.action == TRIM and r.ticker in open_positions:
            p = open_positions[r.ticker]
            qty = float(min(np.floor(p.original_shares * cfg.exits.first_scale_fraction),
                            p.shares))
            if qty > 0:
                orders.append(_Pending(r.ticker, TRIM, qty, r.stop or 0.0,
                                       p.sector, "scale_first_tranche"))
        elif r.action == BUY and r.ticker not in open_positions and r.shares:
            orders.append(_Pending(r.ticker, BUY, float(r.shares), r.stop or 0.0,
                                   sectors.get(r.ticker, "Unknown"),
                                   ",".join(r.reason_codes)))
    return orders


def _execute(od: _Pending, day, open_px, slip, commission,
             open_positions, book, cash, trades) -> float:
    """Fill one pending order at `open_px`; mutate positions/cash, log closed trades."""
    if od.action == BUY:
        fill = open_px * (1.0 + slip)
        cost = od.shares * fill + commission
        # Skip if unaffordable, or if the open already gapped through the stop
        # (you would not enter a long that opens at/below its protective stop).
        if od.shares <= 0 or cost > cash or fill <= od.stop:
            return cash
        entry_i = book.index_at(od.ticker, day)
        open_positions[od.ticker] = _OpenPosition(
            ticker=od.ticker, sector=od.sector, entry_date=day, entry_index=entry_i,
            entry_price=fill, initial_stop=od.stop, shares=od.shares,
            original_shares=od.shares,
            initial_risk=od.shares * max(fill - od.stop, 1e-9),
            fees=commission, max_high=fill,
        )
        return cash - cost

    if od.ticker not in open_positions:
        return cash
    p = open_positions[od.ticker]

    if od.action == TRIM:
        fill = open_px * (1.0 - slip)
        qty = min(od.shares, p.shares)
        proceeds = qty * fill - commission
        p.realized_pnl += qty * (fill - p.entry_price)
        p.shares -= qty
        p.fees += commission
        p.scaled = True
        return cash + proceeds

    # EXIT — close the remainder and log the round trip
    fill = open_px * (1.0 - slip)
    qty = p.shares
    proceeds = qty * fill - commission
    p.realized_pnl += qty * (fill - p.entry_price)
    p.fees += commission
    total_pnl = p.realized_pnl
    r_mult = total_pnl / p.initial_risk if p.initial_risk > 0 else 0.0
    mfe_r = ((p.max_high - p.entry_price) * p.original_shares) / p.initial_risk \
        if p.initial_risk > 0 else 0.0
    trades.append(TradeRecord(
        ticker=p.ticker, sector=p.sector, entry_date=p.entry_date, exit_date=day,
        entry_price=p.entry_price, exit_price=fill, shares=int(p.original_shares),
        initial_stop=p.initial_stop, r_multiple=float(r_mult), pnl=float(total_pnl),
        fees=float(p.fees), holding_days=int((day - p.entry_date).days),
        exit_reason=od.reason, scaled=p.scaled, mfe_r=float(mfe_r),
    ))
    del open_positions[od.ticker]
    return cash + proceeds
