"""Position sizing — computed from risk, never guessed (Rulebook §7).

    risk_dollars   = account_equity * risk_per_trade_pct
    stop_distance  = entry_price - initial_stop_price        (1R per share)
    shares         = floor(risk_dollars / stop_distance)
    position_value = shares * entry_price

The §6 per-name cap (`max_position_pct`) OVERRIDES risk sizing when it binds:
if risk sizing implies a position larger than the cap, the cap wins (a smaller
position). Dust positions below `min_position_value` (or zero shares) are skipped.

Whole shares only (the rulebook formula floors). Fractional shares are a later,
config-gated option. All numbers come from config.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..config_loader import Config

# binding_cap values
RISK = "risk"
POSITION_CAP = "position_cap"


@dataclass(frozen=True)
class SizingResult:
    shares: int
    entry_price: float
    initial_stop: float
    stop_distance: float
    position_value: float
    intended_risk: float     # account_equity * risk_per_trade_pct
    actual_risk: float       # shares * stop_distance (after floor/cap)
    risk_pct: float          # actual_risk / equity * 100
    position_pct: float      # position_value / equity * 100
    binding_cap: str         # RISK or POSITION_CAP — which rule set the share count
    skipped: bool
    skip_reason: str | None  # "zero_shares" | "dust_min_value" | None


def size_position(
    account_equity: float,
    entry_price: float,
    initial_stop_price: float,
    cfg: Config,
) -> SizingResult:
    """Compute the §7 risk-based share count with the §6 cap override applied."""
    if account_equity <= 0:
        raise ValueError(f"account_equity must be > 0, got {account_equity}")
    if entry_price <= 0:
        raise ValueError(f"entry_price must be > 0, got {entry_price}")
    stop_distance = entry_price - initial_stop_price
    if stop_distance <= 0:
        raise ValueError(
            f"initial_stop ({initial_stop_price}) must be below entry "
            f"({entry_price}) for a long — stop_distance={stop_distance}"
        )

    risk_pct = cfg.sizing.risk_per_trade_pct / 100.0
    intended_risk = account_equity * risk_pct
    risk_shares = math.floor(intended_risk / stop_distance)

    cap_value = account_equity * cfg.portfolio.max_position_pct / 100.0
    cap_shares = math.floor(cap_value / entry_price)

    if cap_shares < risk_shares:
        shares, binding = cap_shares, POSITION_CAP
    else:
        shares, binding = risk_shares, RISK
    shares = max(shares, 0)

    position_value = shares * entry_price
    actual_risk = shares * stop_distance

    skipped = False
    skip_reason: str | None = None
    if shares <= 0:
        skipped, skip_reason = True, "zero_shares"
    elif position_value < cfg.sizing.min_position_value:
        skipped, skip_reason = True, "dust_min_value"

    return SizingResult(
        shares=shares,
        entry_price=float(entry_price),
        initial_stop=float(initial_stop_price),
        stop_distance=float(stop_distance),
        position_value=float(position_value),
        intended_risk=float(intended_risk),
        actual_risk=float(actual_risk),
        risk_pct=float(actual_risk / account_equity * 100.0),
        position_pct=float(position_value / account_equity * 100.0),
        binding_cap=binding,
        skipped=skipped,
        skip_reason=skip_reason,
    )
