#!/usr/bin/env python3
"""Lock the validation holdout dataset (epic A.1, issue #247).

One-shot ops script. Snapshots the last 12 calendar months of OHLCV +
Fear & Greed + BTC funding rate into ``data/holdout/`` with a manifest
that captures hashes, commit, timestamp, and the three caveats that
A.4 / A.6 must inherit.

Usage:
    python scripts/lock_holdout.py            # create the lock
    python scripts/lock_holdout.py --dry-run  # show what would be locked

Refuses to overwrite an existing ``data/holdout/`` directory; that path
is intentionally read-only after the first run.

The script is whitelisted in ``tests/test_holdout_isolation.py`` because
it WRITES to ``data/holdout/`` (the AST scanner detects writes too).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Repo root setup so we can import data/ and reuse backtest fetchers.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from btc_scanner import DEFAULT_SYMBOLS  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
log = logging.getLogger("lock_holdout")


HOLDOUT_DIR = REPO_ROOT / "data" / "holdout"
SOURCE_OHLCV_DB = REPO_ROOT / "data" / "ohlcv.db"
HOLDOUT_DURATION_DAYS = 365
TIMEFRAMES = ("1d", "1h", "4h", "5m")
MANIFEST_SCHEMA_VERSION = 1


def _git_commit_sha() -> str:
    out = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True)
    return out.strip()


def _git_status_clean() -> bool:
    """True if every tracked file matches HEAD. Untracked files (??) are allowed —
    they don't affect what HEAD contains and so don't affect the lock_commit_sha
    contract that the manifest captures.
    """
    out = subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO_ROOT, text=True)
    for line in out.splitlines():
        if not line:
            continue
        if line.startswith("??"):
            continue
        return False
    return True


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ts_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _copy_ohlcv_filtered(holdout_start_ms: int, dest_db: Path) -> dict:
    """Copy ohlcv.db rows for curated symbols where open_time >= holdout_start_ms.

    Schema is preserved exactly. Returns coverage metadata for the manifest.
    """
    log.info("opening source OHLCV at %s", SOURCE_OHLCV_DB)
    src = sqlite3.connect(f"file:{SOURCE_OHLCV_DB}?mode=ro", uri=True)
    try:
        src.row_factory = sqlite3.Row

        log.info("creating destination at %s", dest_db)
        dst = sqlite3.connect(dest_db)
        try:
            dst.executescript(
                """
                CREATE TABLE ohlcv (
                    symbol     TEXT    NOT NULL,
                    timeframe  TEXT    NOT NULL,
                    open_time  INTEGER NOT NULL,
                    open       REAL    NOT NULL,
                    high       REAL    NOT NULL,
                    low        REAL    NOT NULL,
                    close      REAL    NOT NULL,
                    volume     REAL    NOT NULL,
                    provider   TEXT    NOT NULL,
                    fetched_at INTEGER NOT NULL,
                    PRIMARY KEY (symbol, timeframe, open_time)
                ) WITHOUT ROWID;

                CREATE INDEX idx_ohlcv_time
                    ON ohlcv(symbol, timeframe, open_time DESC);

                CREATE TABLE meta (
                    k TEXT PRIMARY KEY,
                    v TEXT NOT NULL
                );

                CREATE TABLE symbol_earliest (
                    symbol         TEXT    NOT NULL,
                    timeframe      TEXT    NOT NULL,
                    first_bar_ms   INTEGER NOT NULL,
                    PRIMARY KEY (symbol, timeframe)
                );
                """
            )

            # Copy meta table verbatim.
            for row in src.execute("SELECT k, v FROM meta"):
                dst.execute("INSERT INTO meta (k, v) VALUES (?, ?)", (row["k"], row["v"]))

            placeholders = ",".join("?" * len(DEFAULT_SYMBOLS))
            coverage: dict[str, dict] = {}
            total_rows = 0

            for symbol in DEFAULT_SYMBOLS:
                coverage[symbol] = {}
                for tf in TIMEFRAMES:
                    rows = src.execute(
                        "SELECT * FROM ohlcv WHERE symbol = ? AND timeframe = ? AND open_time >= ? ORDER BY open_time",
                        (symbol, tf, holdout_start_ms),
                    ).fetchall()
                    if not rows:
                        coverage[symbol][tf] = {"rows": 0, "first_ms": None, "last_ms": None}
                        continue
                    dst.executemany(
                        "INSERT INTO ohlcv (symbol, timeframe, open_time, open, high, low, close, volume, provider, fetched_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [
                            (
                                r["symbol"], r["timeframe"], r["open_time"],
                                r["open"], r["high"], r["low"], r["close"], r["volume"],
                                r["provider"], r["fetched_at"],
                            )
                            for r in rows
                        ],
                    )
                    first_ms = rows[0]["open_time"]
                    last_ms = rows[-1]["open_time"]
                    coverage[symbol][tf] = {
                        "rows": len(rows),
                        "first_ms": first_ms,
                        "first_iso": _ts_to_iso(first_ms),
                        "last_ms": last_ms,
                        "last_iso": _ts_to_iso(last_ms),
                    }
                    total_rows += len(rows)

                    # Capture per-(symbol, timeframe) earliest holdout bar, useful
                    # for A.4 sanity checks against the train/holdout boundary.
                    dst.execute(
                        "INSERT INTO symbol_earliest (symbol, timeframe, first_bar_ms) VALUES (?, ?, ?)",
                        (symbol, tf, first_ms),
                    )

            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()

    log.info("OHLCV holdout: %d rows across %d symbols × %d timeframes",
             total_rows, len(DEFAULT_SYMBOLS), len(TIMEFRAMES))
    return {"total_rows": total_rows, "per_symbol": coverage}


def _snapshot_fng(holdout_start: datetime, holdout_end: datetime, dest: Path) -> dict:
    """Fetch full F&G history via existing backtest helper, slice window, save parquet."""
    from backtest import get_historical_fear_greed

    fetched_at = datetime.now(timezone.utc).isoformat()
    df = get_historical_fear_greed()
    if df.empty:
        raise RuntimeError("F&G fetch returned empty DataFrame — refuse to lock partial holdout")

    df = df.copy()
    if df.index.tz is not None:
        idx_naive = df.index.tz_convert("UTC").tz_localize(None)
    else:
        idx_naive = df.index
    start_naive = holdout_start.replace(tzinfo=None)
    end_naive = holdout_end.replace(tzinfo=None)
    mask = (idx_naive >= start_naive) & (idx_naive <= end_naive)
    sliced = df.loc[mask]
    if sliced.empty:
        raise RuntimeError(
            f"F&G slice for [{holdout_start.date()}, {holdout_end.date()}] is empty — "
            "refuse to lock partial holdout"
        )

    sliced.to_parquet(dest)
    log.info("F&G holdout: %d daily values (%s → %s)",
             len(sliced), sliced.index[0].date(), sliced.index[-1].date())
    return {
        "rows": len(sliced),
        "first_iso": sliced.index[0].isoformat(),
        "last_iso": sliced.index[-1].isoformat(),
        "fetched_at_utc": fetched_at,
        "source": "https://api.alternative.me/fng/?limit=0",
    }


def _snapshot_funding(holdout_start: datetime, holdout_end: datetime, dest: Path) -> dict:
    """Fetch full BTC funding rate history via existing backtest helper, slice window."""
    from backtest import get_historical_funding_rate

    fetched_at = datetime.now(timezone.utc).isoformat()
    df = get_historical_funding_rate()
    if df.empty:
        raise RuntimeError("Funding rate fetch returned empty DataFrame — refuse to lock partial holdout")

    df = df.copy()
    if df.index.tz is not None:
        idx_naive = df.index.tz_convert("UTC").tz_localize(None)
    else:
        idx_naive = df.index
    start_naive = holdout_start.replace(tzinfo=None)
    end_naive = holdout_end.replace(tzinfo=None)
    mask = (idx_naive >= start_naive) & (idx_naive <= end_naive)
    sliced = df.loc[mask]
    if sliced.empty:
        raise RuntimeError(
            f"Funding slice for [{holdout_start.date()}, {holdout_end.date()}] is empty — "
            "refuse to lock partial holdout"
        )

    sliced.to_parquet(dest)
    log.info("Funding holdout (BTC): %d 8h-period values (%s → %s)",
             len(sliced), sliced.index[0].date(), sliced.index[-1].date())
    return {
        "rows": len(sliced),
        "first_iso": sliced.index[0].isoformat(),
        "last_iso": sliced.index[-1].isoformat(),
        "fetched_at_utc": fetched_at,
        "source": "https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT",
        "symbols_covered": ["BTCUSDT"],
        "symbols_uncovered_rationale": (
            "Per strategy/regime.py the regime detector uses BTC funding rate as a "
            "global signal for all symbols (line 240 in production code, line 349 "
            "in cached path). Snapshotting BTC-only matches what production scoring "
            "consumes."
        ),
    }


def _make_readonly(root: Path) -> None:
    """chmod all files to 0o444 and dirs to 0o555 — prevent accidental writes."""
    for path in root.rglob("*"):
        if path.is_file():
            path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        elif path.is_dir():
            path.chmod(stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    root.chmod(stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)


CAVEATS = [
    {
        "id": "RE_TUNE_REQUIRED_FOR_A4",
        "summary": (
            "Current atr_sl_mult/tp/be in config.json['symbol_overrides'] were tuned "
            "over the full history including the holdout range. Evaluating those "
            "parameters directly against this holdout is leakage."
        ),
        "obligation": (
            "A.4 (#250) MUST re-tune over the train segment "
            "[ohlcv earliest, holdout_start - 1 bar] BEFORE evaluating against this holdout."
        ),
    },
    {
        "id": "REGIME_COMPOSITION_NOT_GUARANTEED",
        "summary": (
            "The 12-month holdout window may not cover all regimes (bull/bear/neutral). "
            "If the window is dominated by one regime, A.4 cannot test SHORT gating "
            "or BULL/BEAR transitions out-of-sample with equal coverage."
        ),
        "obligation": (
            "A.4 (#250) MUST report the bull/bear/neutral mix observed in this window "
            "(reconstructed from the locked F&G + funding + price components) and "
            "explicitly call out coverage gaps before claiming validation."
        ),
    },
    {
        "id": "DRIFT_NOT_AUTODETECTABLE_FROM_LOCK",
        "summary": (
            "F&G and funding rate hashes freeze the snapshot taken at lock_timestamp_utc. "
            "Provider revisions of historical values are NOT detectable from this lock alone."
        ),
        "obligation": (
            "A.4 (#250) MUST re-fetch F&G and funding for the holdout window from "
            "their source APIs and diff against the locked snapshot. Any divergence "
            "must be reported (not silently overridden)."
        ),
    },
]


def _write_manifest(
    *,
    manifest_path: Path,
    lock_ts: datetime,
    commit_sha: str,
    git_clean: bool,
    holdout_start: datetime,
    ohlcv_meta: dict,
    fng_meta: dict,
    funding_meta: dict,
    file_hashes: dict,
) -> None:
    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "lock_timestamp_utc": lock_ts.isoformat(),
        "lock_commit_sha": commit_sha,
        "lock_git_clean": git_clean,
        "epic": "#246",
        "ticket": "#247",
        "cutoff": {
            "type": "fixed",
            "duration_days": HOLDOUT_DURATION_DAYS,
            "start_inclusive_utc": holdout_start.isoformat(),
        },
        "curated_symbols": list(DEFAULT_SYMBOLS),
        "sources": {
            "ohlcv": {
                "file": "ohlcv.sqlite",
                "sha256": file_hashes["ohlcv.sqlite"],
                "total_rows": ohlcv_meta["total_rows"],
                "timeframes": list(TIMEFRAMES),
                "per_symbol_per_timeframe": ohlcv_meta["per_symbol"],
                "drift_caveat": (
                    "OHLCV bars are immutable once recorded by Binance/Bybit. The "
                    "source data/ohlcv.db is append-only via data/_storage.py. "
                    "This snapshot is the authoritative copy for evaluation."
                ),
            },
            "fear_greed": {
                "file": "fng.parquet",
                "sha256": file_hashes["fng.parquet"],
                **fng_meta,
                "drift_caveat": (
                    "alternative.me may revise historical values. Hash freezes the "
                    "snapshot taken at fetched_at_utc; A.4 must re-fetch + diff."
                ),
            },
            "funding_rate": {
                "file": "funding.parquet",
                "sha256": file_hashes["funding.parquet"],
                **funding_meta,
                "drift_caveat": (
                    "Binance Futures may revise historical funding values. Hash "
                    "freezes the snapshot at fetched_at_utc; A.4 must re-fetch + diff."
                ),
            },
        },
        "uncovered_sources": [],
        "caveats": CAVEATS,
        "guard": {
            "wrapper_module": "data/holdout_access.py",
            "ast_scanner": "tests/test_holdout_isolation.py",
            "policy": (
                "Guard A (wrapper) is opt-in ergonomics. Guard B (AST scanner) is "
                "the structural net. There is intentionally no monkey-patch / env "
                "override of A. To use the holdout legitimately, either call "
                "open_holdout(..., evaluation_mode=True) or add the new module to "
                "HOLDOUT_LEGITIMATE_MODULES in tests/test_holdout_isolation.py with "
                "a justification in the PR."
            ),
        },
        "provenance_doc": "docs/superpowers/specs/es/2026-04-30-a1-holdout-dataset-provenance.md",
    }
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")
    log.info("manifest written: %s", manifest_path)


README = """# data/holdout/ — locked validation snapshot

This directory is the **intact holdout** for strategy validation (epic #246, ticket #247).
It is **read-only** at the filesystem level and gated by:

- `data/holdout_access.py` — `open_holdout(rel_path, *, evaluation_mode=True)` wrapper
- `tests/test_holdout_isolation.py` — AST scanner (whitelist-based)

**Do not read files in this directory directly from scanner / auto_tune / backtest tuning code.**
The AST scanner will fail CI if you do.

For full context (corte, fuentes cubiertas, caveats, justificación), see:

- `docs/superpowers/specs/es/2026-04-30-a1-holdout-dataset-provenance.md`
- `MANIFEST.json` in this directory.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Lock the validation holdout (A.1, #247).")
    parser.add_argument("--dry-run", action="store_true", help="report what would be locked, do not write")
    parser.add_argument(
        "--allow-dirty-tree", action="store_true",
        help="permit lock with uncommitted changes (manifest will record git_clean=false)",
    )
    args = parser.parse_args()

    if HOLDOUT_DIR.exists():
        log.error("data/holdout/ already exists — refuse to overwrite a locked snapshot.")
        log.error("If you really need to re-lock, remove the directory by hand "
                  "(chmod +w first) and document the rationale in the PR.")
        return 2

    if not SOURCE_OHLCV_DB.exists():
        log.error("source OHLCV not found at %s", SOURCE_OHLCV_DB)
        return 2

    git_clean = _git_status_clean()
    if not git_clean and not args.allow_dirty_tree:
        log.error("git tree is dirty — pass --allow-dirty-tree to record this in the manifest, "
                  "or commit/stash first.")
        return 2

    commit_sha = _git_commit_sha()
    lock_ts = datetime.now(timezone.utc)
    holdout_start = (lock_ts - timedelta(days=HOLDOUT_DURATION_DAYS)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    holdout_start_ms = int(holdout_start.timestamp() * 1000)

    log.info("=" * 70)
    log.info("Lock parameters")
    log.info("  lock_timestamp_utc : %s", lock_ts.isoformat())
    log.info("  commit_sha         : %s", commit_sha)
    log.info("  git_clean          : %s", git_clean)
    log.info("  holdout_start_utc  : %s", holdout_start.isoformat())
    log.info("  holdout_duration   : %d days (fixed, not rolling)", HOLDOUT_DURATION_DAYS)
    log.info("  curated_symbols    : %s", ",".join(DEFAULT_SYMBOLS))
    log.info("=" * 70)

    if args.dry_run:
        log.info("--dry-run: not writing.")
        return 0

    HOLDOUT_DIR.mkdir(parents=True, exist_ok=False)
    try:
        ohlcv_dest = HOLDOUT_DIR / "ohlcv.sqlite"
        fng_dest = HOLDOUT_DIR / "fng.parquet"
        funding_dest = HOLDOUT_DIR / "funding.parquet"
        manifest_dest = HOLDOUT_DIR / "MANIFEST.json"
        readme_dest = HOLDOUT_DIR / "README.md"

        ohlcv_meta = _copy_ohlcv_filtered(holdout_start_ms, ohlcv_dest)
        fng_meta = _snapshot_fng(holdout_start, lock_ts, fng_dest)
        funding_meta = _snapshot_funding(holdout_start, lock_ts, funding_dest)

        file_hashes = {
            "ohlcv.sqlite": _sha256(ohlcv_dest),
            "fng.parquet": _sha256(fng_dest),
            "funding.parquet": _sha256(funding_dest),
        }

        _write_manifest(
            manifest_path=manifest_dest,
            lock_ts=lock_ts,
            commit_sha=commit_sha,
            git_clean=git_clean,
            holdout_start=holdout_start,
            ohlcv_meta=ohlcv_meta,
            fng_meta=fng_meta,
            funding_meta=funding_meta,
            file_hashes=file_hashes,
        )

        readme_dest.write_text(README)
        _make_readonly(HOLDOUT_DIR)

    except Exception:
        log.error("lock failed; cleaning up partial data/holdout/ ...")
        # Restore writable mode in case _make_readonly partially ran, then remove.
        for path in HOLDOUT_DIR.rglob("*"):
            try:
                path.chmod(0o644)
            except FileNotFoundError:
                pass
        try:
            HOLDOUT_DIR.chmod(0o755)
        except FileNotFoundError:
            pass
        shutil.rmtree(HOLDOUT_DIR, ignore_errors=True)
        raise

    log.info("=" * 70)
    log.info("Lock complete. data/holdout/ is now read-only.")
    log.info("  ohlcv.sqlite : %d rows  sha256=%s",
             ohlcv_meta["total_rows"], file_hashes["ohlcv.sqlite"][:16] + "...")
    log.info("  fng.parquet  : %d rows  sha256=%s",
             fng_meta["rows"], file_hashes["fng.parquet"][:16] + "...")
    log.info("  funding.parq : %d rows  sha256=%s",
             funding_meta["rows"], file_hashes["funding.parquet"][:16] + "...")
    log.info("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
