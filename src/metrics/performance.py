"""Performance metrics & the Gate-1 edge check (Rulebook §11).

R-based edge metrics (expectancy, win rate, payoff ratio, % stopped at the initial
stop) are computed directly from the trade log — small, exact, and unit-tested.
Risk-adjusted return metrics (Sharpe, Sortino, max drawdown) come from quantstats
on the equity curve / daily returns.

Gate 1 (GO/NO-GO #1, Plan Phase 2): positive expectancy after modeled costs across
>= `go_live_min_trades` trades. The in-sample vs out-of-sample comparison is done
by the caller (run_backtest over two date ranges); `gate_check` evaluates one run.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config_loader import Config

# a trade is "stopped at the initial stop" (full-risk loss) at or below this R
_FULL_STOP_R = -0.9


@dataclass(frozen=True)
class PerformanceMetrics:
    total_trades: int
    win_rate: float                 # fraction of trades with R > 0
    expectancy_r: float             # mean R per trade (the core edge number)
    avg_win_r: float
    avg_loss_r: float               # negative
    payoff_ratio: float             # avg_win_r / |avg_loss_r|
    pct_stopped_at_initial: float   # fraction of trades that took ~ -1R
    avg_holding_days: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe: float
    sortino: float
    expectancy_dollars: float       # mean P&L per trade


def compute_metrics(result, cfg: Config) -> PerformanceMetrics:
    """Compute §11 metrics from a BacktestResult."""
    trades = list(result.trades)
    n = len(trades)
    if n == 0:
        return _empty_metrics(result)

    r = np.array([t.r_multiple for t in trades], dtype=float)
    pnl = np.array([t.pnl for t in trades], dtype=float)
    hold = np.array([t.holding_days for t in trades], dtype=float)
    wins = r[r > 0]
    losses = r[r <= 0]

    avg_win = float(wins.mean()) if wins.size else 0.0
    avg_loss = float(losses.mean()) if losses.size else 0.0
    payoff = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf") if wins.size else 0.0

    return PerformanceMetrics(
        total_trades=n,
        win_rate=float((r > 0).mean()),
        expectancy_r=float(r.mean()),
        avg_win_r=avg_win,
        avg_loss_r=avg_loss,
        payoff_ratio=float(payoff),
        pct_stopped_at_initial=float((r <= _FULL_STOP_R).mean()),
        avg_holding_days=float(hold.mean()),
        total_return_pct=float((result.final_equity / result.initial_equity - 1.0) * 100.0),
        max_drawdown_pct=_max_drawdown_pct(result.equity_curve),
        sharpe=_qs_metric("sharpe", result.daily_returns),
        sortino=_qs_metric("sortino", result.daily_returns),
        expectancy_dollars=float(pnl.mean()),
    )


def _empty_metrics(result) -> PerformanceMetrics:
    return PerformanceMetrics(
        0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
        float((result.final_equity / result.initial_equity - 1.0) * 100.0),
        _max_drawdown_pct(result.equity_curve), 0.0, 0.0, 0.0,
    )


def _max_drawdown_pct(equity: pd.Series) -> float:
    if equity is None or len(equity) < 2:
        return 0.0
    peak = equity.cummax()
    dd = (equity / peak - 1.0).min()
    return float(dd * 100.0)


def _qs_metric(name: str, returns: pd.Series) -> float:
    if returns is None or len(returns) < 2 or returns.std() == 0:
        return 0.0
    try:
        import quantstats as qs
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fn = getattr(qs.stats, name)
            val = float(fn(returns))
        return val if np.isfinite(val) else 0.0
    except Exception:
        return 0.0


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reasons: list[str]


def gate_check(metrics: PerformanceMetrics, cfg: Config) -> GateResult:
    """Evaluate one backtest run against the Gate-1 bar (§11 / Plan Phase 2)."""
    reasons: list[str] = []
    enough = metrics.total_trades >= cfg.protocol.go_live_min_trades
    positive = metrics.expectancy_r > cfg.protocol.go_live_min_expectancy_r
    reasons.append(
        f"trades {metrics.total_trades} {'>=' if enough else '<'} "
        f"{cfg.protocol.go_live_min_trades} required"
    )
    reasons.append(
        f"expectancy {metrics.expectancy_r:+.3f}R {'>' if positive else '<='} "
        f"{cfg.protocol.go_live_min_expectancy_r:.3f}R bar"
    )
    return GateResult(passed=bool(enough and positive), reasons=reasons)


def summary(metrics: PerformanceMetrics, *, title: str = "Backtest") -> str:
    """Human-readable metrics block."""
    m = metrics
    return (
        f"=== {title} ===\n"
        f"  trades            : {m.total_trades}\n"
        f"  expectancy (R)    : {m.expectancy_r:+.3f}   <- core edge\n"
        f"  win rate          : {m.win_rate * 100:5.1f}%\n"
        f"  payoff (W/L)      : {m.payoff_ratio:.2f}   (avg win {m.avg_win_r:+.2f}R / "
        f"avg loss {m.avg_loss_r:+.2f}R)\n"
        f"  % full-stop losses: {m.pct_stopped_at_initial * 100:5.1f}%\n"
        f"  avg holding days  : {m.avg_holding_days:.1f}\n"
        f"  total return      : {m.total_return_pct:+.2f}%\n"
        f"  max drawdown      : {m.max_drawdown_pct:.2f}%\n"
        f"  Sharpe / Sortino  : {m.sharpe:.2f} / {m.sortino:.2f}\n"
        f"  avg $ per trade   : {m.expectancy_dollars:+.2f}"
    )
