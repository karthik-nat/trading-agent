"""Decision engine — orchestrates the funnel into recommendations (Rulebook §10).

    universe -> regime (§4) -> entry (§5) -> initial stop & size (§7/§8)
             -> portfolio caps (§6/§9) -> ranked recommendations.

Deterministic and offline. It produces RECOMMENDATIONS ONLY — never orders. Held
positions are always managed (even when the market filter is OFF); new BUY
candidates are generated only when the market filter is ON, ranked, then filled
top-down subject to the §6 portfolio caps. Candidates that qualify technically but
are blocked by a cap or by full slots are surfaced as WATCH with the binding cap.

Phase 1 scope: no §9 drawdown / consecutive-loss circuit breakers (those + the
pre-trade guard are Phase 5, risk/guards.py) and no live data or orders.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .config_loader import Config
from .strategy.entry_pullback import evaluate_entry, rank_candidates, relative_strength
from .strategy.exits import (
    Position,
    compute_initial_stop,
    evaluate_exit,
    first_scale_target,
)
from .strategy.portfolio import Candidate, Holding, PortfolioState, evaluate_candidate
from .strategy.regime import evaluate_market_filter, evaluate_regime
from .strategy.sizing import size_position

# actions
BUY = "BUY"
WATCH = "WATCH"     # qualified candidate not actioned (slot/cap) — see binding_cap
TRIM = "TRIM"
EXIT = "EXIT"
HOLD = "HOLD"


@dataclass(frozen=True)
class AccountState:
    equity: float
    cash: float
    new_positions_today: int = 0


@dataclass(frozen=True)
class HeldPosition:
    ticker: str
    sector: str
    entry_price: float
    initial_stop: float
    current_stop: float
    shares: float
    entry_index: int
    scaled: bool = False


@dataclass(frozen=True)
class Recommendation:
    action: str
    ticker: str
    reason_codes: list[str] = field(default_factory=list)
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    shares: int | None = None
    position_pct: float | None = None
    risk_pct: float | None = None
    binding_cap: str | None = None
    rank: int | None = None
    score: float | None = None
    notes: str | None = None


@dataclass(frozen=True)
class EngineResult:
    market_filter_on: bool
    recommendations: list[Recommendation]

    def by_action(self, action: str) -> list[Recommendation]:
        return [r for r in self.recommendations if r.action == action]


def _returns_by_date(df: pd.DataFrame) -> pd.Series:
    """Daily returns indexed by date (for correlation alignment)."""
    idx = df["date"] if "date" in df.columns else df.index
    return pd.Series(df["close"].pct_change().values, index=pd.Index(idx))


def run_engine(
    cfg: Config,
    prices: dict[str, pd.DataFrame],
    index_df: pd.DataFrame,
    account: AccountState,
    held: list[HeldPosition],
    sectors: dict[str, str],
) -> EngineResult:
    """Run the full funnel and return ranked recommendations."""
    market = evaluate_market_filter(index_df, cfg)
    recs: list[Recommendation] = []
    held_tickers = {h.ticker for h in held}

    # ---- 1) manage held positions (regardless of market filter) ------------- #
    accepted_holdings: list[Holding] = []
    cash = account.cash
    for h in held:
        df = prices[h.ticker]
        last_close = float(df["close"].iloc[-1])
        pos = Position(
            ticker=h.ticker, entry_price=h.entry_price, initial_stop=h.initial_stop,
            entry_index=h.entry_index, shares=h.shares, scaled=h.scaled,
        )
        d = evaluate_exit(pos, df, cfg)
        open_risk = max(0.0, h.shares * (last_close - d.suggested_stop))
        recs.append(
            Recommendation(
                action=d.action, ticker=h.ticker, reason_codes=list(d.reason_codes),
                entry=h.entry_price, stop=d.suggested_stop, target=d.scale_target,
                shares=int(h.shares), position_pct=h.shares * last_close / account.equity * 100.0,
                risk_pct=open_risk / account.equity * 100.0, score=d.current_r,
                notes=f"bars_held={d.bars_held}",
            )
        )
        accepted_holdings.append(
            Holding(h.ticker, h.sector, market_value=h.shares * last_close,
                    open_risk=open_risk, returns=_returns_by_date(df))
        )

    # ---- 2) scan for new buy candidates (only when market filter ON) -------- #
    if market.on:
        cand_rows = []
        for ticker, df in prices.items():
            if ticker in held_tickers or ticker == cfg.regime.market_filter_symbol:
                continue
            if not evaluate_regime(df, cfg, ticker=ticker).passed:
                continue
            sig = evaluate_entry(df, cfg, ticker=ticker)
            if not sig.triggered:
                continue
            stop_res = compute_initial_stop(df, sig.close, cfg)
            size = size_position(account.equity, sig.close, stop_res.stop, cfg)
            stop_dist_pct = (sig.close - stop_res.stop) / sig.close * 100.0
            reward_risk = 1.0 / stop_dist_pct if stop_dist_pct > 0 else 0.0
            rs = relative_strength(df, index_df, cfg)
            cand_rows.append({
                "ticker": ticker, "sector": sectors.get(ticker, "Unknown"),
                "sig": sig, "stop": stop_res.stop, "size": size,
                "target": first_scale_target(sig.close, stop_res.stop, cfg),
                "rs": rs if rs == rs else 0.0, "rr": reward_risk,
                "returns": _returns_by_date(df),
            })

        ranked = rank_candidates([(c["ticker"], c["rs"], c["rr"]) for c in cand_rows])
        rank_by_ticker = {r.key: r for r in ranked}
        cand_rows.sort(key=lambda c: rank_by_ticker[c["ticker"]].rank)

        new_today = account.new_positions_today
        for c in cand_rows:
            size = c["size"]
            rk = rank_by_ticker[c["ticker"]]
            reasons = _entry_reasons(c["sig"])
            cand = Candidate(
                ticker=c["ticker"], sector=c["sector"],
                position_value=size.position_value, risk=size.actual_risk,
                returns=c["returns"],
            )
            state = PortfolioState(account.equity, cash, new_today)
            decision = evaluate_candidate(cand, accepted_holdings, state, cfg)

            actionable = decision.allowed and not size.skipped
            binding = None
            if size.skipped:
                binding = f"size:{size.skip_reason}"
                reasons = reasons + [f"skipped_{size.skip_reason}"]
            elif decision.binding_caps:
                binding = decision.binding_caps[0]
                reasons = reasons + [f"blocked:{','.join(decision.binding_caps)}"]
            else:
                binding = size.binding_cap  # which cap set the share count
            reasons = reasons + list(decision.warnings)

            recs.append(
                Recommendation(
                    action=BUY if actionable else WATCH,
                    ticker=c["ticker"], reason_codes=reasons,
                    entry=c["sig"].close, stop=c["stop"], target=c["target"],
                    shares=size.shares, position_pct=size.position_pct,
                    risk_pct=size.risk_pct, binding_cap=binding,
                    rank=rk.rank, score=c["rs"],
                )
            )
            if actionable:
                accepted_holdings.append(
                    Holding(c["ticker"], c["sector"], market_value=size.position_value,
                            open_risk=size.actual_risk, returns=c["returns"])
                )
                cash -= size.position_value
                new_today += 1

    return EngineResult(market_filter_on=market.on, recommendations=recs)


def _entry_reasons(sig) -> list[str]:
    codes = []
    if sig.e1_pullback:
        codes.append("E1_pullback")
    if sig.e2_rsi_turn:
        codes.append("E2_rsi_turn")
    if sig.e3_macd_resume:
        codes.append("E3_macd_resume")
    if sig.e4_volume:
        codes.append("E4_volume")
    return codes
