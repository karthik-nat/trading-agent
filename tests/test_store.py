"""Storage round-trip tests: parquet history + SQLite state + config reload.

Mirrors the Phase 0 exit gate: write history to parquet, round-trip through
SQLite, reload config — all without network (the live pull is exercised by
scripts/fetch_history.py).
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.config_loader import load_config
from src.data.market_data import (
    OHLCV_COLUMNS,
    MarketDataError,
    YFinanceProvider,
    empty_ohlcv,
)
from src.data.store import (
    TABLES,
    connect,
    has_prices,
    init_db,
    list_tables,
    read_prices,
    write_prices,
)


def _synthetic_history(n: int = 30) -> pd.DataFrame:
    """A canonical OHLCV frame with realistic-ish values (no network)."""
    dates = pd.bdate_range("2024-01-02", periods=n)
    base = [100.0 + i for i in range(n)]
    df = pd.DataFrame(
        {
            "date": dates,
            "open": [b - 0.5 for b in base],
            "high": [b + 1.0 for b in base],
            "low": [b - 1.0 for b in base],
            "close": base,
            "adj_close": base,
            "volume": [1_000_000 + 1000 * i for i in range(n)],
        }
    )
    df["volume"] = df["volume"].astype("float64")
    return df[list(OHLCV_COLUMNS)]


# --------------------------------------------------------------------------- #
# SQLite schema (Implementation Plan §3)
# --------------------------------------------------------------------------- #
def test_init_db_creates_all_tables(tmp_path):
    db = tmp_path / "trading.db"
    init_db(db)
    tables = list_tables(db)
    assert set(tables) == set(TABLES)
    assert len(TABLES) == 6


def test_init_db_is_idempotent(tmp_path):
    db = tmp_path / "trading.db"
    init_db(db)
    init_db(db)  # second call must not raise
    assert set(list_tables(db)) == set(TABLES)


def test_sqlite_state_roundtrip(tmp_path):
    db = tmp_path / "trading.db"
    init_db(db)
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO positions "
            "(ticker, shares, avg_cost, entry_date, initial_stop, current_stop, status) "
            "VALUES (?,?,?,?,?,?,?)",
            ("AAPL", 10, 100.0, "2024-06-01", 95.0, 95.0, "open"),
        )
        conn.execute(
            "INSERT INTO trades (ticker, side, qty, price, datetime, source, r_multiple) "
            "VALUES (?,?,?,?,?,?,?)",
            ("AAPL", "buy", 10, 100.0, "2024-06-01T13:30:00", "manual", None),
        )
    with connect(db) as conn:
        pos = conn.execute("SELECT * FROM positions").fetchone()
        trade = conn.execute("SELECT * FROM trades").fetchone()
    assert pos["ticker"] == "AAPL"
    assert pos["shares"] == 10
    assert pos["status"] == "open"
    assert trade["side"] == "buy"
    assert trade["source"] == "manual"


def test_sqlite_check_constraints_enforced(tmp_path):
    db = tmp_path / "trading.db"
    init_db(db)
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        with connect(db) as conn:
            conn.execute(
                "INSERT INTO positions "
                "(ticker, shares, avg_cost, entry_date, status) VALUES (?,?,?,?,?)",
                ("AAPL", 10, 100.0, "2024-06-01", "bogus_status"),
            )


# --------------------------------------------------------------------------- #
# Parquet price history
# --------------------------------------------------------------------------- #
def test_parquet_roundtrip_preserves_frame(tmp_path):
    df = _synthetic_history(40)
    write_prices("AAPL", df, tmp_path)
    assert has_prices("AAPL", tmp_path)
    back = read_prices("AAPL", tmp_path)
    pd.testing.assert_frame_equal(df, back)


def test_parquet_filename_is_uppercased(tmp_path):
    write_prices("aapl", _synthetic_history(5), tmp_path)
    assert (tmp_path / "AAPL.parquet").exists()


def test_read_missing_returns_empty_canonical_frame(tmp_path):
    back = read_prices("NOPE", tmp_path)
    assert back.empty
    assert list(back.columns) == list(OHLCV_COLUMNS)


def test_write_rejects_wrong_columns(tmp_path):
    bad = pd.DataFrame({"date": [], "close": []})
    with pytest.raises(ValueError, match="expected columns"):
        write_prices("BAD", bad, tmp_path)


def test_empty_ohlcv_has_canonical_schema():
    e = empty_ohlcv()
    assert list(e.columns) == list(OHLCV_COLUMNS)


# --------------------------------------------------------------------------- #
# market_data normalization (offline: no network)
# --------------------------------------------------------------------------- #
def test_yfinance_normalize_maps_to_canonical_schema():
    # emulate a raw yfinance frame (Date index, capitalized cols, tz-aware)
    idx = pd.DatetimeIndex(
        pd.to_datetime(["2024-01-02", "2024-01-03"]).tz_localize("America/New_York"),
        name="Date",
    )
    raw = pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.0],
            "Close": [101.0, 102.0],
            "Adj Close": [101.0, 102.0],
            "Volume": [1_000_000, 1_100_000],
            "Dividends": [0.0, 0.0],
            "Stock Splits": [0.0, 0.0],
        },
        index=idx,
    )
    out = YFinanceProvider._normalize(raw)
    assert list(out.columns) == list(OHLCV_COLUMNS)
    assert out["date"].dt.tz is None  # tz stripped
    assert out["date"].is_monotonic_increasing
    assert out["close"].tolist() == [101.0, 102.0]


def test_provider_validation_rejects_duplicate_dates():
    from src.data.market_data import _validate_frame

    df = _synthetic_history(3)
    df.loc[1, "date"] = df.loc[0, "date"]
    with pytest.raises(MarketDataError, match="duplicate dates"):
        _validate_frame("X", df)


# --------------------------------------------------------------------------- #
# Full gate mirror: parquet + sqlite + config reload together
# --------------------------------------------------------------------------- #
def test_phase0_roundtrip_all_layers(tmp_path):
    # 1) config reloads
    cfg = load_config()
    assert cfg.system.data_provider == "yfinance"
    # 2) parquet history round-trips
    write_prices("MSFT", _synthetic_history(20), tmp_path)
    assert len(read_prices("MSFT", tmp_path)) == 20
    # 3) sqlite schema + state round-trips
    db = tmp_path / "trading.db"
    init_db(db)
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO equity_curve (date, total_value, cash, invested, drawdown_pct) "
            "VALUES (?,?,?,?,?)",
            ("2024-06-01", 5000.0, 5000.0, 0.0, 0.0),
        )
    with connect(db) as conn:
        row = conn.execute("SELECT total_value FROM equity_curve").fetchone()
    assert row["total_value"] == 5000.0
