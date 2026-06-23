"""Position-sizing tests (Rulebook §7) — incl. the rulebook's worked example."""
from __future__ import annotations

import pytest

from src.config_loader import load_config
from src.strategy.sizing import POSITION_CAP, RISK, size_position

CFG = load_config()  # risk_per_trade_pct=1.0, max_position_pct=10.0, min_position_value=200


def test_rulebook_worked_example_position_cap_binds():
    # §7: $5,000 @ 1% risk, entry 100, stop 95 -> risk wants 10 shares ($1,000 = 20%),
    # but the 10% cap ($500) wins -> 5 shares.
    r = size_position(5000.0, 100.0, 95.0, CFG)
    assert r.stop_distance == 5.0
    assert r.intended_risk == 50.0
    assert r.shares == 5
    assert r.binding_cap == POSITION_CAP
    assert r.position_value == 500.0
    assert r.position_pct == pytest.approx(10.0)
    assert r.actual_risk == 25.0
    assert r.risk_pct == pytest.approx(0.5)
    assert not r.skipped


def test_risk_binds_when_below_cap():
    # equity 100k, entry 50, stop 40 (1R=10): risk wants floor(1000/10)=100 shares
    # -> $5,000 = 5% < 10% cap, so RISK binds.
    r = size_position(100_000.0, 50.0, 40.0, CFG)
    assert r.shares == 100
    assert r.binding_cap == RISK
    assert r.position_pct == pytest.approx(5.0)
    assert r.risk_pct == pytest.approx(1.0)
    assert not r.skipped


def test_expensive_stock_zero_shares_skipped():
    # cap value $500, entry $600 -> cap_shares 0 -> skip
    r = size_position(5000.0, 600.0, 595.0, CFG)
    assert r.shares == 0
    assert r.skipped and r.skip_reason == "zero_shares"
    assert r.binding_cap == POSITION_CAP


def test_dust_below_min_value_skipped():
    # equity 1000 -> cap value 100 -> at entry 50 cap_shares=2 -> value 100 < $200 min
    r = size_position(1000.0, 50.0, 45.0, CFG)
    assert r.shares == 2
    assert r.position_value == 100.0
    assert r.skipped and r.skip_reason == "dust_min_value"


def test_stop_at_or_above_entry_raises():
    with pytest.raises(ValueError, match="below entry"):
        size_position(5000.0, 100.0, 100.0, CFG)
    with pytest.raises(ValueError, match="below entry"):
        size_position(5000.0, 100.0, 105.0, CFG)


def test_nonpositive_inputs_raise():
    with pytest.raises(ValueError, match="account_equity"):
        size_position(0.0, 100.0, 95.0, CFG)
    with pytest.raises(ValueError, match="entry_price"):
        size_position(5000.0, 0.0, -5.0, CFG)


def test_actual_risk_never_exceeds_intended_when_risk_binds():
    r = size_position(100_000.0, 50.0, 40.0, CFG)
    assert r.actual_risk <= r.intended_risk + 1e-9
