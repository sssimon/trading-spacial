"""Read-guard for the locked holdout dataset (epic A.1, issue #247).

This module is the single legitimate entry point for reading anything under
`data/holdout/`. The wrapper raises `HoldoutAccessError` unless the caller
explicitly passes `evaluation_mode=True`, which signals that the caller is
A.2 walk-forward / A.4 evaluation harness — not scanner / auto_tune /
backtest tuning code.

Guard layers (per #247 decision A+B):
  A) ergonomics + explicit footprint of legitimate access (this module)
  B) AST whitelist scanner in tests/test_holdout_isolation.py — the real
     contamination net. A is opt-in; B is structural.

There is intentionally no monkey-patch / env var override of A. The only
ways to read holdout data are:
  - through `open_holdout(..., evaluation_mode=True)`, or
  - by adding the calling module to HOLDOUT_LEGITIMATE_MODULES in the
    AST test (which is itself reviewed in PRs).

The naming `holdout_access.py` (not `holdout.py`) avoids any module/package
ambiguity with the `data/holdout/` directory of locked artifacts.
"""
from __future__ import annotations

from pathlib import Path

_HOLDOUT_ROOT = Path(__file__).resolve().parent / "holdout"


class HoldoutAccessError(RuntimeError):
    """Raised when holdout data is accessed without `evaluation_mode=True`."""


def open_holdout(rel_path: str, *, evaluation_mode: bool) -> Path:
    """Resolve a path under `data/holdout/`. Caller opens the returned Path.

    Parameters
    ----------
    rel_path
        Relative path inside `data/holdout/` (e.g. ``"ohlcv.sqlite"``,
        ``"MANIFEST.json"``, ``"fng.parquet"``). Must not escape the
        holdout root.
    evaluation_mode
        Keyword-only, must be ``True``. Pass it explicitly from A.2 / A.4
        evaluation harness modules. Anything else (False, missing, truthy
        non-bool, etc.) raises ``HoldoutAccessError``.

    Returns
    -------
    Path
        Absolute, resolved path to the requested holdout file.

    Raises
    ------
    HoldoutAccessError
        If ``evaluation_mode`` is not literally ``True``, or if ``rel_path``
        resolves outside ``data/holdout/``.
    FileNotFoundError
        If the resolved path does not exist.
    """
    if evaluation_mode is not True:
        raise HoldoutAccessError(
            "data/holdout/ requires evaluation_mode=True — this path is "
            "read only by A.2 walk-forward and A.4 evaluation harness, "
            "NOT by scanner/auto_tune/backtest tuning code. See epic #246 "
            "and issue #247."
        )

    target = (_HOLDOUT_ROOT / rel_path).resolve()
    try:
        target.relative_to(_HOLDOUT_ROOT)
    except ValueError as exc:
        raise HoldoutAccessError(
            f"rel_path {rel_path!r} escapes data/holdout/ — refused."
        ) from exc

    if not target.exists():
        raise FileNotFoundError(f"holdout file not found: {target}")

    return target


def holdout_root() -> Path:
    """Return the holdout root path. For tooling that needs to know the
    location (e.g. the AST scanner whitelist) without reading any file."""
    return _HOLDOUT_ROOT
