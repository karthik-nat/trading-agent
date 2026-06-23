"""Performance-metric tests (Rulebook §11) — exact R-math on a known trade log."""
from __future__ import annotations

import pandas as pd
import pytest

from src.backtest.runner import BacktestResult, TradeRecord
from src.config_loader import load_config
from src.metrics.performance import (
    PerformanceMetrics,
    compute_metrics,
    gate_check,
    summary,
)

CFG = load_config()  # go_live_min_trades=50, go_live_min_expectancy_r=0.0


def _trade(r: float, pnl: float = 0.0, reason: str = "x", scaled: bool = False) -> TradeRecord:
    d = pd.Timestamp("2022-01-03")
    return TradeRecord(
        ticker="T", sector="S", entry_date=d, exit_date=d, entry_price=100.0,
        exit_price=100.0, shares=10, initial_stop=95.0, r_multiple=r, pnl=pnl,
        fees=0.0, holding_days=10, exit_reason=reason, scaled=scaled, mfe_r=r,
    )


def _result(trades, initial=100_000.0, final=101_000.0) -> BacktestResult:
    eq = pd.Series([initial, final], index=pd.to_datetime(["2022-01-03", "2022-12-30"]))
    return BacktestResult(
        equity_curve=eq, daily_returns=eq.pct_change().dropna(), trades=trades,
        start=eq.index[0], end=eq.index[-1], initial_equity=initial, final_equity=final,
    )


def test_expectancy_winrate_payoff_pctstopped():
    trades = [_trade(2.0, 200), _trade(2.0, 200), _trade(-1.0, -100),
              _trade(-1.0, -100), _trade(1.0, 100)]
    m = compute_metrics(_result(trades), CFG)
    assert m.total_trades == 5
    assert m.expectancy_r == pytest.approx(0.6)          # (2+2-1-1+1)/5
    assert m.win_rate == pytest.approx(0.6)              # 3 of 5
    assert m.avg_win_r == pytest.approx((2 + 2 + 1) / 3)
    assert m.avg_loss_r == pytest.approx(-1.0)
    assert m.payoff_ratio == pytest.approx((2 + 2 + 1) / 3)
    assert m.pct_stopped_at_initial == pytest.approx(0.4)  # two -1R trades
    assert m.expectancy_dollars == pytest.approx(60.0)


def test_no_trades_is_safe():
    m = compute_metrics(_result([]), CFG)
    assert m.total_trades == 0 and m.expectancy_r == 0.0


def test_max_drawdown():
    eq = pd.Series([100.0, 110.0, 90.0, 100.0],
                   index=pd.bdate_range("2022-01-03", periods=4))
    res = BacktestResult(eq, eq.pct_change().dropna(), [], eq.index[0], eq.index[-1],
                         100.0, 100.0)
    m = compute_metrics(res, CFG)
    assert m.max_drawdown_pct == pytest.approx(-18.1818, abs=1e-3)  # 90/110 - 1


def _metrics(trades: int, expectancy: float) -> PerformanceMetrics:
    return PerformanceMetrics(trades, 0.5, expectancy, 1.0, -1.0, 1.0, 0.3, 20.0,
                              5.0, -8.0, 1.2, 1.5, 50.0)


def test_gate_passes_with_enough_trades_and_positive_expectancy():
    g = gate_check(_metrics(60, 0.25), CFG)
    assert g.passed is True


def test_gate_fails_on_too_few_trades():
    assert gate_check(_metrics(40, 0.5), CFG).passed is False


def test_gate_fails_on_nonpositive_expectancy():
    assert gate_check(_metrics(80, -0.1), CFG).passed is False
    assert gate_check(_metrics(80, 0.0), CFG).passed is False     # must be > 0


def test_summary_renders():
    s = summary(_metrics(60, 0.25), title="Test")
    assert "expectancy (R)" in s and "Test" in s
