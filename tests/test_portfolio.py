"""Portfolio-cap tests (Rulebook §6 + §9 heat) — each cap blocks; clear case allows."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.config_loader import load_config
from src.strategy.portfolio import (
    CASH_FLOOR,
    CORRELATION,
    MAX_NEW_PER_DAY,
    MAX_POSITION_PCT,
    MAX_POSITIONS,
    MAX_SECTOR_PCT,
    TOTAL_HEAT,
    Candidate,
    Holding,
    PortfolioState,
    evaluate_candidate,
)

CFG = load_config()
EQ = 100_000.0


def _series(seed: int, n: int = 70) -> pd.Series:
    return pd.Series(np.random.default_rng(seed).normal(0, 0.01, n))


def _state(cash=80_000.0, new_today=0, equity=EQ) -> PortfolioState:
    return PortfolioState(account_equity=equity, cash=cash, new_positions_today=new_today)


def _cand(sector="Information Technology", value=5_000.0, risk=500.0, returns=None):
    return Candidate("CAND", sector, position_value=value, risk=risk, returns=returns)


def test_all_clear_allows():
    holdings = [
        Holding("A", "Information Technology", 5_000.0, 500.0),
        Holding("B", "Financials", 5_000.0, 500.0),
    ]
    d = evaluate_candidate(_cand(), holdings, _state(), CFG)
    assert d.allowed is True
    assert d.binding_caps == []


def test_max_positions_blocks():
    holdings = [Holding(f"H{i}", "Financials", 1_000.0) for i in range(12)]
    d = evaluate_candidate(_cand(value=1_000.0), holdings, _state(), CFG)
    assert MAX_POSITIONS in d.binding_caps and not d.allowed


def test_new_positions_per_day_blocks():
    d = evaluate_candidate(_cand(), [], _state(new_today=2), CFG)
    assert MAX_NEW_PER_DAY in d.binding_caps


def test_single_name_cap_blocks():
    d = evaluate_candidate(_cand(value=10_001.0), [], _state(), CFG)  # > 10% of 100k
    assert MAX_POSITION_PCT in d.binding_caps


def test_sector_cap_blocks():
    holdings = [Holding("T1", "Information Technology", 25_000.0)]
    d = evaluate_candidate(_cand(value=10_000.0), holdings, _state(), CFG)  # 35% tech
    assert MAX_SECTOR_PCT in d.binding_caps
    assert d.sector_weight_after_pct == 35.0


def test_sector_cap_exactly_at_limit_allows():
    holdings = [Holding("T1", "Information Technology", 20_000.0)]
    d = evaluate_candidate(_cand(value=10_000.0), holdings, _state(), CFG)  # exactly 30%
    assert MAX_SECTOR_PCT not in d.binding_caps


def test_cash_floor_blocks():
    # equity 100k -> floor 5k; cash 6k, buy 2k -> 4k after
    d = evaluate_candidate(_cand(value=2_000.0), [], _state(cash=6_000.0), CFG)
    assert CASH_FLOOR in d.binding_caps


def test_total_heat_blocks():
    holdings = [Holding("H", "Energy", 10_000.0, open_risk=5_000.0)]  # 5% heat
    d = evaluate_candidate(_cand(risk=2_000.0), holdings, _state(), CFG)  # +2% -> 7% > 6%
    assert TOTAL_HEAT in d.binding_caps
    assert d.heat_after_pct == pytest.approx(7.0)


def test_correlation_two_names_blocks():
    r = _series(1)
    holdings = [
        Holding("X", "Information Technology", 5_000.0, returns=r.copy()),
        Holding("Y", "Information Technology", 5_000.0, returns=r.copy()),
    ]
    # keep sector within cap: 5k+5k+5k = 15% < 30%
    d = evaluate_candidate(_cand(returns=r.copy()), holdings, _state(), CFG)
    assert CORRELATION in d.binding_caps
    assert set(d.correlated_names) == {"X", "Y"}


def test_correlation_one_name_warns_but_allows():
    r = _series(2)
    holdings = [
        Holding("X", "Financials", 5_000.0, returns=r.copy()),
        Holding("Z", "Energy", 5_000.0, returns=_series(99)),  # uncorrelated
    ]
    d = evaluate_candidate(_cand(sector="Health Care", returns=r.copy()), holdings, _state(), CFG)
    assert d.allowed is True
    assert d.correlated_names == ["X"]
    assert any("correlated_with:X" in w for w in d.warnings)


def test_multiple_caps_reported_together():
    holdings = [Holding(f"H{i}", "Information Technology", 3_000.0) for i in range(12)]
    d = evaluate_candidate(
        _cand(value=11_000.0), holdings, _state(new_today=2), CFG
    )
    assert {MAX_POSITIONS, MAX_NEW_PER_DAY, MAX_POSITION_PCT} <= set(d.binding_caps)
    assert not d.allowed
