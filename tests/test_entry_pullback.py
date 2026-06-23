"""Entry-engine tests (Rulebook §5) — a series that MUST trigger and ones that MUST NOT."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.config_loader import load_config
from src.strategy.entry_pullback import (
    evaluate_entry,
    rank_candidates,
    relative_strength,
)

CFG = load_config()


def _frame(close: np.ndarray, volume: np.ndarray) -> pd.DataFrame:
    return pd.DataFrame(
        {"open": close, "high": close + 0.5, "low": close - 0.5,
         "close": close, "volume": volume}
    )


def _pullback_setup(pull_bottom=124.0, resume=(125.5, 127.0), vol_spike=True) -> pd.DataFrame:
    """Uptrend -> multi-bar pullback toward the 20-SMA -> resumption up-day."""
    up = np.linspace(100, 130, 60)
    pull = np.linspace(130, pull_bottom, 6)[1:]           # 5 down bars
    close = np.concatenate([up, pull, np.array(resume, dtype=float)])
    vol = np.full(len(close), 1_000_000.0)
    if vol_spike:
        vol[-1] = 1_500_000.0                             # >1.2x avg on resume day
    return _frame(close, vol)


# --------------------------------------------------------------------------- #
# MUST trigger
# --------------------------------------------------------------------------- #
def test_textbook_pullback_triggers_all_four():
    s = evaluate_entry(_pullback_setup(), CFG, ticker="GOOD")
    assert s.triggered is True
    assert s.confluence_count == 4
    assert s.e1_pullback and s.e2_rsi_turn and s.e3_macd_resume and s.e4_volume
    assert s.anti_chase is False
    assert s.rsi > s.rsi_prev                              # turning up
    assert s.volume_ratio >= CFG.entry.volume_confirm_mult


# --------------------------------------------------------------------------- #
# MUST NOT trigger
# --------------------------------------------------------------------------- #
def test_missing_volume_drops_to_three_and_blocks():
    s = evaluate_entry(_pullback_setup(vol_spike=False), CFG, ticker="NOVOL")
    assert s.e4_volume is False
    assert s.confluence_count == 3
    assert s.triggered is False                            # confluence_required == 4


def test_extended_price_is_anti_chased():
    close = np.concatenate([np.linspace(100, 130, 60), np.array([145.0])])
    s = evaluate_entry(_frame(close, np.r_[np.full(60, 1e6), 2e6]), CFG, ticker="EXT")
    assert s.extension_pct > CFG.entry.anti_chase_max_ext_pct
    assert s.anti_chase is True
    assert s.triggered is False                            # cap beats signal


def test_plain_downtrend_does_not_trigger():
    close = np.linspace(150, 90, 80)
    s = evaluate_entry(_frame(close, np.full(80, 1e6)), CFG, ticker="DN")
    assert s.triggered is False


def test_insufficient_history_fails_safe():
    close = np.linspace(100, 110, 15)
    s = evaluate_entry(_frame(close, np.full(15, 1e6)), CFG, ticker="SHORT")
    assert s.triggered is False
    assert s.confluence_count == 0


# --------------------------------------------------------------------------- #
# Relative strength
# --------------------------------------------------------------------------- #
def test_relative_strength_sign():
    n = 80
    strong = _frame(np.linspace(100, 140, n), np.full(n, 1e6))   # +40%
    weak = _frame(np.linspace(100, 105, n), np.full(n, 1e6))     # +5%
    index = _frame(np.linspace(100, 120, n), np.full(n, 1e6))    # +20%
    assert relative_strength(strong, index, CFG) > 0
    assert relative_strength(weak, index, CFG) < 0


def test_relative_strength_nan_when_short():
    n = 20  # < rs_lookback_days (63)
    df = _frame(np.linspace(100, 110, n), np.full(n, 1e6))
    assert np.isnan(relative_strength(df, df, CFG))


# --------------------------------------------------------------------------- #
# Ranking (weight-free rank-sum)
# --------------------------------------------------------------------------- #
def test_rank_candidates_orders_by_composite():
    ranked = rank_candidates(
        [("A", 0.30, 3.0), ("B", 0.20, 2.0), ("C", 0.10, 1.0)]
    )
    assert [r.key for r in ranked] == ["A", "B", "C"]
    assert ranked[0].rank == 1 and ranked[0].composite == 2  # rs_rank1 + rr_rank1


def test_rank_candidates_tie_breaks_by_rs():
    # X: best RS / worst RR ; Y: worst RS / best RR -> composite tie, RS wins
    ranked = rank_candidates([("X", 0.50, 1.0), ("Y", 0.10, 5.0)])
    assert ranked[0].key == "X"
    assert ranked[0].composite == ranked[1].composite


def test_rank_candidates_empty():
    assert rank_candidates([]) == []
