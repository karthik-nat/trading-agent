#!/usr/bin/env python3
"""Pull daily history for the seed universe and write it to parquet (Phase 0).

This is data plumbing, not strategy: it exercises the market-data adapter and
the parquet store end to end. Indicators/strategy come in Phase 1.

Usage:
    python -m scripts.fetch_history                 # uses min_history_days from config
    python scripts/fetch_history.py --lookback 800  # explicit lookback (trading days)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config_loader import load_config, load_seed_universe  # noqa: E402
from src.data.market_data import MarketDataError, get_provider  # noqa: E402
from src.data.store import read_prices, write_prices  # noqa: E402
from src.paths import PRICES_DIR, ensure_data_dirs  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch seed-universe history to parquet")
    parser.add_argument(
        "--lookback", type=int, default=None,
        help="trading-day lookback (default: rulebook universe.min_history_days)",
    )
    parser.add_argument(
        "--include-index", action="store_true",
        help="also fetch the market-filter index (indicator only)",
    )
    args = parser.parse_args()

    cfg = load_config()
    uni = load_seed_universe()
    lookback = args.lookback or cfg.universe.min_history_days
    provider = get_provider(cfg.system.data_provider)
    ensure_data_dirs()

    symbols = [n.ticker for n in uni.seed_universe]
    if args.include_index:
        symbols.append(uni.market_index)

    print(f"provider={provider.name}  lookback={lookback}d  symbols={len(symbols)}")
    ok, failed = 0, []
    for sym in symbols:
        try:
            df = provider.get_daily_history(sym, lookback_days=lookback)
            path = write_prices(sym, df, PRICES_DIR)
            rt = read_prices(sym, PRICES_DIR)  # confirm the round-trip on disk
            span = f"{df['date'].iloc[0].date()}..{df['date'].iloc[-1].date()}"
            print(f"  {sym:6s} {len(df):4d} bars  {span}  -> {path.name}  (reread {len(rt)})")
            ok += 1
        except MarketDataError as exc:
            print(f"  {sym:6s} FAILED: {exc}")
            failed.append(sym)

    print(f"\nfetched {ok}/{len(symbols)} symbols; failed: {failed or 'none'}")
    return 0 if ok > 0 and not failed else (0 if ok > 0 else 1)


if __name__ == "__main__":
    raise SystemExit(main())
