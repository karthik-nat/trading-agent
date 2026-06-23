"""Portfolio construction & diversification caps (Rulebook §6, plus §9 heat).

Because there is no index fund underneath the book, these caps ARE the
diversification system. Given the current holdings and a proposed new buy, decide
whether the buy is allowed and, if not, which cap(s) bind:

  * max_positions          : holdings already at target_positions_max.
  * max_new_positions_day  : already opened max_new_positions_per_day today.
  * max_position_pct       : single-name weight > cap (re-check of §7's cap).
  * max_sector_pct         : sector weight (incl. candidate) > cap.
  * cash_floor             : buying would drop cash below cash_floor_pct.
  * total_heat_cap (§9)    : sum of open risk-to-stops (incl. candidate) > cap.
  * correlation            : candidate highly correlated (> threshold, trailing
                             lookback) with >= max_correlated_names holdings.

A single correlated name is a WARNING, not a block. All numbers come from config.
The §9 drawdown / consecutive-loss circuit breakers and the pre-trade re-check
guard are deliberately NOT here — they live in risk/guards.py (Phase 5).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ..config_loader import Config

# binding-cap codes
MAX_POSITIONS = "max_positions"
MAX_NEW_PER_DAY = "max_new_positions_per_day"
MAX_POSITION_PCT = "max_position_pct"
MAX_SECTOR_PCT = "max_sector_pct"
CASH_FLOOR = "cash_floor"
TOTAL_HEAT = "total_heat_cap"
CORRELATION = "correlation"

_EPS = 1e-9


@dataclass(frozen=True)
class Holding:
    ticker: str
    sector: str
    market_value: float
    open_risk: float = 0.0                 # shares * (entry - current_stop)
    returns: pd.Series | None = None       # daily returns for the correlation guard


@dataclass(frozen=True)
class Candidate:
    ticker: str
    sector: str
    position_value: float
    risk: float = 0.0                      # actual risk dollars to the initial stop
    returns: pd.Series | None = None


@dataclass(frozen=True)
class PortfolioState:
    account_equity: float
    cash: float
    new_positions_today: int = 0


@dataclass(frozen=True)
class PortfolioDecision:
    allowed: bool
    binding_caps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    holdings_count: int = 0
    sector_weight_after_pct: float = 0.0
    cash_after: float = 0.0
    heat_after_pct: float = 0.0
    correlated_names: list[str] = field(default_factory=list)


def _pairwise_corr(a: pd.Series | None, b: pd.Series | None, lookback: int) -> float:
    if a is None or b is None:
        return float("nan")
    joined = pd.concat([a.rename("a"), b.rename("b")], axis=1, join="inner").dropna()
    if len(joined) < 2:
        return float("nan")
    tail = joined.tail(lookback)
    if len(tail) < 2:
        return float("nan")
    return float(tail["a"].corr(tail["b"]))


def evaluate_candidate(
    candidate: Candidate,
    holdings: list[Holding],
    state: PortfolioState,
    cfg: Config,
) -> PortfolioDecision:
    """Decide whether `candidate` may be bought given current holdings/state."""
    p = cfg.portfolio
    eq = state.account_equity
    binding: list[str] = []
    warnings: list[str] = []

    # 1) position-count cap
    if len(holdings) >= p.target_positions_max:
        binding.append(MAX_POSITIONS)

    # 2) new-positions-per-day cap
    if state.new_positions_today >= p.max_new_positions_per_day:
        binding.append(MAX_NEW_PER_DAY)

    # 3) single-name weight cap (re-check of §7)
    if candidate.position_value > p.max_position_pct / 100.0 * eq + _EPS:
        binding.append(MAX_POSITION_PCT)

    # 4) sector cap
    sector_value = sum(
        h.market_value for h in holdings if h.sector == candidate.sector
    )
    sector_weight_after = (sector_value + candidate.position_value) / eq * 100.0
    if sector_weight_after > p.max_sector_pct + _EPS:
        binding.append(MAX_SECTOR_PCT)

    # 5) cash floor
    cash_after = state.cash - candidate.position_value
    if cash_after < p.cash_floor_pct / 100.0 * eq - _EPS:
        binding.append(CASH_FLOOR)

    # 6) total heat (§9) — open risk to stops including the candidate
    heat_after = sum(h.open_risk for h in holdings) + candidate.risk
    heat_after_pct = heat_after / eq * 100.0
    if heat_after_pct > cfg.risk.total_heat_cap_pct + _EPS:
        binding.append(TOTAL_HEAT)

    # 7) correlation guard
    correlated = [
        h.ticker
        for h in holdings
        if (c := _pairwise_corr(candidate.returns, h.returns, p.correlation_lookback_days))
        == c  # not NaN
        and c > p.correlation_threshold
    ]
    if len(correlated) >= p.max_correlated_names:
        binding.append(CORRELATION)
    elif correlated:
        warnings.append(f"correlated_with:{','.join(correlated)}")

    return PortfolioDecision(
        allowed=(len(binding) == 0),
        binding_caps=binding,
        warnings=warnings,
        holdings_count=len(holdings),
        sector_weight_after_pct=float(sector_weight_after),
        cash_after=float(cash_after),
        heat_after_pct=float(heat_after_pct),
        correlated_names=correlated,
    )
