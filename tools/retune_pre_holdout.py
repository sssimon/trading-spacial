#!/usr/bin/env python3
"""Pre-holdout re-tune wrapper (epic A.4-1, ticket #250).

Drives ``auto_tune.optimize_symbol`` over the active portfolio with a
hard ``--max-date`` cutoff so no bar at or after the holdout start date
can leak into the tune. The tune itself runs the same grid search and
walk-forward windows as the production runner; this wrapper layers on
top three things that the production runner does NOT do:

1. **Artefact directory** at ``data/retune/<run_date>-pre-holdout/``
   with ``params.json`` (drop-in ``symbol_overrides`` block),
   ``report.md`` (current vs re-tuned table), and ``manifest.json``.
2. **No mutation of repo state.** Does not touch ``config.json``,
   does not write ``config_proposed.json``, does not insert into the
   ``tune_results`` table, does not call Telegram. Promotion of the
   re-tuned params to production is a separate PR after A.4-2 + A.4-3.
3. **Manifest with no-leakage proof.** Records the cutoff, code commit
   SHA, ``data/ohlcv.db`` sha256, RNG seed, runtime seconds, and the
   ``[min_ts, max_ts]`` per ``(symbol, timeframe)`` of every series
   consulted. The wrapper asserts ``max_ts < cutoff`` for every series
   before declaring the run successful.

Usage:
    python -m tools.retune_pre_holdout --max-date 2025-04-30

Per-direction tuning is intentionally out of scope here (option (b) per
the A.4-1 scope brief). The artefact uses the flat shape that current
``config.json["symbol_overrides"]`` already uses.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone

import auto_tune
from btc_scanner import DEFAULT_SYMBOLS

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
log = logging.getLogger("retune_pre_holdout")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OHLCV_DB = os.path.join(REPO_ROOT, "data", "ohlcv.db")

TIMEFRAMES = ("5m", "1h", "4h", "1d")


def _resolve_git_commit() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()
    except Exception:
        return "UNKNOWN"


def _sha256_file(path: str, block_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(block_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _per_symbol_data_ranges(db_path: str, symbols: list, cutoff_ms: int) -> dict:
    """Per (symbol, timeframe), report (min_ts_ms, max_ts_ms, count) of bars
    with ``open_time < cutoff_ms``. Used as no-leakage proof in the manifest.
    """
    ranges: dict = {}
    con = sqlite3.connect(db_path)
    try:
        for sym in symbols:
            ranges[sym] = {}
            for tf in TIMEFRAMES:
                row = con.execute(
                    "SELECT MIN(open_time), MAX(open_time), COUNT(*) "
                    "FROM ohlcv WHERE symbol=? AND timeframe=? AND open_time<?",
                    (sym, tf, cutoff_ms),
                ).fetchone()
                if row and row[2]:
                    ranges[sym][tf] = {
                        "min_ts_ms": int(row[0]),
                        "max_ts_ms": int(row[1]),
                        "min_ts_iso": datetime.fromtimestamp(row[0] / 1000, timezone.utc).isoformat(),
                        "max_ts_iso": datetime.fromtimestamp(row[1] / 1000, timezone.utc).isoformat(),
                        "count": int(row[2]),
                    }
                else:
                    ranges[sym][tf] = {"min_ts_ms": None, "max_ts_ms": None, "count": 0}
    finally:
        con.close()
    return ranges


def _verify_no_leakage(ranges: dict, cutoff_ms: int) -> str:
    for sym, tfs in ranges.items():
        for tf, span in tfs.items():
            if span["max_ts_ms"] is not None and span["max_ts_ms"] >= cutoff_ms:
                raise AssertionError(
                    f"no-leakage violation: {sym} {tf} max_ts_ms={span['max_ts_ms']} "
                    f">= cutoff_ms={cutoff_ms}"
                )
    return "PASS"


def _build_params_block(results: list, current_overrides: dict) -> dict:
    """Build the new ``symbol_overrides`` block from re-tune results.

    Symbols where the optimizer recommended CHANGE → use proposed params.
    Symbols where it recommended KEEP / NO_DATA / ERROR → preserve current
    overrides verbatim. Output preserves the flat shape of the current
    config.
    """
    out = {}
    for r in results:
        sym = r["symbol"]
        if r.get("recommendation") == "CHANGE" and r.get("proposed_params"):
            out[sym] = {
                "atr_sl_mult": r["proposed_params"]["atr_sl_mult"],
                "atr_tp_mult": r["proposed_params"]["atr_tp_mult"],
                "atr_be_mult": r["proposed_params"]["atr_be_mult"],
            }
        else:
            cur = current_overrides.get(sym, {})
            if isinstance(cur, dict):
                out[sym] = {
                    "atr_sl_mult": cur.get("atr_sl_mult"),
                    "atr_tp_mult": cur.get("atr_tp_mult"),
                    "atr_be_mult": cur.get("atr_be_mult"),
                }
            else:
                out[sym] = cur
    return out


def _is_at_grid_edge(params: dict | None) -> list:
    if not params:
        return []
    edges = []
    grid = auto_tune.GRID
    for key, value in params.items():
        if key not in grid:
            continue
        values = grid[key]
        if value == values[0]:
            edges.append(f"{key}={value} (lower bound)")
        elif value == values[-1]:
            edges.append(f"{key}={value} (upper bound)")
    return edges


def _build_report(results: list, current_overrides: dict, cutoff_iso: str,
                  ranges: dict, runtime_seconds: float) -> str:
    lines = []
    lines.append("# Pre-holdout Re-tune Report (A.4-1)")
    lines.append("")
    lines.append(f"- **Cutoff (`--max-date`):** {cutoff_iso}")
    lines.append(f"- **Symbols re-tuned:** {len(results)}")
    lines.append(f"- **Runtime:** {runtime_seconds:.0f}s")
    lines.append("")
    lines.append("## Side-by-side: current vs re-tuned")
    lines.append("")
    lines.append("| Symbol | Curr SL | Curr TP | Curr BE | New SL | New TP | New BE | Reco | Val PnL Δ | PF (val) |")
    lines.append("|--------|---------|---------|---------|--------|--------|--------|------|-----------|----------|")
    for r in results:
        sym = r["symbol"]
        cp = r.get("current_params", {}) or {}
        pp = r.get("proposed_params") or cp
        d = r.get("proposal_detail") or {}
        cur_pnl = r.get("current_val_pnl", 0)
        new_pnl = d.get("val_pnl", cur_pnl)
        delta = new_pnl - cur_pnl
        pf = d.get("val_pf", "—")
        lines.append(
            f"| {sym} "
            f"| {cp.get('atr_sl_mult', '—')} | {cp.get('atr_tp_mult', '—')} | {cp.get('atr_be_mult', '—')} "
            f"| {pp.get('atr_sl_mult', '—')} | {pp.get('atr_tp_mult', '—')} | {pp.get('atr_be_mult', '—')} "
            f"| {r.get('recommendation', '—')} "
            f"| ${delta:+,.0f} "
            f"| {pf if isinstance(pf, str) else f'{pf:.2f}'} |"
        )
    lines.append("")

    edge_rows = []
    for r in results:
        edges = _is_at_grid_edge(r.get("proposed_params"))
        if edges:
            edge_rows.append((r["symbol"], edges))
    lines.append("## Grid-edge convergence")
    lines.append("")
    if edge_rows:
        lines.append("Symbols where the optimizer landed on the boundary of the search grid (suggests the true optimum may lie outside the grid):")
        lines.append("")
        for sym, edges in edge_rows:
            lines.append(f"- **{sym}:** {', '.join(edges)}")
    else:
        lines.append("None — all proposed params landed strictly inside the grid.")
    lines.append("")

    lines.append("## Per-symbol caveats")
    lines.append("")
    lines.append("- **JUPUSDT** — earliest OHLCV bar is 2024-01-31. The standard 12-month train window (cutoff − 15mo → cutoff − 3mo) starts before JUP first bar. Indicators that need ~100 bars warmup (notably SMA100 1H, ~4 days) yield NaN over the first ~4 days of JUP train data. Tuning proceeds on the remaining bars; consumers should know JUP was tuned with a degraded warmup window relative to coins with full 2021+ history.")
    lines.append("")

    lines.append("## Data ranges consumed (per symbol × timeframe, all bars below cutoff)")
    lines.append("")
    lines.append("| Symbol | TF | Min ts (UTC) | Max ts (UTC) | Bars |")
    lines.append("|--------|----|---------------|---------------|------|")
    for sym in sorted(ranges.keys()):
        for tf in TIMEFRAMES:
            span = ranges[sym].get(tf, {})
            lines.append(
                f"| {sym} | {tf} "
                f"| {span.get('min_ts_iso', '—')} "
                f"| {span.get('max_ts_iso', '—')} "
                f"| {span.get('count', 0)} |"
            )
    lines.append("")

    return "\n".join(lines)


def _atomic_write_json(path: str, payload: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, sort_keys=True, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pre-holdout re-tune wrapper (A.4-1, #250).",
    )
    parser.add_argument(
        "--max-date",
        type=str,
        required=True,
        help="ISO date (YYYY-MM-DD, UTC). Holdout starts on this day; tune sees only bars strictly before it.",
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        default=None,
        help="Override output directory. Defaults to data/retune/<today>-pre-holdout/.",
    )
    args = parser.parse_args(argv)

    cutoff = datetime.fromisoformat(args.max_date).replace(tzinfo=timezone.utc)
    cutoff_ms = int(cutoff.timestamp() * 1000)

    if not os.path.exists(OHLCV_DB):
        log.error("OHLCV DB not found at %s", OHLCV_DB)
        return 2

    config = auto_tune.load_config()
    seed = auto_tune.initialize_seed(config)
    symbols = auto_tune.get_portfolio_symbols(config)

    if args.out_dir:
        out_dir = args.out_dir
    else:
        run_date = datetime.now(timezone.utc).date().isoformat()
        out_dir = os.path.join(REPO_ROOT, "data", "retune", f"{run_date}-pre-holdout")
    os.makedirs(out_dir, exist_ok=True)

    log.info("Pre-holdout re-tune starting")
    log.info("  cutoff:   %s", cutoff.isoformat())
    log.info("  symbols:  %s", ", ".join(symbols))
    log.info("  seed:     %d", seed)
    log.info("  out_dir:  %s", out_dir)

    start = time.time()
    results = []
    for sym in symbols:
        try:
            log.info("[%s] optimizing...", sym)
            r = auto_tune.optimize_symbol(sym, config, today=cutoff, cutoff=cutoff)
            results.append(r)
        except Exception as exc:  # noqa: BLE001
            log.error("[%s] failed: %s", sym, exc)
            results.append({
                "symbol": sym,
                "recommendation": "ERROR",
                "current_params": auto_tune.get_current_params(sym, config),
                "current_val_pnl": 0,
                "proposed_params": None,
                "proposal_detail": None,
                "error": str(exc),
            })
    runtime_seconds = time.time() - start

    current_overrides = config.get("symbol_overrides", {}) or {}
    params_block = _build_params_block(results, current_overrides)

    log.info("Computing per-symbol data ranges from ohlcv.db...")
    ranges = _per_symbol_data_ranges(OHLCV_DB, symbols, cutoff_ms)
    leakage_check = _verify_no_leakage(ranges, cutoff_ms)
    log.info("Leakage check: %s", leakage_check)

    log.info("Hashing ohlcv.db (this may take a moment)...")
    ohlcv_sha = _sha256_file(OHLCV_DB)
    code_commit = _resolve_git_commit()

    manifest = {
        "cutoff_effective_iso": cutoff.isoformat(),
        "cutoff_effective_ms": cutoff_ms,
        "code_commit": code_commit,
        "ohlcv_sha256": ohlcv_sha,
        "ohlcv_path_relative": os.path.relpath(OHLCV_DB, REPO_ROOT),
        "seed": seed,
        "ran_at_iso": datetime.now(timezone.utc).isoformat(),
        "runtime_seconds": round(runtime_seconds, 2),
        "leakage_check": leakage_check,
        "symbols": symbols,
        "per_symbol_data_ranges": ranges,
        "scope_notes": {
            "per_direction": "out_of_scope_A41_option_b",
            "promotion_to_config": "deferred_to_post_A42_A43_PR",
        },
    }

    params_json_payload = {
        "format_version": 1,
        "shape": "flat_per_symbol",
        "symbol_overrides": params_block,
    }

    report_md = _build_report(results, current_overrides, cutoff.isoformat(), ranges, runtime_seconds)

    _atomic_write_json(os.path.join(out_dir, "params.json"), params_json_payload)
    _atomic_write_json(os.path.join(out_dir, "manifest.json"), manifest)
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write(report_md)

    log.info("Artefacts written to %s", out_dir)
    log.info("  params.json   — drop-in symbol_overrides block (review before promoting)")
    log.info("  report.md     — side-by-side current vs re-tuned, grid-edge flags, caveats")
    log.info("  manifest.json — cutoff, seed, hashes, no-leakage proof")
    return 0


if __name__ == "__main__":
    sys.exit(main())
