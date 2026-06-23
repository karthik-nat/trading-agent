"""Engine integration tests (Rulebook §10) — full funnel on a multi-name universe."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.config_loader import load_config
from src.engine import (
    BUY,
    EXIT,
    HOLD,
    WATCH,
    AccountState,
    HeldPosition,
    run_engine,
)

CFG = load_config()


def _frame(close, vol=None) -> pd.DataFrame:
    close = np.asarray(close, float)
    n = len(close)
    if vol is None:
        vol = np.full(n, 1e6)
    return pd.DataFrame(
        {"date": pd.bdate_range("2022-01-03", periods=n),
         "open": close, "high": close + 0.5, "low": close - 0.5,
         "close": close, "volume": vol}
    )


def _buy_name() -> pd.DataFrame:
    """Long uptrend (passes §4 regime) ending in a pullback-resume (fires §5)."""
    close = np.concatenate([
        np.linspace(60, 100, 170), np.linspace(100, 130, 60),
        np.linspace(130, 124, 6)[1:], np.array([125.5, 127.0]),
    ])
    vol = np.full(len(close), 1e6)
    vol[-1] = 1.5e6
    return _frame(close, vol)


def _holder() -> pd.DataFrame:           # reached +1R, sits above breakeven -> HOLD
    return _frame(np.r_[np.full(20, 100.0), [103.0, 106.0, 107.0, 106.0, 106.0]])


def _exiter() -> pd.DataFrame:           # closes below the 50-SMA -> trend-break EXIT
    return _frame(np.r_[np.linspace(100, 140, 50), [135.0, 128.0, 120.0, 110.0]])


UP_INDEX = _frame(np.linspace(3000, 5000, 260))
DOWN_INDEX = _frame(np.linspace(5000, 3000, 260))
DOWNTREND = _frame(np.linspace(200, 120, 260))

HELD = [
    HeldPosition("HOLDER", "Information Technology", 100.0, 95.0, 95.0, 50, 20),
    HeldPosition("EXITER", "Energy", 100.0, 95.0, 95.0, 50, 10),
]
HELD_PRICES = {"HOLDER": _holder(), "EXITER": _exiter()}


def test_full_funnel_buys_holds_exits():
    prices = {"GOODBUY": _buy_name(), "DOWN": DOWNTREND, **HELD_PRICES}
    sectors = {"GOODBUY": "Information Technology", "DOWN": "Energy"}
    acct = AccountState(equity=100_000.0, cash=80_000.0)
    res = run_engine(CFG, prices, UP_INDEX, acct, HELD, sectors)

    assert res.market_filter_on is True
    by = {r.ticker: r for r in res.recommendations}

    # downtrend name never reaches the recommendation list
    assert "DOWN" not in by

    # fresh BUY, sized within the 10% cap, all four entry reasons present
    buy = by["GOODBUY"]
    assert buy.action == BUY and buy.rank == 1
    assert buy.entry == 127.0
    assert 0 < buy.position_pct <= 10.0 + 1e-9
    assert buy.shares > 0
    assert {"E1_pullback", "E2_rsi_turn", "E3_macd_resume", "E4_volume"} <= set(buy.reason_codes)

    # held positions managed
    assert by["HOLDER"].action == HOLD
    assert by["EXITER"].action == EXIT
    assert "close_below_50sma" in by["EXITER"].reason_codes


def test_market_filter_off_blocks_new_buys_but_still_manages():
    prices = {"GOODBUY": _buy_name(), **HELD_PRICES}
    sectors = {"GOODBUY": "Information Technology"}
    acct = AccountState(equity=100_000.0, cash=80_000.0)
    res = run_engine(CFG, prices, DOWN_INDEX, acct, HELD, sectors)

    assert res.market_filter_on is False
    assert res.by_action(BUY) == [] and res.by_action(WATCH) == []
    # existing positions are still managed
    assert {r.ticker for r in res.recommendations} == {"HOLDER", "EXITER"}
    assert res.by_action(EXIT)[0].ticker == "EXITER"


def test_qualified_candidate_capped_becomes_watch():
    prices = {"GOODBUY": _buy_name()}
    sectors = {"GOODBUY": "Information Technology"}
    acct = AccountState(equity=100_000.0, cash=80_000.0, new_positions_today=2)  # daily cap hit
    res = run_engine(CFG, prices, UP_INDEX, acct, [], sectors)

    watch = res.by_action(WATCH)
    assert len(watch) == 1 and watch[0].ticker == "GOODBUY"
    assert watch[0].binding_cap == "max_new_positions_per_day"
    assert res.by_action(BUY) == []


def test_two_candidates_are_ranked_and_filled():
    prices = {"BUY_A": _buy_name(), "BUY_B": _buy_name()}
    sectors = {"BUY_A": "Information Technology", "BUY_B": "Financials"}
    acct = AccountState(equity=1_000_000.0, cash=900_000.0)  # room for both
    res = run_engine(CFG, prices, UP_INDEX, acct, [], sectors)

    buys = res.by_action(BUY)
    assert len(buys) == 2
    assert {b.rank for b in buys} == {1, 2}
