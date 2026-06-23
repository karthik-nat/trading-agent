# trading-agent

A personal, self-hosted, rules-based **swing-trading decision-support system** for
long-only US individual equities. The strategy is deterministic Python that reads
every parameter from `config/rulebook.yaml`; the system **recommends**, a human
reviews and places every order. See `docs/Strategy_Rulebook_v1.md` (the *what*) and
`docs/Implementation_Plan_v1.md` (the *how*, phases 0–6).

> No real capital moves before the validation gates (Plan §4). Phases 0–4 risk no
> money. See `CLAUDE.md` for the non-negotiable build rules.

## Status

**Phase 0 — Foundations & scaffolding (complete).** Plumbing only: config loading
+ validation, SQLite/parquet storage, a swappable market-data adapter, a seed
universe, and tests.

**Phase 1 — Indicator + strategy engine (complete).** The offline "brain":
hand-rolled indicators (§5/§8), regime gate (§4), trend-pullback entry (§5),
risk-based sizing (§7), exits (§8), portfolio caps (§6 + §9 heat), and the
`engine.py` funnel (§10) that emits ranked BUY/TRIM/EXIT/HOLD recommendations.
Deterministic and offline — **no backtest, live data, or orders yet** (Phases 2+).
The §3 tradeability gate (`data/universe.py`, needs market-cap/earnings data) and
all later-phase modules remain stubs.

## Layout (Phase 0 implemented; later modules are stubs)

```
config/   rulebook.yaml (strategy params), universe.yaml (seed list), secrets.env (gitignored)
data/     prices/*.parquet (history), trading.db (SQLite state)   [gitignored]
src/      config_loader.py, paths.py
          data/        market_data.py, store.py        (Phase 0)
          indicators/  technicals.py                   (Phase 1)
          strategy/    regime, entry_pullback, sizing, exits, portfolio   (Phase 1)
          engine.py    the §10 funnel                  (Phase 1)
          risk/ broker/ monitor/ metrics/ backtest/    (later-phase stubs)
scripts/  init_db.py, fetch_history.py
tests/    9 files, 103 tests (config, store, technicals, regime, entry, sizing,
          exits, portfolio, engine)
```

## Setup

This project runs in its **own isolated conda env** (kept separate from any other
environment, so we can install freely here):

```bash
conda create -n trading-agent python=3.12
conda activate trading-agent
pip install -r requirements.txt          # pandas, numpy, pyarrow, PyYAML, yfinance, pandas-ta, pytest
```

## Phase 0 usage

```bash
python -m scripts.init_db                 # validate config + create SQLite schema
python -m scripts.fetch_history           # pull seed-universe history -> data/prices/*.parquet
python -m pytest -q                        # run the test suite
```

## Design notes

- **Config is the single source of truth.** No strategy number is hardcoded; the
  loader fails loudly on any missing/mistyped/out-of-range/unknown key.
- **Market data is an adapter.** Callers depend only on `MarketDataProvider` and
  the canonical OHLCV schema, so Alpaca/Massive can replace yfinance later without
  touching callers. The broker MCP is **never** a source of historical bars.
- **Operational paths** (`data/`, db file) live in `src/paths.py`, deliberately
  kept out of `rulebook.yaml` (which holds strategy parameters only).
