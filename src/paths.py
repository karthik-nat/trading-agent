"""Filesystem paths for the project.

These are OPERATIONAL paths, deliberately kept OUT of ``config/rulebook.yaml``
(which holds strategy numbers only). Everything is derived from the project
root so the layout is portable.
"""
from __future__ import annotations

from pathlib import Path

# src/paths.py -> src/ -> project root
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

CONFIG_DIR: Path = PROJECT_ROOT / "config"
RULEBOOK_PATH: Path = CONFIG_DIR / "rulebook.yaml"
UNIVERSE_PATH: Path = CONFIG_DIR / "universe.yaml"
SECRETS_PATH: Path = CONFIG_DIR / "secrets.env"  # gitignored; not required in Phase 0

DATA_DIR: Path = PROJECT_ROOT / "data"
PRICES_DIR: Path = DATA_DIR / "prices"
DB_PATH: Path = DATA_DIR / "trading.db"


def ensure_data_dirs() -> None:
    """Create the data directories if they do not yet exist."""
    PRICES_DIR.mkdir(parents=True, exist_ok=True)
