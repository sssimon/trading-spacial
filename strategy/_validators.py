"""Shared config-value validators with throttled warnings.

Both the live `api.positions.check_position_stops` path, the backtest's
`simulate_strategy` resolver, and the production `btc_scanner.scan` path
consume per-symbol `time_limit_hours` / `max_participation_rate` /
`cooldown_hours` and the process-wide `scan_interval_sec` from cfg.
Validation lives here so the same rules apply across paths and a single
throttle prevents log spam from a long-running scanner that re-reads cfg
every tick.

`_validator_warned` is a module-level set keyed by `(caller, symbol, error_kind)`.
"""
from __future__ import annotations

import logging
import math


_validator_warned: set[tuple[str, str, str]] = set()


def validated_time_limit_hours(
    value, symbol: str, caller: str, logger: logging.Logger
) -> float | None:
    """Return value as float if valid positive finite number, else None.

    Rejects None passthrough (caller decides the no-config default), bool
    (subclasses int but is never a valid hour count), non-numeric types,
    NaN, Inf, and zero-or-negative values. Each rejection emits one warning
    per `(caller, symbol, error_kind)` per process lifetime.
    """
    if value is None:
        return None

    error_kind: str | None = None
    msg: str | None = None

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        error_kind = f"time_limit:type:{type(value).__name__}"
        msg = (
            f"{caller}: time_limit_hours for {symbol} has wrong type "
            f"({type(value).__name__}, value={value!r}) — ignoring"
        )
    elif not math.isfinite(value):
        error_kind = "time_limit:non-finite"
        msg = (
            f"{caller}: time_limit_hours for {symbol} must be finite "
            f"(got {value!r}) — ignoring"
        )
    elif value <= 0:
        error_kind = "time_limit:non-positive"
        msg = (
            f"{caller}: time_limit_hours for {symbol} must be > 0 "
            f"(got {value}) — ignoring"
        )

    if error_kind is not None:
        warn_key = (caller, symbol, error_kind)
        if warn_key not in _validator_warned:
            logger.warning(msg)
            _validator_warned.add(warn_key)
        return None

    return float(value)


def validated_max_participation_rate(
    value, symbol: str, caller: str, logger: logging.Logger
) -> float | None:
    """Return value as float if valid in (0, 1.0], else None.

    Same throttle pattern as `validated_time_limit_hours`. Rejects None
    passthrough (caller decides the no-config default), bool (subclasses int
    but is never a valid rate), non-numeric, NaN, Inf, ≤0, and >1.0
    (sanity: 100% participation = whole bar volume; values >1 are nonsense
    for retail).
    """
    if value is None:
        return None

    error_kind: str | None = None
    msg: str | None = None

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        error_kind = f"max_pov:type:{type(value).__name__}"
        msg = (
            f"{caller}: max_participation_rate for {symbol} has wrong type "
            f"({type(value).__name__}, value={value!r}) — ignoring"
        )
    elif not math.isfinite(value):
        error_kind = "max_pov:non-finite"
        msg = (
            f"{caller}: max_participation_rate for {symbol} must be finite "
            f"(got {value!r}) — ignoring"
        )
    elif value <= 0:
        error_kind = "max_pov:non-positive"
        msg = (
            f"{caller}: max_participation_rate for {symbol} must be > 0 "
            f"(got {value}) — ignoring"
        )
    elif value > 1.0:
        error_kind = "max_pov:above-one"
        msg = (
            f"{caller}: max_participation_rate for {symbol} must be ≤ 1.0 "
            f"(got {value}) — ignoring"
        )

    if error_kind is not None:
        warn_key = (caller, symbol, error_kind)
        if warn_key not in _validator_warned:
            logger.warning(msg)
            _validator_warned.add(warn_key)
        return None

    return float(value)


def validated_cooldown_hours(
    value,
    *,
    caller: str,
    symbol: str,
    logger: logging.Logger,
    default: float = 6.0,
) -> float:
    """Return value as float if valid in (0, 168], else `default` (boundary > 168 = sanity reject)."""
    if value is None:
        return float(default)

    error_kind: str | None = None
    msg: str | None = None

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        error_kind = f"cooldown:type:{type(value).__name__}"
        msg = (
            f"{caller}: cooldown_hours for {symbol} has wrong type "
            f"({type(value).__name__}, value={value!r}) — falling back to {default}"
        )
    elif not math.isfinite(value):
        error_kind = "cooldown:non-finite"
        msg = (
            f"{caller}: cooldown_hours for {symbol} must be finite "
            f"(got {value!r}) — falling back to {default}"
        )
    elif value <= 0:
        error_kind = "cooldown:non-positive"
        msg = (
            f"{caller}: cooldown_hours for {symbol} must be > 0 "
            f"(got {value}) — falling back to {default}"
        )
    elif value > 168:
        error_kind = "cooldown:above-168"
        msg = (
            f"{caller}: cooldown_hours for {symbol} must be ≤ 168 "
            f"(got {value}) — falling back to {default}"
        )

    if error_kind is not None:
        warn_key = (caller, symbol, error_kind)
        if warn_key not in _validator_warned:
            logger.warning(msg)
            _validator_warned.add(warn_key)
        return float(default)

    return float(value)


def validated_scan_interval_sec(
    cfg: dict, caller: str, logger: logging.Logger, *, default: int = 300
) -> int:
    """Return scan_interval_sec from cfg, validated. Falls back to `default`
    on any non-finite, non-positive, non-numeric, or bool value.

    Throttle key is `(caller, "<scan_interval>", error_kind)` — symbol slot
    holds a literal so the same caller doesn't re-warn on every tick.
    """
    value = cfg.get("scan_interval_sec", default)

    error_kind: str | None = None
    msg: str | None = None

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        error_kind = f"scan_interval:type:{type(value).__name__}"
        msg = (
            f"{caller}: scan_interval_sec has wrong type "
            f"({type(value).__name__}, value={value!r}) — falling back to {default}"
        )
    elif not math.isfinite(value):
        error_kind = "scan_interval:non-finite"
        msg = (
            f"{caller}: scan_interval_sec must be finite "
            f"(got {value!r}) — falling back to {default}"
        )
    elif value <= 0:
        error_kind = "scan_interval:non-positive"
        msg = (
            f"{caller}: scan_interval_sec must be > 0 "
            f"(got {value}) — falling back to {default}"
        )

    if error_kind is not None:
        warn_key = (caller, "<scan_interval>", error_kind)
        if warn_key not in _validator_warned:
            logger.warning(msg)
            _validator_warned.add(warn_key)
        return default

    return int(value)
