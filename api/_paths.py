"""Shared filesystem paths for the api/ layer.

Single source of truth for DATA_DIR / LOGS_DIR and the on-disk artifacts
(signals.log) that multiple api/* modules write to. Eliminates the
duplication that lived in api/positions.py and api/signals.py post-PR5
(flagged by the final review of the api+db refactor, 2026-04-27).

Domain-specific paths (positions_summary.json, symbols_status.json,
signals_history.csv) stay in the modules that own them.
"""
from __future__ import annotations

import os

_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATA_DIR = os.path.join(_SCRIPT_DIR, "data")
LOGS_DIR = os.path.join(_SCRIPT_DIR, "logs")
SIGNALS_LOG_FILE = os.path.join(LOGS_DIR, "signals.log")


def _ensure_dirs() -> None:
    """Create DATA_DIR and LOGS_DIR if they don't exist."""
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(LOGS_DIR, exist_ok=True)
