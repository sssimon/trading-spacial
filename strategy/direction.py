"""Per-direction parameter resolution for scan() (extracted from btc_scanner.py per #225).

resolve_direction_params: read symbol_overrides config and return per-direction
ATR multipliers, or None if the direction is disabled for that symbol.
"""
from __future__ import annotations

from strategy.constants import (
    ATR_SL_MULT_DEFAULT, ATR_TP_MULT_DEFAULT, ATR_BE_MULT_DEFAULT,
)

# Module-level aliases (preserved from btc_scanner.py for backward compat).
ATR_SL_MULT = ATR_SL_MULT_DEFAULT
ATR_TP_MULT = ATR_TP_MULT_DEFAULT
ATR_BE_MULT = ATR_BE_MULT_DEFAULT


def resolve_direction_params(
    overrides: dict | None,
    symbol: str,
    direction: str,
) -> dict | None:
    """Resolve {atr_sl_mult, atr_tp_mult, atr_be_mult} for (symbol, direction).

    Returns None if the direction is disabled for that symbol (via `"short": null`).
    Precedence: direction block (long/short) > flat dict > global defaults.
    Case insensitive on direction.

    Spec: docs/superpowers/specs/es/2026-04-20-per-symbol-regime-design.md §6
    """
    defaults = {
        "atr_sl_mult": ATR_SL_MULT,
        "atr_tp_mult": ATR_TP_MULT,
        "atr_be_mult": ATR_BE_MULT,
    }

    if direction is None:
        return defaults

    if not isinstance(overrides, dict):
        return defaults

    entry = overrides.get(symbol, {})
    if not isinstance(entry, dict):
        return defaults

    sentinel = object()
    dir_key = direction.lower()
    dir_block = entry.get(dir_key, sentinel)

    if dir_block is None:
        return None  # direction disabled

    if isinstance(dir_block, dict):
        return {
            "atr_sl_mult": dir_block.get("atr_sl_mult",
                              entry.get("atr_sl_mult", defaults["atr_sl_mult"])),
            "atr_tp_mult": dir_block.get("atr_tp_mult",
                              entry.get("atr_tp_mult", defaults["atr_tp_mult"])),
            "atr_be_mult": dir_block.get("atr_be_mult",
                              entry.get("atr_be_mult", defaults["atr_be_mult"])),
        }

    return {
        "atr_sl_mult": entry.get("atr_sl_mult", defaults["atr_sl_mult"]),
        "atr_tp_mult": entry.get("atr_tp_mult", defaults["atr_tp_mult"]),
        "atr_be_mult": entry.get("atr_be_mult", defaults["atr_be_mult"]),
    }


def metrics_inc_direction_disabled(symbol: str, direction: str) -> None:
    """Increment the direction_disabled_skips_total metric (no-op on failure)."""
    try:
        from data import metrics
        metrics.inc("direction_disabled_skips_total",
                    labels={"symbol": symbol, "direction": direction})
    except Exception:
        pass  # metrics optional — don't crash scan on metric failure
