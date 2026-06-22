# CLAUDE.md — Build brief for this repository

You are building a **personal, self-hosted AI trading-analyst / decision-support system** for an individual US-equities swing-trading experiment. Read this file fully before doing anything. Two authoritative documents in `/docs` define the system:

- `docs/Strategy_Rulebook_v1.md` — the **what** (the exact trading rules).
- `docs/Implementation_Plan_v1.md` — the **how** and **in-what-order** (phases 0–6, each with an exit gate).

`config/rulebook.yaml` is the machine-readable version of the rulebook's parameter table and is the **single source of truth for every strategy number**.

---

## Non-negotiable working rules

1. **Build phase-by-phase. One phase per task.** Do not build ahead. When asked to do Phase N, build only Phase N. Do not scaffold later phases "to save time" — the phase boundaries are a safety design, not bureaucracy.

2. **Each phase's exit gate is the acceptance criteria.** A phase is done only when its gate (defined in the Implementation Plan §4) demonstrably passes. State explicitly whether the gate is met before considering the phase complete.

3. **Rules live in config, never in code.** Read every strategy threshold from `config/rulebook.yaml`. Never hardcode an indicator period, cap, or threshold. If a value you need isn't in the config, stop and ask — don't invent it.

4. **No real money before the gates.** Phases 0–4 use no real capital. Live execution code (Phase 5) and autonomous execution (Phase 6) are **off-limits until those phases are explicitly authorized** by the user. Never write code that places real orders until told we are in Phase 5. Never enable autonomous mode before Phase 6 and before the 50-trade positive-expectancy gate has passed.

5. **The Robinhood MCP is a thin broker adapter only.** Use it for (a) reading account state, (b) a live quote at order time, and (c) placing the exact order the engine computed. It is **never** the strategy brain and **never** the source of historical bars for indicators (those come from the market-data API). Do not pass strategy logic to an LLM agent to interpret.

6. **The pre-trade guard is critical safety code.** In Phase 5, before any order reaches the broker, deterministically re-check every cap (position size, sector, total heat, drawdown breaker) and block non-compliant orders regardless of the signal. Treat this module with extra care and extra tests.

7. **Test every rule.** Each strategy rule gets a unit test with hand-checked fixtures that encode intent (a series that *must* trigger; one that *must not*). The engine gets integration tests. Don't mark Phase 1 done without them.

8. **Secrets never touch git or code.** API keys live in a gitignored `config/secrets.env`. The MCP is authorized via the Robinhood app — never pass account credentials to code.

9. **Ask before deviating.** If the plan is ambiguous, a dependency seems wrong, or you'd need to add a library not in the stack, stop and ask rather than guessing. Small assumptions compound.

10. **Safety precedence.** If a trading signal and a risk cap ever conflict, the cap wins — in code, via the guard, not by discipline.

---

## Tech stack (do not substitute without asking)

Python · SQLite (state) · parquet/pyarrow (price history) · pandas + pandas-ta (indicators) · backtesting.py or vectorbt (backtest) · quantstats (metrics) · Streamlit (dashboard) · cron + pandas_market_calendars (scheduling) · Robinhood Trading MCP (broker) · Alpaca paper (validation). Market data: yfinance (free) now, designed so Alpaca/Massive drop in later.

## Repo conventions

Follow the structure in Implementation Plan §2. Strategy logic in `src/strategy/`, indicators in `src/indicators/`, risk in `src/risk/`, broker in `src/broker/`, orchestration in `src/engine.py`. Keep modules small and named after the rulebook section they implement.

## Definition of done (every phase)

Code + tests + the phase's exit gate demonstrably passing + a one-paragraph summary of what was built and the gate result. Commit at each passing gate.

## Out of scope for v1 (do not build)

ETFs, funds, options, crypto; intraday cadence; non-US equities; autonomous execution before validation. These are deliberate later decisions.

---

## Current status

**Phase: 0 (not started).** Build only what the current phase prompt asks for.
