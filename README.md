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
universe, and tests. **No strategy logic, indicators, or orders yet.**

## Layout (Phase 0 implemented; later modules are stubs)

```
config/   rulebook.yaml (strategy params), universe.yaml (seed list), secrets.env (gitignored)
data/     prices/*.parquet (history), trading.db (SQLite state)   [gitignored]
src/      config_loader.py, paths.py, data/ (market_data, store), + stubs for strategy/risk/broker/...
scripts/  init_db.py, fetch_history.py
tests/    test_config_loader.py, test_store.py
```

## Setup

```bash
pip install -r requirements.txt          # pandas, pyarrow, PyYAML, yfinance, pytest
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
