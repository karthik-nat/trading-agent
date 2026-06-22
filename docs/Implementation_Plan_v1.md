# Phased Implementation Plan — Multi-Stock Technical Swing System (v1)

**Companion to:** Strategy Rulebook v1
**Version:** 1.0
**Status:** Draft for review
**Last updated:** 2026-06-17

---

## 0. How to use this document

This plan turns the Strategy Rulebook into a build sequence. It is organized so that **the brain is built and validated offline first, real money and autonomy come last, and every phase ends in a testable gate** that must pass before the next phase starts. The rulebook is the *what*; this plan is the *how* and the *in-what-order*.

Two principles govern everything below:

1. **Rules live in code, not in prompts.** The strategy is deterministic Python that reads parameters from a config file (the rulebook's §12 table). The Robinhood MCP is used only as a broker adapter — reading account state and placing the exact orders the engine computed. We never hand "run my strategy" to a free-form LLM agent; that would dissolve the testability and the risk caps.
2. **Money is gated.** Phases 0–2 cost nothing but time. Phases 3–4 touch live data and paper money but never real capital. Only Phase 5 deploys the $5K, and only after a validated edge.

**Effort labels** (S/M/L) are relative build sizes, not calendar promises. With Claude Code, expect each phase to be a handful of focused sessions; the gates matter more than the speed.

---

## 1. Target architecture (end state)

```
                    ┌─────────────────────┐
   Market Data API  │   DATA LAYER        │   yfinance (free) →
   (historical      │   prices → parquet  │   Alpaca/Massive (paid)
    bars, indicators)│  state   → SQLite   │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │   SIGNAL ENGINE     │   deterministic rulebook:
                    │  gate→regime→entry  │   universe, regime, trend-pullback,
                    │  →sizing→exits→port │   sizing, exits, portfolio caps
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
     ┌────────▼──────┐ ┌───────▼───────┐ ┌──────▼────────┐
     │  BACKTEST     │ │  RISK GUARDS  │ │  DASHBOARD    │
     │ (validation)  │ │ breakers +    │ │  (Streamlit)  │
     │               │ │ pre-trade     │ │  twice daily  │
     └───────────────┘ └───────┬───────┘ └───────────────┘
                               │
                    ┌──────────▼──────────┐
                    │  BROKER ADAPTER     │   Robinhood MCP:
                    │  read state + quote │   - read: positions/balances/orders
                    │  + place orders     │   - execute: agentic account only
                    └─────────────────────┘   Alpaca paper for validation
```

The MCP supplies account state, a live quote at order time, and execution; **it is not the indicator data source** (it serves point-in-time quotes, not bulk historical bars). Historical bars for RSI/MACD/MA/ATR come from the market-data API.

---

## 2. Repository structure

```
ai-trading-analyst/
├── config/
│   ├── rulebook.yaml          # the §12 parameter table — single source of strategy settings
│   ├── universe.yaml          # seed universe + tradeability thresholds
│   └── secrets.env            # API keys (gitignored, never committed)
├── data/
│   ├── prices/                # parquet historical OHLCV bars
│   └── trading.db             # SQLite: positions, trades, signals, journal, equity
├── src/
│   ├── config_loader.py       # loads + validates rulebook.yaml into typed config
│   ├── data/
│   │   ├── market_data.py     # data API adapter (yfinance → alpaca/massive)
│   │   ├── universe.py        # §3 tradeability gate
│   │   └── store.py           # SQLite + parquet read/write
│   ├── indicators/
│   │   └── technicals.py      # RSI, MACD, SMA(20/50/200), ATR, volume avg
│   ├── strategy/
│   │   ├── regime.py          # §4 trend + market filter
│   │   ├── entry_pullback.py  # §5 trend-pullback engine (primary)
│   │   ├── entry_breakout.py  # §13 module (deferred)
│   │   ├── entry_meanrev.py   # §13 module (deferred)
│   │   ├── sizing.py          # §7 position-sizing math
│   │   ├── exits.py           # §8 stops, targets, trailing, time stop
│   │   └── portfolio.py       # §6 position/sector/correlation/heat caps
│   ├── risk/
│   │   └── guards.py          # §9 circuit breakers + pre-trade guard
│   ├── engine.py              # orchestrates gate→regime→entry→size→exits→recommendations
│   ├── backtest/
│   │   └── runner.py          # backtesting.py / vectorbt harness + cost model
│   ├── broker/
│   │   ├── mcp_client.py      # Robinhood MCP adapter (read state, quote, execute)
│   │   └── paper.py           # Alpaca paper / simulated fills
│   ├── monitor/
│   │   ├── morning.py         # pre-open run
│   │   └── afternoon.py       # near-close run
│   └── metrics/
│       └── performance.py     # quantstats: expectancy, win rate, payoff, drawdown
├── dashboard/
│   └── app.py                 # Streamlit dashboard
├── scripts/
│   ├── init_db.py             # create SQLite schema
│   ├── run_morning.py         # cron entrypoint
│   └── run_afternoon.py       # cron entrypoint
├── tests/                     # unit tests per rule + integration tests
├── cron/
│   └── crontab.txt            # scheduled jobs with market-calendar guard
├── requirements.txt
└── README.md
```

---

## 3. Data model (SQLite)

Defined once in Phase 0; used everywhere after.

| Table | Key columns | Purpose |
|---|---|---|
| `positions` | ticker, shares, avg_cost, entry_date, initial_stop, current_stop, status | Live + historical holdings |
| `signals` | date, ticker, action, reason_codes, entry, stop, target, score, rank | Every recommendation the engine emits |
| `trades` | trade_id, ticker, side, qty, price, datetime, source(manual/paper/mcp), r_multiple | Every fill, for metrics + tax |
| `journal` | date, run(morning/afternoon), regime_state, breaker_state, notes | Daily system state log |
| `equity_curve` | date, total_value, cash, invested, drawdown_pct | Performance + drawdown tracking |
| `universe_cache` | date, ticker, passed_gate, reason | Daily eligible universe |

Price history lives in parquet (`data/prices/{ticker}.parquet`), columnar and compact for fast indicator computation.

---

## 4. Phase-by-phase plan

### Phase 0 — Foundations & scaffolding  *(Effort: M)*

**Objective.** A working skeleton: repo, environment, config, storage, and a data feed that reliably lands historical bars in parquet — with zero strategy logic yet.

**Build.**
- `config_loader.py` — load and validate `rulebook.yaml` into a typed config object; fail loudly on missing/invalid keys.
- `config/rulebook.yaml` — transcribe the rulebook §12 parameter table verbatim. This becomes the only place strategy numbers live.
- `store.py` + `init_db.py` — create the SQLite schema (§3 above) and parquet read/write helpers.
- `market_data.py` — adapter interface with a yfinance implementation first; method signatures designed so Alpaca/Massive can drop in later without touching callers.
- A small seed universe (e.g. 20–50 liquid names) for early testing.

**Data sources.** yfinance (free).

**Config consumed.** Universe thresholds (`min_price`, `min_avg_dollar_volume`, `min_market_cap`); data paths.

**Exit gate.** Run a script that pulls history for the seed universe, writes parquet, round-trips through SQLite, and reloads config — all green, with a storage round-trip unit test passing. No strategy logic required to pass.

---

### Phase 1 — Indicator + strategy engine (the offline brain)  *(Effort: L)*

**Objective.** The full rulebook expressed as deterministic, unit-tested functions. Given any price history, the engine emits the exact BUY/EXIT/HOLD decisions the rulebook specifies — with no live data and no orders.

**Build (each maps to a rulebook section).**
- `indicators/technicals.py` — RSI(14), MACD(12/26/9), SMA(20/50/200), ATR(14), 20-day volume average via pandas-ta. Unit-test against hand-computed fixtures.
- `strategy/regime.py` — §4 trend gate (price>200SMA, 50>200, rising slope) + market filter (SPX>200SMA).
- `strategy/entry_pullback.py` — §5 confluence (E1 pullback-to-MA, E2 RSI turn, E3 MACD resume, E4 volume) + anti-chase guard + ranking.
- `strategy/sizing.py` — §7 risk-based share count, with the §6 position cap overriding when it binds.
- `strategy/exits.py` — §8 initial stop, +2R scale, trailing, trend-break, time stop, breakeven move.
- `strategy/portfolio.py` — §6 position count, per-name cap, sector cap, correlation guard, cash floor, heat.
- `engine.py` — orchestrates the funnel and produces a structured recommendation list (action, ticker, reason codes, entry/stop/target, shares, position %, risk %, binding cap).

**Data sources.** Parquet history from Phase 0 (offline).

**Config consumed.** Essentially the whole §12 table.

**Testing.** One test file per rule module with hand-checked fixtures (e.g. a synthetic uptrend-pullback series that *must* trigger a buy; a downtrend series that *must not*). Integration test: feed a multi-name history, assert the engine's full recommendation set.

**Exit gate.** Deterministic correctness — for a battery of fixture histories, the engine output matches the rulebook's expected decisions exactly, including sizes and stops. Full per-rule unit coverage green.

---

### Phase 2 — Backtest & validation harness (the first hard gate)  *(Effort: L)*

**Objective.** Measure whether the strategy has a real edge — *before* a dollar is at risk — and provide the tuning surface.

**Build.**
- `backtest/runner.py` — wrap the engine in backtesting.py (simplest) or vectorbt (faster sweeps); model **commissions and slippage** realistically (manual fills differ from idealized backtests).
- `metrics/performance.py` — quantstats tear sheet: expectancy (avg R), win rate, payoff ratio, max drawdown, Sharpe/Sortino, % stopped at initial stop.
- Assemble a multi-year historical dataset for a realistic universe.
- Parameter tuning **in config only**, with an out-of-sample / walk-forward split to guard against overfitting.

**Data sources.** Multi-year historical bars (yfinance to start; upgrade if data quality limits the backtest).

**Config consumed.** Full §12 table (this is the tuning phase).

**Exit gate (GO/NO-GO #1).** A documented edge estimate against the rulebook's go-live bar: **positive expectancy after modeled costs across ≥50 backtested trades**, with the out-of-sample result not collapsing versus in-sample. If the edge isn't there, iterate on parameters or reconsider the engine — do **not** proceed to live data spending real attention on a system with no demonstrated edge.

---

### Phase 3 — Live monitoring dashboard (read-only decision-support)  *(Effort: M)*

**Objective.** The twice-daily decision-support system originally described: live account state in, ranked recommendations out, you place every order by hand.

**Build.**
- `broker/mcp_client.py` — connect the Robinhood Trading MCP **read-only**; pull positions/balances/orders into SQLite; expose a live-quote call for order-time price checks. Connect via Claude Code: `claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading` (authorize in the Robinhood app; never share credentials).
- `monitor/morning.py` + `monitor/afternoon.py` — the two scheduled runs (morning: gaps, stops, fresh candidates; afternoon: confirm signals on the forming close, update trailing stops, write journal).
- `dashboard/app.py` — Streamlit panels: current holdings, today's ranked recommendations (with reason codes, entry/stop/size, position % and risk %), risk-cap and circuit-breaker status, and the performance/equity panel.
- `cron/crontab.txt` — schedule both runs on weekdays with a `pandas_market_calendars` guard so jobs no-op on closed days/half-days.

**Data sources.** Market-data API (indicators) + Robinhood MCP (account state, live quote).

**Config consumed.** Schedule times, cadence, all strategy params (read-only use).

**Exit gate.** A live dashboard that, twice daily on schedule, reads your *actual* account and surfaces correct recommendations, with breaker/cap status visible. You execute manually. **Milestone: decision-support is live.**

---

### Phase 4 — Paper trading (structured live experiment, second hard gate)  *(Effort: M)*

**Objective.** Validate the system *live* (not just historically), catch overfitting and real slippage, and accumulate the trade sample that decides go/no-go for real money.

**Build.**
- `broker/paper.py` — Alpaca paper account (or simulated fills) running the full loop end-to-end.
- Journaling of every signal and fill into `trades`/`signals`/`journal`.
- `metrics/performance.py` extended to compare **paper expectancy vs. backtest expectancy** (a large gap signals overfit or slippage you under-modeled).

**Protocol.** Run for a fixed structured window until **≥50 live paper trades**. Change nothing mid-experiment except logged, deliberate adjustments.

**Data sources.** Market-data API + paper broker.

**Exit gate (GO/NO-GO #2).** **≥50 paper trades with positive expectancy after costs**, and paper metrics roughly consistent with the backtest. Pass → real money is authorized. Fail → back to Phase 2 tuning, not forward.

---

### Phase 5 — Gated live execution (real money, preview/approval)  *(Effort: M)*

**Objective.** Deploy the $5K with two independent safety layers in front of every order: a deterministic guard and a human approval.

**Build.**
- `risk/guards.py` — the **pre-trade guard**: before any order reaches the MCP, deterministically re-check every cap (position size §6, sector §6, total heat §9, drawdown breaker §9). Block any non-compliant order regardless of what the signal said. This is the most important code in the system.
- `broker/mcp_client.py` execute path — place orders into the **dedicated agentic account only**, in **preview/approval** mode (you confirm in the app before it fills).
- Order-lifecycle tracking + daily reconciliation (MCP-reported fills vs. local `trades`).

**Funding & security.** Fund the ring-fenced agentic account deliberately with the experiment capital; everything else stays read-only-by-design (connecting grants read across all accounts, trade only in the agentic one).

**Data sources.** Market-data API + Robinhood MCP (state, quote, execute).

**Config consumed.** Execution mode flag (`preview`), all caps/breakers.

**Exit gate.** Real orders execute correctly with **both** the guard and human approval in the path; reconciliation matches; the system runs clean over a defined live window without a guard breach or reconciliation mismatch.

---

### Phase 6 — Optional autonomy + hardening  *(Effort: M)*

**Objective.** Hands-off operation within the caps (only if you want it), plus production polish. Entirely optional — stopping at Phase 5 semi-auto is a legitimate end state.

**Build.**
- Flip execution mode `preview → autonomous` within the agentic account, with the pre-trade guard and circuit breakers **still fully in force** and the one-tap MCP kill switch available.
- Hardening: error alerting (failed runs, data gaps), data-source failover (yfinance → paid feed), SQLite backups, health checks, and observability/logging.

**Exit gate.** Runs hands-off within its caps over a defined window with no guard breach — *or* a deliberate decision to remain semi-automatic. Only after this do you consider **scaling toward $50K**, which is itself gated on multi-month validated performance.

---

## 5. Sequencing & dependencies

```
Phase 0 ──► Phase 1 ──► Phase 2 ──[GATE 1: edge]──► Phase 3 ──► Phase 4 ──[GATE 2: 50 trades]──► Phase 5 ──► Phase 6
 (free)      (free)      (free)                      (live data) (paper $)   (real $: $5K)        (autonomy)
```

- **No real money before Gate 2.** Gates 1 and 2 are hard stops, not formalities.
- Phases 0–2 require no external spend; the paid data feed is pulled forward only if yfinance reliability limits the backtest (Phase 2) or live monitoring (Phase 3).
- Alternate engines (breakout, mean-reversion) and the intraday option are **deferred v2 work**, evaluated against the validated daily-bar baseline — not built into v1.

---

## 6. Tech stack by layer

| Layer | Tools |
|---|---|
| Language / runtime | Python, local server, Claude Code, cron |
| Data | yfinance (free) → Alpaca/Massive (paid); parquet via pyarrow; SQLite |
| Indicators | pandas-ta (pandas backbone) |
| Backtest | backtesting.py or vectorbt |
| Metrics | quantstats |
| Dashboard | Streamlit |
| Scheduling | cron + pandas_market_calendars |
| Broker | Robinhood Trading MCP (state/quote/execute); Alpaca paper (validation) |

*(Stack rationale and 2026 pricing/maintenance status are in the separate tooling research report.)*

---

## 7. Cross-cutting concerns

- **Config-driven:** no strategy number is ever hardcoded; tuning happens in `rulebook.yaml`, then re-test, then nothing else changes.
- **Testing:** every rule has a unit test; the engine has integration tests; reconciliation is tested before live execution.
- **Logging/journal:** every run and every fill is recorded for metrics, debugging, and tax (short-term gains + wash-sale tracking).
- **Secrets:** API keys in gitignored `secrets.env`; MCP authorized via the Robinhood app, never by passing credentials to code.
- **Safety precedence:** if a signal and a cap conflict, the cap wins — enforced in code by the pre-trade guard, not by discipline.

---

## 8. Explicitly out of scope for v1

ETFs, funds, options, and crypto; intraday/day-trading cadence; autonomous execution before validation; any non-US equities. Each is a deliberate later decision, gated behind a working, validated daily-bar baseline.

---

## 9. References

- Strategy Rulebook v1 (the *what*; this plan implements it section-by-section).
- Tooling research report (2025–2026 data APIs, libraries, dashboards, brokerage/MCP reality, pricing).
- Robinhood Trading MCP endpoint and Claude Code connection command per Robinhood's agentic-trading documentation; validation-gate rationale per the active-trader base-rate evidence cited in the rulebook (§1, §15).

---

*End of Implementation Plan v1. Build in order; never skip a gate.*
