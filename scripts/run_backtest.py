#!/usr/bin/env python3
"""Run the Phase 2 backtest and report the Gate-1 edge assessment.

Reads parquet history for the seed universe, runs the event-driven backtester
over the full period plus an in-sample / out-of-sample split, and reports the
§11 metrics against the GO/NO-GO #1 bar (>= go_live_min_trades trades, positive
expectancy after modeled costs, OOS not collapsing vs IS).

Usage:
    python -m scripts.run_backtest                  # full seed universe
    python -m scripts.run_backtest --max-names 10   # quicker subset
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from src.backtest.runner import run_backtest  # noqa: E402
from src.config_loader import load_config, load_seed_universe  # noqa: E402
from src.data.store import has_prices, read_prices  # noqa: E402
from src.metrics.performance import compute_metrics, gate_check, summary  # noqa: E402


def _load_prices(universe, max_names: int | None):
    prices, sectors = {}, {}
    for sn in universe.seed_universe:
        if max_names and len(prices) >= max_names:
            break
        if has_prices(sn.ticker):
            df = read_prices(sn.ticker)
            if len(df) > 0:
                prices[sn.ticker] = df
                sectors[sn.ticker] = sn.sector
    return prices, sectors


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--equity", type=float, default=100_000.0)
    ap.add_argument("--max-names", type=int, default=None)
    ap.add_argument("--log-every", type=int, default=250)
    args = ap.parse_args()

    cfg = load_config()
    uni = load_seed_universe()
    prices, sectors = _load_prices(uni, args.max_names)
    index_df = read_prices(uni.market_index)
    dates = pd.to_datetime(index_df["date"]).sort_values().reset_index(drop=True)
    split = dates.iloc[int(0.7 * len(dates))]

    print(f"universe : {len(prices)} names | index {uni.market_index} | "
          f"{dates.iloc[0].date()}..{dates.iloc[-1].date()} ({len(dates)} bars)")
    print(f"IS/OOS split at {split.date()}  (initial equity ${args.equity:,.0f})\n")

    def run(label, **kw):
        print(f"--- running {label} ---", flush=True)
        res = run_backtest(cfg, prices, index_df, initial_equity=args.equity,
                           sectors=sectors, log_every=args.log_every, **kw)
        m = compute_metrics(res, cfg)
        print(summary(m, title=label))
        print()
        return m

    full = run("FULL PERIOD")
    is_m = run("IN-SAMPLE (first 70%)", end=split)
    oos_m = run("OUT-OF-SAMPLE (last 30%)", start=split)

    g = gate_check(full, cfg)
    print("=== GATE 1 (GO/NO-GO #1) ===")
    for r in g.reasons:
        print(f"  - {r}")
    collapse = (is_m.expectancy_r > 0) and (oos_m.expectancy_r <= 0)
    print(f"  - IS expectancy {is_m.expectancy_r:+.3f}R ({is_m.total_trades} trades) | "
          f"OOS expectancy {oos_m.expectancy_r:+.3f}R ({oos_m.total_trades} trades)")
    verdict = "GO" if (g.passed and not collapse) else "NO-GO"
    print(f"\n  VERDICT: {verdict}")
    if collapse:
        print("  (out-of-sample expectancy collapsed vs in-sample)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
