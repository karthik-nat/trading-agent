"""Load and validate ``config/rulebook.yaml`` into a typed config object.

This is the ONLY place strategy numbers enter the system. Code reads values from
the returned :class:`Config` object; nothing is hardcoded elsewhere.

Design goals (Phase 0):
  * Fail LOUDLY on any missing, mistyped, or out-of-range key — a typo'd
    parameter name must raise, never silently fall back to a default.
  * Reject unknown keys (a typo that would otherwise be ignored).
  * Cross-field sanity checks (e.g. 200-SMA > 50-SMA, min_history >= longest MA).

No strategy logic lives here — only loading and validation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from .paths import RULEBOOK_PATH, UNIVERSE_PATH

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")  # HH:MM 24h


class ConfigError(Exception):
    """Raised when the configuration is missing, malformed, or out of range."""


# --------------------------------------------------------------------------- #
# Section validator — pulls typed values from a dict and tracks consumed keys
# --------------------------------------------------------------------------- #
class _Section:
    """Helper to read+validate one mapping section with clear error messages."""

    def __init__(self, name: str, raw: Any):
        if not isinstance(raw, dict):
            raise ConfigError(
                f"section '{name}' must be a mapping, got {type(raw).__name__}"
            )
        self._name = name
        self._raw = raw
        self._used: set[str] = set()

    def _get(self, key: str) -> Any:
        if key not in self._raw:
            raise ConfigError(f"[{self._name}] missing required key '{key}'")
        self._used.add(key)
        return self._raw[key]

    def _range_err(self, key: str, val: Any, cond: str) -> ConfigError:
        return ConfigError(f"[{self._name}] '{key}' = {val!r} is out of range ({cond})")

    def num(
        self,
        key: str,
        *,
        gt: float | None = None,
        ge: float | None = None,
        lt: float | None = None,
        le: float | None = None,
    ) -> float:
        val = self._get(key)
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            raise ConfigError(
                f"[{self._name}] '{key}' must be a number, got {type(val).__name__}"
            )
        val = float(val)
        if gt is not None and not val > gt:
            raise self._range_err(key, val, f"must be > {gt}")
        if ge is not None and not val >= ge:
            raise self._range_err(key, val, f"must be >= {ge}")
        if lt is not None and not val < lt:
            raise self._range_err(key, val, f"must be < {lt}")
        if le is not None and not val <= le:
            raise self._range_err(key, val, f"must be <= {le}")
        return val

    def integer(
        self,
        key: str,
        *,
        gt: int | None = None,
        ge: int | None = None,
        lt: int | None = None,
        le: int | None = None,
    ) -> int:
        val = self._get(key)
        if isinstance(val, bool) or not isinstance(val, int):
            raise ConfigError(
                f"[{self._name}] '{key}' must be an integer, got {type(val).__name__}"
            )
        if gt is not None and not val > gt:
            raise self._range_err(key, val, f"must be > {gt}")
        if ge is not None and not val >= ge:
            raise self._range_err(key, val, f"must be >= {ge}")
        if lt is not None and not val < lt:
            raise self._range_err(key, val, f"must be < {lt}")
        if le is not None and not val <= le:
            raise self._range_err(key, val, f"must be <= {le}")
        return val

    def boolean(self, key: str) -> bool:
        val = self._get(key)
        if not isinstance(val, bool):
            raise ConfigError(
                f"[{self._name}] '{key}' must be a boolean, got {type(val).__name__}"
            )
        return val

    def string(self, key: str, *, choices: tuple[str, ...] | None = None,
               nonempty: bool = True) -> str:
        val = self._get(key)
        if not isinstance(val, str):
            raise ConfigError(
                f"[{self._name}] '{key}' must be a string, got {type(val).__name__}"
            )
        if nonempty and not val.strip():
            raise ConfigError(f"[{self._name}] '{key}' must be a non-empty string")
        if choices is not None and val not in choices:
            raise ConfigError(
                f"[{self._name}] '{key}' = {val!r} must be one of {choices}"
            )
        return val

    def str_list(self, key: str, *, nonempty: bool = True) -> list[str]:
        val = self._get(key)
        if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
            raise ConfigError(f"[{self._name}] '{key}' must be a list of strings")
        if nonempty and not val:
            raise ConfigError(f"[{self._name}] '{key}' must not be empty")
        return val

    def time_str(self, key: str) -> str:
        val = self.string(key)
        if not _TIME_RE.match(val):
            raise ConfigError(
                f"[{self._name}] '{key}' = {val!r} must be 24h time 'HH:MM'"
            )
        return val

    def done(self) -> None:
        """Raise if any key in the section was not consumed (catches typos)."""
        extra = set(self._raw) - self._used
        if extra:
            raise ConfigError(
                f"[{self._name}] unknown key(s): {sorted(extra)} "
                f"(typo? remove or correct in rulebook.yaml)"
            )


# --------------------------------------------------------------------------- #
# Typed config dataclasses — one per rulebook section
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MetaConfig:
    version: str
    engine: str
    description: str


@dataclass(frozen=True)
class UniverseConfig:
    exchanges: list[str]
    min_price: float
    min_avg_dollar_volume: float
    min_market_cap: float
    earnings_blackout_days: int
    min_history_days: int
    sector_source: str


@dataclass(frozen=True)
class RegimeConfig:
    trend_ma_long: int
    trend_ma_mid: int
    trend_slope_lookback: int
    market_filter_symbol: str
    market_filter_ma: int


@dataclass(frozen=True)
class EntryConfig:
    trend_ma_short: int
    pullback_proximity_pct: float
    rsi_period: int
    rsi_turn_low: float
    rsi_turn_high: float
    rsi_must_turn_up: bool
    macd_fast: int
    macd_slow: int
    macd_signal: int
    macd_prefer_above_zero: bool
    volume_avg_period: int
    volume_confirm_mult: float
    confluence_required: int
    anti_chase_max_ext_pct: float
    rs_lookback_days: int


@dataclass(frozen=True)
class SizingConfig:
    risk_per_trade_pct: float
    min_position_value: float


@dataclass(frozen=True)
class PortfolioConfig:
    target_positions_min: int
    target_positions_max: int
    min_positions_full: int
    max_position_pct: float
    max_sector_pct: float
    correlation_threshold: float
    correlation_lookback_days: int
    max_correlated_names: int
    cash_floor_pct: float
    max_new_positions_per_day: int


@dataclass(frozen=True)
class ExitsConfig:
    atr_period: int
    initial_stop_atr_mult: float
    use_swing_low: bool
    first_scale_r: float
    first_scale_fraction: float
    breakeven_trigger_r: float
    trail_ma: int
    trail_atr_mult: float
    trend_break_ma: int
    time_stop_days: int
    time_stop_min_r: float


@dataclass(frozen=True)
class RiskConfig:
    dd_circuit_breaker_pct: float
    dd_hard_halt_pct: float
    consec_loss_pause: int
    consec_loss_pause_days: int
    daily_new_risk_cap_pct: float
    total_heat_cap_pct: float


@dataclass(frozen=True)
class ProtocolConfig:
    go_live_min_trades: int
    go_live_min_expectancy_r: float
    model_commission_per_trade: float
    model_slippage_bps: float


@dataclass(frozen=True)
class SystemConfig:
    cadence: str
    morning_run_local: str
    afternoon_run_local: str
    timezone: str
    data_provider: str
    delayed_data_ok: bool
    execution_mode: str
    broker_mcp_url: str
    agentic_account_only: bool


@dataclass(frozen=True)
class Config:
    meta: MetaConfig
    universe: UniverseConfig
    regime: RegimeConfig
    entry: EntryConfig
    sizing: SizingConfig
    portfolio: PortfolioConfig
    exits: ExitsConfig
    risk: RiskConfig
    protocol: ProtocolConfig
    system: SystemConfig


# --------------------------------------------------------------------------- #
# Section parsers
# --------------------------------------------------------------------------- #
def _parse_meta(raw: Any) -> MetaConfig:
    s = _Section("meta", raw)
    cfg = MetaConfig(
        version=s.string("version"),
        engine=s.string("engine"),
        description=s.string("description"),
    )
    s.done()
    return cfg


def _parse_universe(raw: Any) -> UniverseConfig:
    s = _Section("universe", raw)
    cfg = UniverseConfig(
        exchanges=s.str_list("exchanges"),
        min_price=s.num("min_price", gt=0),
        min_avg_dollar_volume=s.num("min_avg_dollar_volume", gt=0),
        min_market_cap=s.num("min_market_cap", gt=0),
        earnings_blackout_days=s.integer("earnings_blackout_days", ge=0),
        min_history_days=s.integer("min_history_days", gt=0),
        sector_source=s.string("sector_source"),
    )
    s.done()
    return cfg


def _parse_regime(raw: Any) -> RegimeConfig:
    s = _Section("regime", raw)
    cfg = RegimeConfig(
        trend_ma_long=s.integer("trend_ma_long", gt=0),
        trend_ma_mid=s.integer("trend_ma_mid", gt=0),
        trend_slope_lookback=s.integer("trend_slope_lookback", gt=0),
        market_filter_symbol=s.string("market_filter_symbol"),
        market_filter_ma=s.integer("market_filter_ma", gt=0),
    )
    s.done()
    return cfg


def _parse_entry(raw: Any) -> EntryConfig:
    s = _Section("entry", raw)
    cfg = EntryConfig(
        trend_ma_short=s.integer("trend_ma_short", gt=0),
        pullback_proximity_pct=s.num("pullback_proximity_pct", gt=0, le=100),
        rsi_period=s.integer("rsi_period", gt=0),
        rsi_turn_low=s.num("rsi_turn_low", ge=0, le=100),
        rsi_turn_high=s.num("rsi_turn_high", ge=0, le=100),
        rsi_must_turn_up=s.boolean("rsi_must_turn_up"),
        macd_fast=s.integer("macd_fast", gt=0),
        macd_slow=s.integer("macd_slow", gt=0),
        macd_signal=s.integer("macd_signal", gt=0),
        macd_prefer_above_zero=s.boolean("macd_prefer_above_zero"),
        volume_avg_period=s.integer("volume_avg_period", gt=0),
        volume_confirm_mult=s.num("volume_confirm_mult", gt=0),
        confluence_required=s.integer("confluence_required", ge=1, le=4),
        anti_chase_max_ext_pct=s.num("anti_chase_max_ext_pct", gt=0),
        rs_lookback_days=s.integer("rs_lookback_days", gt=0),
    )
    s.done()
    if not cfg.rsi_turn_low < cfg.rsi_turn_high:
        raise ConfigError(
            "[entry] rsi_turn_low must be < rsi_turn_high "
            f"({cfg.rsi_turn_low} !< {cfg.rsi_turn_high})"
        )
    if not cfg.macd_fast < cfg.macd_slow:
        raise ConfigError(
            "[entry] macd_fast must be < macd_slow "
            f"({cfg.macd_fast} !< {cfg.macd_slow})"
        )
    return cfg


def _parse_sizing(raw: Any) -> SizingConfig:
    s = _Section("sizing", raw)
    cfg = SizingConfig(
        risk_per_trade_pct=s.num("risk_per_trade_pct", gt=0, le=100),
        min_position_value=s.num("min_position_value", ge=0),
    )
    s.done()
    return cfg


def _parse_portfolio(raw: Any) -> PortfolioConfig:
    s = _Section("portfolio", raw)
    cfg = PortfolioConfig(
        target_positions_min=s.integer("target_positions_min", gt=0),
        target_positions_max=s.integer("target_positions_max", gt=0),
        min_positions_full=s.integer("min_positions_full", gt=0),
        max_position_pct=s.num("max_position_pct", gt=0, le=100),
        max_sector_pct=s.num("max_sector_pct", gt=0, le=100),
        correlation_threshold=s.num("correlation_threshold", ge=-1, le=1),
        correlation_lookback_days=s.integer("correlation_lookback_days", gt=0),
        max_correlated_names=s.integer("max_correlated_names", gt=0),
        cash_floor_pct=s.num("cash_floor_pct", ge=0, le=100),
        max_new_positions_per_day=s.integer("max_new_positions_per_day", gt=0),
    )
    s.done()
    if not cfg.target_positions_min <= cfg.target_positions_max:
        raise ConfigError(
            "[portfolio] target_positions_min must be <= target_positions_max "
            f"({cfg.target_positions_min} > {cfg.target_positions_max})"
        )
    return cfg


def _parse_exits(raw: Any) -> ExitsConfig:
    s = _Section("exits", raw)
    cfg = ExitsConfig(
        atr_period=s.integer("atr_period", gt=0),
        initial_stop_atr_mult=s.num("initial_stop_atr_mult", gt=0),
        use_swing_low=s.boolean("use_swing_low"),
        first_scale_r=s.num("first_scale_r", gt=0),
        first_scale_fraction=s.num("first_scale_fraction", gt=0, le=1),
        breakeven_trigger_r=s.num("breakeven_trigger_r", gt=0),
        trail_ma=s.integer("trail_ma", gt=0),
        trail_atr_mult=s.num("trail_atr_mult", gt=0),
        trend_break_ma=s.integer("trend_break_ma", gt=0),
        time_stop_days=s.integer("time_stop_days", gt=0),
        time_stop_min_r=s.num("time_stop_min_r", ge=0),
    )
    s.done()
    return cfg


def _parse_risk(raw: Any) -> RiskConfig:
    s = _Section("risk", raw)
    cfg = RiskConfig(
        dd_circuit_breaker_pct=s.num("dd_circuit_breaker_pct", gt=0, le=100),
        dd_hard_halt_pct=s.num("dd_hard_halt_pct", gt=0, le=100),
        consec_loss_pause=s.integer("consec_loss_pause", gt=0),
        consec_loss_pause_days=s.integer("consec_loss_pause_days", gt=0),
        daily_new_risk_cap_pct=s.num("daily_new_risk_cap_pct", gt=0, le=100),
        total_heat_cap_pct=s.num("total_heat_cap_pct", gt=0, le=100),
    )
    s.done()
    if not cfg.dd_circuit_breaker_pct < cfg.dd_hard_halt_pct:
        raise ConfigError(
            "[risk] dd_circuit_breaker_pct must be < dd_hard_halt_pct "
            f"({cfg.dd_circuit_breaker_pct} !< {cfg.dd_hard_halt_pct})"
        )
    return cfg


def _parse_protocol(raw: Any) -> ProtocolConfig:
    s = _Section("protocol", raw)
    cfg = ProtocolConfig(
        go_live_min_trades=s.integer("go_live_min_trades", gt=0),
        go_live_min_expectancy_r=s.num("go_live_min_expectancy_r"),
        model_commission_per_trade=s.num("model_commission_per_trade", ge=0),
        model_slippage_bps=s.num("model_slippage_bps", ge=0),
    )
    s.done()
    return cfg


def _parse_system(raw: Any) -> SystemConfig:
    s = _Section("system", raw)
    cfg = SystemConfig(
        cadence=s.string("cadence"),
        morning_run_local=s.time_str("morning_run_local"),
        afternoon_run_local=s.time_str("afternoon_run_local"),
        timezone=s.string("timezone"),
        data_provider=s.string("data_provider"),
        delayed_data_ok=s.boolean("delayed_data_ok"),
        execution_mode=s.string(
            "execution_mode", choices=("readonly", "preview", "autonomous")
        ),
        broker_mcp_url=s.string("broker_mcp_url"),
        agentic_account_only=s.boolean("agentic_account_only"),
    )
    s.done()
    try:
        ZoneInfo(cfg.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(
            f"[system] timezone = {cfg.timezone!r} is not a valid IANA zone"
        ) from exc
    return cfg


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
_REQUIRED_SECTIONS = (
    "meta", "universe", "regime", "entry", "sizing",
    "portfolio", "exits", "risk", "protocol", "system",
)


def load_config(path: str | Path = RULEBOOK_PATH) -> Config:
    """Load and validate ``rulebook.yaml``, returning a typed :class:`Config`.

    Raises :class:`ConfigError` (loudly, naming the offending key) on any
    missing/mistyped/out-of-range value, unknown key, or failed cross-check.
    """
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ConfigError(f"could not parse YAML in {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain a top-level mapping")

    missing = [s for s in _REQUIRED_SECTIONS if s not in raw]
    if missing:
        raise ConfigError(f"missing top-level section(s): {missing}")
    unknown = set(raw) - set(_REQUIRED_SECTIONS)
    if unknown:
        raise ConfigError(f"unknown top-level section(s): {sorted(unknown)}")

    cfg = Config(
        meta=_parse_meta(raw["meta"]),
        universe=_parse_universe(raw["universe"]),
        regime=_parse_regime(raw["regime"]),
        entry=_parse_entry(raw["entry"]),
        sizing=_parse_sizing(raw["sizing"]),
        portfolio=_parse_portfolio(raw["portfolio"]),
        exits=_parse_exits(raw["exits"]),
        risk=_parse_risk(raw["risk"]),
        protocol=_parse_protocol(raw["protocol"]),
        system=_parse_system(raw["system"]),
    )

    # ---- cross-section sanity checks (catch internally inconsistent configs) ----
    if not cfg.regime.trend_ma_mid < cfg.regime.trend_ma_long:
        raise ConfigError(
            "[regime] trend_ma_mid must be < trend_ma_long "
            f"({cfg.regime.trend_ma_mid} !< {cfg.regime.trend_ma_long})"
        )
    if not cfg.entry.trend_ma_short < cfg.regime.trend_ma_mid:
        raise ConfigError(
            "trend_ma_short (entry) must be < trend_ma_mid (regime) "
            f"({cfg.entry.trend_ma_short} !< {cfg.regime.trend_ma_mid})"
        )
    longest_ma = max(cfg.regime.trend_ma_long, cfg.regime.market_filter_ma)
    if cfg.universe.min_history_days < longest_ma:
        raise ConfigError(
            f"[universe] min_history_days ({cfg.universe.min_history_days}) must be "
            f">= longest MA period ({longest_ma}) to compute it"
        )
    return cfg


@dataclass(frozen=True)
class SeedName:
    ticker: str
    sector: str
    name: str


@dataclass(frozen=True)
class UniverseFile:
    seed_universe: list[SeedName]
    market_index: str


def load_seed_universe(path: str | Path = UNIVERSE_PATH) -> UniverseFile:
    """Load the seed test universe (``config/universe.yaml``).

    Plumbing only — this is the seed list for Phase 0 data pulls, NOT the §3
    tradeability gate (that is strategy logic, built in Phase 1).
    """
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"universe file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ConfigError(f"could not parse YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict) or "seed_universe" not in raw:
        raise ConfigError(f"{path} must define a 'seed_universe' list")

    names: list[SeedName] = []
    seen: set[str] = set()
    for i, item in enumerate(raw["seed_universe"]):
        if not isinstance(item, dict) or "ticker" not in item:
            raise ConfigError(f"seed_universe[{i}] must be a mapping with 'ticker'")
        ticker = str(item["ticker"]).strip().upper()
        if not ticker:
            raise ConfigError(f"seed_universe[{i}] has an empty ticker")
        if ticker in seen:
            raise ConfigError(f"seed_universe has duplicate ticker {ticker!r}")
        seen.add(ticker)
        names.append(
            SeedName(
                ticker=ticker,
                sector=str(item.get("sector", "Unknown")),
                name=str(item.get("name", "")),
            )
        )
    if not names:
        raise ConfigError(f"{path}: seed_universe is empty")

    market_index = str(raw.get("market_index", "^GSPC"))
    return UniverseFile(seed_universe=names, market_index=market_index)
