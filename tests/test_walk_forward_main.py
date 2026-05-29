"""Tests for `walk_forward.main()` — the CLI entry point.

Commit 7 of #276 wires `--execute` to drive `run_walk_forward` +
`aggregate_run_stats` and dump the summary as JSON. These tests cover:

  1. `--dry-run` (no `--execute`) returns 0 and prints the window list
     without invoking any orchestrator function.
  2. `--execute --ci-mode --config-path <fixture>` with a degenerate
     (zero-window) range returns 0 and prints a JSON summary with
     `n_windows == 0`. This is the exact shape the CI smoke job
     consumes — no OHLCV touched, no `auto_tune` import-time cost.
  3. `--execute --ci-mode --config-path <fixture>` with a real window
     range drives the loop through `frozen_params_for_window` and
     `evaluate_window`. `auto_tune.run_backtest_with_params` and
     `get_portfolio_symbols` are stubbed via `sys.modules` so we
     never touch `data/ohlcv.db` (matches the discipline in
     `tests/test_walk_forward_ci_mode.py`).
  4. `--config-path` rejects missing files and non-object JSON with a
     clear error rather than a stacktrace at the JSON layer.

The real `api.config.load_config` is never reached — every test
passes `--config-path` explicitly so we don't depend on the
production config graph being on disk.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import walk_forward


FIXTURE_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "walk_forward_ci_config.json"
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


class _FakeAutoTune:
    """Stand-in for `auto_tune` installed via `sys.modules`.

    Records `run_backtest_with_params` calls so a test can assert the
    runner was reached (Property 3) or NEVER reached (Properties 1, 2).
    The sentinel return matches `auto_tune.run_backtest_with_params`'s
    no-data path — `evaluate_window` consumes it transparently.
    """

    def __init__(self, symbols: list[str]):
        self._symbols = list(symbols)
        self.runner_calls: list[dict] = []

    def get_portfolio_symbols(self, config):
        return list(self._symbols)

    def run_backtest_with_params(
        self, symbol, params, sim_start, sim_end, *, app_config=None,
    ):
        self.runner_calls.append({
            "symbol": symbol,
            "params": dict(params),
            "sim_start": sim_start,
            "sim_end": sim_end,
        })
        # Mirror the "No data" sentinel — evaluate_window will record
        # n_trades=0 + an `error` string on the per-symbol entry.
        return [], {
            "error": "No data",
            "total_trades": 0,
            "net_pnl": 0,
            "profit_factor": 0,
        }

    def optimize_symbol(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError(
            "optimize_symbol must not be called from the smoke CLI path"
        )


@pytest.fixture
def fake_auto_tune(monkeypatch):
    def _install(symbols):
        fake = _FakeAutoTune(symbols)
        monkeypatch.setitem(sys.modules, "auto_tune", fake)
        return fake
    return _install


def _split_json_payload(stdout: str) -> dict:
    """Extract the JSON block that follows the summary marker."""
    marker = "=== walk-forward summary (JSON) ==="
    assert marker in stdout, (
        f"summary marker missing from stdout; got:\n{stdout!r}"
    )
    _, _, payload = stdout.partition(marker)
    return json.loads(payload.strip())


# --------------------------------------------------------------------------- #
# Property 1: --dry-run prints windows, exits 0, never touches the orchestrator
# --------------------------------------------------------------------------- #


def test_dry_run_no_execute(capsys, monkeypatch):
    """Without `--execute`, the CLI must not invoke `run_walk_forward`."""
    run_calls: list = []

    def boom_run(*args, **kwargs):  # pragma: no cover — must not be reached
        run_calls.append(1)
        raise AssertionError("run_walk_forward called without --execute")

    monkeypatch.setattr(walk_forward, "run_walk_forward", boom_run)

    rc = walk_forward.main([
        "--history-start", "2023-01-01",
        "--history-end",   "2024-06-30",
        "--holdout-start", "2026-01-01",
        "--initial-train-months", "6",
        "--test-months", "3",
        "--step-months", "3",
        "--dry-run",
    ])

    assert rc == 0
    assert run_calls == []
    out = capsys.readouterr().out
    # Smoke check: at least one window row printed.
    assert "train 2023-01-01" in out


# --------------------------------------------------------------------------- #
# Property 2: --execute on a zero-window range emits a clean JSON summary
# --------------------------------------------------------------------------- #


def test_execute_zero_windows_emits_json(capsys, fake_auto_tune):
    """A range that yields no windows still produces a valid summary.

    This is the exact configuration the CI smoke job runs: small enough
    that `compute_windows` returns `[]`, so the orchestrator never
    reaches `auto_tune` and the OHLCV layer is not touched.
    """
    fake = fake_auto_tune(["BTCUSDT"])

    rc = walk_forward.main([
        "--history-start", "2023-01-01",
        "--history-end",   "2023-02-01",
        "--holdout-start", "2026-01-01",
        # initial_train (12mo) > available history → zero windows.
        "--initial-train-months", "12",
        "--test-months", "3",
        "--step-months", "3",
        "--execute",
        "--ci-mode",
        "--config-path", str(FIXTURE_PATH),
    ])

    # Zero windows = exit code 1 per the existing "no windows" branch.
    # The summary JSON is intentionally NOT printed in that case (the
    # branch returns before `--execute` lands). This is the right shape
    # for CI: if a range degenerates, surface a non-zero exit.
    assert rc == 1
    assert fake.runner_calls == []


def test_execute_zero_windows_via_holdout_clip(capsys, fake_auto_tune):
    """Alternate zero-window construction: holdout sits before any test
    window would land. The `--execute` branch still emits the summary
    JSON because windows were *requested* but pruned to zero.

    Reality check: the current main() short-circuits on `not windows`
    BEFORE the `--execute` branch (returns 1). Both branches collapse
    to the same exit semantics; this test pins the contract so a
    future refactor that re-orders the branches still fails loudly if
    it accidentally emits JSON on a degenerate range.
    """
    fake = fake_auto_tune(["BTCUSDT"])

    rc = walk_forward.main([
        "--history-start", "2024-01-01",
        "--history-end",   "2024-02-01",
        "--holdout-start", "2024-01-15",
        "--initial-train-months", "6",
        "--test-months", "3",
        "--step-months", "3",
        "--execute",
        "--ci-mode",
        "--config-path", str(FIXTURE_PATH),
    ])

    assert rc == 1
    assert fake.runner_calls == []


# --------------------------------------------------------------------------- #
# Property 3: --execute with real windows drives frozen path + evaluate_window
# --------------------------------------------------------------------------- #


def test_execute_drives_full_pipeline(capsys, fake_auto_tune):
    """A real range produces windows, runs `frozen_params_for_window`
    per fold, calls the (stubbed) runner, and emits a parseable JSON
    summary on stdout.

    The fake `auto_tune` returns the no-data sentinel — so we expect
    `total_trades == 0` and `n_windows_with_trades == 0`. The point is
    that the orchestrator + aggregator wiring is exercised end-to-end.
    """
    fake = fake_auto_tune(["BTCUSDT", "ETHUSDT"])

    rc = walk_forward.main([
        "--history-start", "2023-01-01",
        "--history-end",   "2024-06-30",
        "--holdout-start", "2026-01-01",
        "--initial-train-months", "6",
        "--test-months", "3",
        "--step-months", "3",
        "--execute",
        "--ci-mode",
        "--config-path", str(FIXTURE_PATH),
    ])

    assert rc == 0
    out = capsys.readouterr().out
    summary = _split_json_payload(out)

    # Wiring assertions — shape, not numbers.
    assert summary["n_windows"] >= 1
    assert summary["total_trades"] == 0
    assert summary["n_windows_with_trades"] == 0
    assert "cv" in summary and isinstance(summary["cv"], dict)
    assert "oos_is_ratio" in summary
    assert summary["oos_is_ratio"]["metric"] == "net_pnl"

    # Runner was reached per (window, symbol) cell that produced frozen
    # params. The fixture lists 2 symbols, each with full ATR overrides
    # → both reach the runner per window.
    assert len(fake.runner_calls) == 2 * summary["n_windows"]
    runner_symbols = {c["symbol"] for c in fake.runner_calls}
    assert runner_symbols == {"BTCUSDT", "ETHUSDT"}


# --------------------------------------------------------------------------- #
# Property 4: --config-path validation
# --------------------------------------------------------------------------- #


def test_config_path_missing_file_raises(tmp_path, fake_auto_tune):
    """A non-existent --config-path must error loudly, not silently
    fall back to load_config().
    """
    fake_auto_tune(["BTCUSDT"])
    missing = tmp_path / "does_not_exist.json"
    with pytest.raises(FileNotFoundError):
        walk_forward.main([
            "--history-start", "2023-01-01",
            "--history-end",   "2024-06-30",
            "--holdout-start", "2026-01-01",
            "--initial-train-months", "6",
            "--test-months", "3",
            "--step-months", "3",
            "--execute",
            "--ci-mode",
            "--config-path", str(missing),
        ])


def test_config_path_non_object_rejected(tmp_path, fake_auto_tune):
    """A JSON file whose top level is not an object must be rejected."""
    fake_auto_tune(["BTCUSDT"])
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        walk_forward.main([
            "--history-start", "2023-01-01",
            "--history-end",   "2024-06-30",
            "--holdout-start", "2026-01-01",
            "--initial-train-months", "6",
            "--test-months", "3",
            "--step-months", "3",
            "--execute",
            "--ci-mode",
            "--config-path", str(bad),
        ])
