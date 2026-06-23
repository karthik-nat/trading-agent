"""Backtester tests (Plan Phase 2) — cost/fill math + end-to-end determinism."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.backtest.runner import (
    BUY,
    EXIT,
    TRIM,
    _Book,
    _execute,
    _OpenPosition,
    _Pending,
    run_backtest,
)
from src.config_loader import load_config

CFG = load_config()
DAY0 = pd.Timestamp("2022-01-03")
DAY1 = pd.Timestamp("2022-01-04")


def _book_one() -> _Book:
    df = pd.DataFrame(
        {"date": [DAY0, DAY1], "open": [99.0, 100.0], "high": [101.0, 101.0],
         "low": [98.0, 99.0], "close": [100.0, 100.5], "volume": [1e6, 1e6]}
    )
    return _Book({"T": df})


# --------------------------------------------------------------------------- #
# Fill / cost model (next-open + slippage + commission)
# --------------------------------------------------------------------------- #
def test_buy_fill_applies_slippage_and_commission():
    pos, trades = {}, []
    od = _Pending("T", BUY, 10.0, 95.0, "Tech", "E1")
    cash = _execute(od, DAY1, 100.0, 0.0005, 1.0, pos, _book_one(), 10_000.0, trades)
    p = pos["T"]
    assert p.entry_price == pytest.approx(100.05)         # 100 * (1 + 5bps)
    assert p.entry_index == 1                             # filled on DAY1 (index 1)
    assert cash == pytest.approx(10_000 - (10 * 100.05 + 1))  # cost incl. commission


def test_buy_skipped_when_cash_insufficient():
    pos, trades = {}, []
    od = _Pending("T", BUY, 10.0, 95.0, "Tech", "E1")
    cash = _execute(od, DAY1, 100.0, 0.0005, 1.0, pos, _book_one(), 500.0, trades)
    assert pos == {} and cash == 500.0


def test_exit_logs_trade_with_correct_r():
    pos = {"T": _OpenPosition("T", "S", DAY0, 0, 100.0, 95.0, 10.0, 10.0,
                              initial_risk=50.0, max_high=110.0)}
    trades = []
    od = _Pending("T", EXIT, 10.0, 100.0, "S", "stop_hit")
    cash = _execute(od, DAY1, 110.0, 0.0005, 1.0, pos, _book_one(), 5_000.0, trades)
    t = trades[0]
    assert t.exit_price == pytest.approx(109.945)         # 110 * (1 - 5bps)
    assert t.pnl == pytest.approx(10 * (109.945 - 100.0))
    assert t.r_multiple == pytest.approx(99.45 / 50.0, abs=1e-3)
    assert "T" not in pos
    assert cash == pytest.approx(5_000 + (10 * 109.945 - 1))


def test_trim_sells_half_and_marks_scaled():
    pos = {"T": _OpenPosition("T", "S", DAY0, 0, 100.0, 95.0, 10.0, 10.0,
                              initial_risk=50.0, max_high=110.0)}
    cash = _execute(_Pending("T", TRIM, 5.0, 100.0, "S", "scale"),
                    DAY1, 110.0, 0.0005, 1.0, pos, _book_one(), 5_000.0, [])
    p = pos["T"]
    assert p.shares == 5.0 and p.scaled is True
    assert p.realized_pnl == pytest.approx(5 * (109.945 - 100.0))
    assert cash == pytest.approx(5_000 + (5 * 109.945 - 1))


# --------------------------------------------------------------------------- #
# End-to-end integration
# --------------------------------------------------------------------------- #
def _scenario():
    close = np.concatenate([
        np.linspace(60, 100, 220), np.linspace(100, 130, 60),
        np.linspace(130, 124, 6)[1:], np.array([125.5, 127.0]),
        np.linspace(127, 150, 30), np.linspace(150, 110, 15),
    ])
    n = len(close)
    dates = pd.bdate_range("2020-01-02", periods=n)
    vol = np.full(n, 1e6)
    vol[286] = 1.5e6
    df = pd.DataFrame({"date": dates, "open": close, "high": close + 0.5,
                       "low": close - 0.5, "close": close, "volume": vol})
    index = pd.DataFrame({"date": dates, "open": np.linspace(3000, 5200, n),
                          "high": np.linspace(3000, 5200, n) + 1,
                          "low": np.linspace(3000, 5200, n) - 1,
                          "close": np.linspace(3000, 5200, n),
                          "volume": np.full(n, 1e6)})
    return df, index


def test_end_to_end_single_trade():
    good, index = _scenario()
    res = run_backtest(CFG, {"GOODBUY": good}, index, initial_equity=100_000.0,
                       sectors={"GOODBUY": "Information Technology"})
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.r_multiple > 0 and t.scaled is True          # scaled at +2R then ran
    assert res.final_equity > 100_000.0                   # net winner
    assert t.entry_price > 0 and t.exit_price > 0


def test_date_range_is_respected():
    good, index = _scenario()
    start, end = pd.Timestamp("2020-06-01"), pd.Timestamp("2021-01-29")
    res = run_backtest(CFG, {"GOODBUY": good}, index, initial_equity=100_000.0,
                       start=start, end=end,
                       sectors={"GOODBUY": "Information Technology"})
    assert res.equity_curve.index.min() >= start
    assert res.equity_curve.index.max() <= end
