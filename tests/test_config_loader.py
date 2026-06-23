"""Config-validation tests: the real rulebook loads, and bad configs fail loudly."""
from __future__ import annotations

import copy

import pytest
import yaml

from src.config_loader import (
    Config,
    ConfigError,
    load_config,
    load_seed_universe,
)
from src.paths import RULEBOOK_PATH


@pytest.fixture()
def raw_rulebook() -> dict:
    """The real rulebook.yaml parsed to a plain dict (deep-copied per test)."""
    return copy.deepcopy(yaml.safe_load(RULEBOOK_PATH.read_text()))


def _write(tmp_path, data: dict):
    p = tmp_path / "rulebook.yaml"
    p.write_text(yaml.safe_dump(data, sort_keys=False))
    return p


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #
def test_real_rulebook_loads_and_is_typed():
    cfg = load_config()
    assert isinstance(cfg, Config)
    # spot-check values come through with correct types (no hardcoding in code)
    assert cfg.meta.version == "1.0"
    assert cfg.universe.min_price == 10.0
    assert cfg.universe.min_market_cap == 2_000_000_000
    assert cfg.regime.trend_ma_long == 200
    assert cfg.regime.trend_ma_mid == 50
    assert cfg.entry.trend_ma_short == 20
    assert cfg.entry.confluence_required == 4
    assert cfg.sizing.risk_per_trade_pct == 1.0
    assert cfg.portfolio.max_position_pct == 10.0
    assert cfg.exits.first_scale_r == 2.0
    assert cfg.exits.swing_lookback_days == 10
    assert cfg.risk.dd_hard_halt_pct == 25.0
    assert cfg.protocol.go_live_min_trades == 50
    assert cfg.system.execution_mode == "readonly"


def test_config_is_frozen():
    cfg = load_config()
    with pytest.raises(Exception):
        cfg.sizing.risk_per_trade_pct = 5.0  # type: ignore[misc]


def test_roundtrip_real_rulebook_via_tmp(tmp_path, raw_rulebook):
    cfg = load_config(_write(tmp_path, raw_rulebook))
    assert cfg.regime.market_filter_symbol == "^GSPC"


# --------------------------------------------------------------------------- #
# Loud failures
# --------------------------------------------------------------------------- #
def test_missing_section_raises(tmp_path, raw_rulebook):
    del raw_rulebook["risk"]
    with pytest.raises(ConfigError, match="missing top-level section"):
        load_config(_write(tmp_path, raw_rulebook))


def test_missing_key_raises(tmp_path, raw_rulebook):
    del raw_rulebook["sizing"]["risk_per_trade_pct"]
    with pytest.raises(ConfigError, match="missing required key 'risk_per_trade_pct'"):
        load_config(_write(tmp_path, raw_rulebook))


def test_unknown_key_raises(tmp_path, raw_rulebook):
    raw_rulebook["sizing"]["risk_per_trad_pct"] = 1.0  # typo
    with pytest.raises(ConfigError, match="unknown key"):
        load_config(_write(tmp_path, raw_rulebook))


def test_unknown_section_raises(tmp_path, raw_rulebook):
    raw_rulebook["bogus"] = {}
    with pytest.raises(ConfigError, match="unknown top-level section"):
        load_config(_write(tmp_path, raw_rulebook))


def test_wrong_type_raises(tmp_path, raw_rulebook):
    raw_rulebook["sizing"]["risk_per_trade_pct"] = "lots"
    with pytest.raises(ConfigError, match="must be a number"):
        load_config(_write(tmp_path, raw_rulebook))


def test_bool_not_accepted_as_number(tmp_path, raw_rulebook):
    raw_rulebook["universe"]["min_price"] = True
    with pytest.raises(ConfigError, match="must be a number"):
        load_config(_write(tmp_path, raw_rulebook))


def test_negative_min_price_raises(tmp_path, raw_rulebook):
    raw_rulebook["universe"]["min_price"] = -5
    with pytest.raises(ConfigError, match="out of range"):
        load_config(_write(tmp_path, raw_rulebook))


def test_confluence_out_of_range_raises(tmp_path, raw_rulebook):
    raw_rulebook["entry"]["confluence_required"] = 5  # only 4 conditions exist
    with pytest.raises(ConfigError, match="out of range"):
        load_config(_write(tmp_path, raw_rulebook))


def test_pct_above_100_raises(tmp_path, raw_rulebook):
    raw_rulebook["portfolio"]["max_position_pct"] = 150
    with pytest.raises(ConfigError, match="out of range"):
        load_config(_write(tmp_path, raw_rulebook))


def test_rsi_zone_inverted_raises(tmp_path, raw_rulebook):
    raw_rulebook["entry"]["rsi_turn_low"] = 60
    raw_rulebook["entry"]["rsi_turn_high"] = 50
    with pytest.raises(ConfigError, match="rsi_turn_low must be < rsi_turn_high"):
        load_config(_write(tmp_path, raw_rulebook))


def test_ma_ordering_cross_check_raises(tmp_path, raw_rulebook):
    raw_rulebook["regime"]["trend_ma_mid"] = 250  # mid must be < long (200)
    with pytest.raises(ConfigError, match="trend_ma_mid must be < trend_ma_long"):
        load_config(_write(tmp_path, raw_rulebook))


def test_drawdown_breaker_ordering_raises(tmp_path, raw_rulebook):
    raw_rulebook["risk"]["dd_circuit_breaker_pct"] = 30  # must be < hard halt (25)
    with pytest.raises(ConfigError, match="dd_circuit_breaker_pct must be < dd_hard_halt"):
        load_config(_write(tmp_path, raw_rulebook))


def test_min_history_too_short_raises(tmp_path, raw_rulebook):
    raw_rulebook["universe"]["min_history_days"] = 100  # < 200-SMA need
    with pytest.raises(ConfigError, match="min_history_days"):
        load_config(_write(tmp_path, raw_rulebook))


def test_bad_time_format_raises(tmp_path, raw_rulebook):
    raw_rulebook["system"]["morning_run_local"] = "8.30am"
    with pytest.raises(ConfigError, match="24h time"):
        load_config(_write(tmp_path, raw_rulebook))


def test_bad_timezone_raises(tmp_path, raw_rulebook):
    raw_rulebook["system"]["timezone"] = "Mars/Olympus"
    with pytest.raises(ConfigError, match="not a valid IANA zone"):
        load_config(_write(tmp_path, raw_rulebook))


def test_bad_execution_mode_raises(tmp_path, raw_rulebook):
    raw_rulebook["system"]["execution_mode"] = "yolo"
    with pytest.raises(ConfigError, match="must be one of"):
        load_config(_write(tmp_path, raw_rulebook))


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "does_not_exist.yaml")


# --------------------------------------------------------------------------- #
# Seed universe loader
# --------------------------------------------------------------------------- #
def test_seed_universe_loads():
    uni = load_seed_universe()
    assert 20 <= len(uni.seed_universe) <= 50
    assert uni.market_index == "^GSPC"
    tickers = {n.ticker for n in uni.seed_universe}
    assert "AAPL" in tickers and "JPM" in tickers
    assert all(n.sector for n in uni.seed_universe)


def test_seed_universe_rejects_duplicates(tmp_path):
    p = tmp_path / "universe.yaml"
    p.write_text(
        yaml.safe_dump(
            {"seed_universe": [{"ticker": "AAPL"}, {"ticker": "AAPL"}]}
        )
    )
    with pytest.raises(ConfigError, match="duplicate ticker"):
        load_seed_universe(p)
