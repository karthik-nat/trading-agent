#!/usr/bin/env python3
"""Create the SQLite schema (Implementation Plan §3). Idempotent.

Usage:
    python -m scripts.init_db
    python scripts/init_db.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config_loader import load_config  # noqa: E402
from src.data.store import init_db, list_tables  # noqa: E402
from src.paths import DB_PATH  # noqa: E402


def main() -> int:
    # Validate config first — fail loudly before touching storage.
    cfg = load_config()
    print(f"config OK   : rulebook v{cfg.meta.version}, engine={cfg.meta.engine}")

    path = init_db(DB_PATH)
    tables = list_tables(DB_PATH)
    print(f"database    : {path}")
    print(f"tables ({len(tables)}): {', '.join(tables)}")
    print("init_db: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
