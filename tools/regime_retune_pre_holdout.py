#!/usr/bin/env python3
"""Pre-holdout regime threshold re-tune mini-harness.

Sweeps the 4 configurations locked by historical record over the pre-holdout
window [earliest, 2025-04-30T00:00:00Z), aggregates net_pnl per config across
the 10 portfolio symbols, identifies the winner, and applies pre-registered
decision flags (CHANGE detection, sanity check, stability check).

Runs a single backtest per (symbol, config) — grid + objective are locked by
the methodology spec, not optimized. Implemented inline (no shared helpers
from sister harnesses) because this harness must run before its sibling
re-tune (#287) is merged — see spec §2.10 sequencing.

Usage:
    python -m tools.regime_retune_pre_holdout --max-date 2025-04-30
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
log = logging.getLogger("regime_retune_pre_holdout")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OHLCV_DB = os.path.join(REPO_ROOT, "data", "ohlcv.db")

TIMEFRAMES = ("5m", "1h", "4h", "1d")

GRID = [
    {"name": "60_40",       "bull_above": 60,   "bear_below": 40,   "disabled": False},
    {"name": "70_30",       "bull_above": 70,   "bear_below": 30,   "disabled": False},
    {"name": "80_20",       "bull_above": 80,   "bear_below": 20,   "disabled": False},
    {"name": "no_detector", "bull_above": None, "bear_below": None, "disabled": True},
]


def _resolve_git_commit() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        log.error("Could not resolve git commit (manifest will record 'UNKNOWN'): %s",
                  exc, exc_info=True)
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


def _slice_below_cutoff(df: pd.DataFrame, cutoff: datetime) -> pd.DataFrame:
    """Return the subset of df whose index is strictly < cutoff.

    Implemented inline (no import from auto_tune) because this harness must
    run before the sibling re-tune (#287) is merged — see spec §2.10
    sequencing.
    """
    if df is None or df.empty:
        return df
    cutoff_ts = pd.Timestamp(cutoff)
    if df.index.tz is None and cutoff_ts.tz is not None:
        cutoff_ts_for_cmp = cutoff_ts.tz_localize(None)
    elif df.index.tz is not None and cutoff_ts.tz is None:
        cutoff_ts_for_cmp = cutoff_ts.tz_localize("UTC")
    else:
        cutoff_ts_for_cmp = cutoff_ts
    sliced = df.loc[df.index < cutoff_ts_for_cmp]
    if not sliced.empty:
        max_ts = pd.Timestamp(sliced.index.max())
        if max_ts.tz is None:
            max_ts = max_ts.tz_localize("UTC")
        cutoff_check = cutoff_ts if cutoff_ts.tz is not None else cutoff_ts.tz_localize("UTC")
        assert max_ts < cutoff_check, f"Slice leak: max_ts={max_ts} >= cutoff={cutoff_check}"
    return sliced


def _per_symbol_data_ranges(db_path: str, symbols: list, cutoff_ms: int) -> dict:
    """Per (symbol, tf), report [min_ts_ms, max_ts_ms, count] of bars with open_time < cutoff_ms."""
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


def _load_config() -> dict:
    """Load config.json from repo root.

    Hard-errors when missing: the harness pulls production symbol_overrides
    from this file so that the regime threshold is the only varying input
    across the sweep. Running without it silently substitutes empty overrides
    and contaminates the comparison.
    """
    cfg_path = os.path.join(REPO_ROOT, "config.json")
    if not os.path.exists(cfg_path):
        raise FileNotFoundError(
            f"config.json not found at {cfg_path}. Harness requires production "
            "symbol_overrides to ensure regime threshold is the sole varying input."
        )
    with open(cfg_path, encoding="utf-8") as f:
        return json.load(f)


def _load_frames(symbol: str, cutoff: datetime) -> dict:
    """Load all DataFrames simulate_strategy needs, sliced strictly < cutoff.

    Returns dict with keys: df1h, df4h, df5m, df1d, df1d_btc, df_fng, df_funding.
    Each value is a (possibly empty) DataFrame whose index is strictly < cutoff.
    Frame loaders mirror the canonical path used by scripts/_a02_diag_lib.fetch_data.
    """
    from backtest import (
        get_cached_data,
        get_historical_fear_greed,
        get_historical_funding_rate,
    )

    out = {}
    for tf, key in (("1h", "df1h"), ("4h", "df4h"), ("5m", "df5m"), ("1d", "df1d")):
        df = get_cached_data(symbol, tf)
        out[key] = _slice_below_cutoff(df, cutoff)

    if symbol == "BTCUSDT":
        out["df1d_btc"] = out["df1d"]
    else:
        df1d_btc = get_cached_data("BTCUSDT", "1d")
        out["df1d_btc"] = _slice_below_cutoff(df1d_btc, cutoff)

    out["df_fng"] = _slice_below_cutoff(get_historical_fear_greed(), cutoff)
    out["df_funding"] = _slice_below_cutoff(get_historical_funding_rate(), cutoff)

    return out


def _run_one_backtest(symbol: str, config: dict, cutoff: datetime,
                      app_config: dict | None = None) -> dict:
    """Run a single backtest for (symbol, regime config). Returns net_pnl + diagnostics.

    Uses production ATR multipliers from config.json so the regime threshold is
    the only varying input across the sweep. Costs (slippage/spread/fees) match
    production. Returns {symbol, config, net_pnl, trades, error}.
    """
    from backtest import simulate_strategy

    if app_config is None:
        app_config = _load_config()
    overrides = app_config.get("symbol_overrides", {}) or {}

    frames = _load_frames(symbol, cutoff)
    if frames["df1h"].empty or frames["df4h"].empty or frames["df5m"].empty:
        return {"symbol": symbol, "config": config["name"], "net_pnl": 0.0,
                "trades": 0, "error": "empty_ohlcv_below_cutoff"}

    kwargs = {
        "df1h": frames["df1h"],
        "df4h": frames["df4h"],
        "df5m": frames["df5m"],
        "df1d": frames["df1d"],
        "df1d_btc": frames["df1d_btc"],
        "df_fng": frames["df_fng"],
        "df_funding": frames["df_funding"],
        "symbol": symbol,
        "sl_mode": "atr",
        "symbol_overrides": overrides,
        "cfg": app_config,
        "enable_slippage": True,
        "enable_spread": True,
        "enable_fees": True,
    }
    if config["disabled"]:
        kwargs["regime_disabled"] = True
    else:
        kwargs["regime_thresholds"] = (config["bull_above"], config["bear_below"])

    try:
        trades, _equity = simulate_strategy(**kwargs)
    except (sqlite3.DatabaseError, OSError) as exc:
        log.error("[%s][%s] I/O failure: %s", symbol, config["name"], exc, exc_info=True)
        return {"symbol": symbol, "config": config["name"], "net_pnl": 0.0,
                "trades": 0, "error": f"io:{exc}"}
    except (ValueError, AssertionError) as exc:
        log.warning("[%s][%s] data/assertion error: %s", symbol, config["name"], exc, exc_info=True)
        return {"symbol": symbol, "config": config["name"], "net_pnl": 0.0,
                "trades": 0, "error": f"data:{exc}"}

    net_pnl = sum(t.get("pnl_usd", 0.0) for t in trades)
    return {"symbol": symbol, "config": config["name"], "net_pnl": float(net_pnl),
            "trades": len(trades), "error": None}


def _aggregate_results(cells: list) -> dict:
    """Aggregate per-cell results into per-config sums + decision flags.

    cells: list of {symbol, config, net_pnl, trades, error} dicts.
    Tie-break on lex order of config name keeps the winner choice deterministic.
    """
    per_config_pnl: dict[str, float] = {}
    per_config_trades: dict[str, int] = {}
    for cell in cells:
        cfg = cell["config"]
        per_config_pnl[cfg] = per_config_pnl.get(cfg, 0.0) + float(cell["net_pnl"])
        per_config_trades[cfg] = per_config_trades.get(cfg, 0) + int(cell["trades"])

    sorted_configs = sorted(per_config_pnl.items(), key=lambda kv: (-kv[1], kv[0]))
    winner_name, winner_pnl = sorted_configs[0]
    runner_up_name, runner_up_pnl = sorted_configs[1]

    if abs(winner_pnl) > 1e-9:
        margin_pct = (winner_pnl - runner_up_pnl) / abs(winner_pnl) * 100.0
    else:
        margin_pct = 0.0

    decision_flags = {
        "change_detection": winner_name != "60_40",
        "sanity_check":     winner_name == "no_detector",
        "stability_check":  margin_pct < 5.0,
    }

    return {
        "per_config_pnl": per_config_pnl,
        "per_config_trades": per_config_trades,
        "winner": winner_name,
        "winner_pnl": winner_pnl,
        "runner_up": runner_up_name,
        "runner_up_pnl": runner_up_pnl,
        "winner_margin_pct": margin_pct,
        "decision_flags": decision_flags,
    }


def _atomic_write_json(path: str, payload: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, sort_keys=True, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def _atomic_write_text(path: str, content: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)


def _write_regime_params(path: str, agg: dict) -> None:
    """Write regime_params.json. Shape depends on winner:
      - threshold winner:   {"regime_thresholds": {"bull_above": <int>, "bear_below": <int>}}
      - no_detector winner: {"regime_disabled": true}
    """
    winner = agg["winner"]
    if winner == "no_detector":
        payload = {"regime_disabled": True}
    else:
        cfg = next(c for c in GRID if c["name"] == winner)
        payload = {
            "regime_thresholds": {
                "bull_above": cfg["bull_above"],
                "bear_below": cfg["bear_below"],
            },
        }
    _atomic_write_json(path, payload)


def _build_manifest(agg: dict, cutoff: datetime, cutoff_ms: int,
                    ohlcv_sha: str, code_commit: str,
                    ranges: dict, runtime_seconds: float,
                    leakage_check: str, symbols: list,
                    symbol_overrides: dict | None = None) -> dict:
    overrides_sha = hashlib.sha256(
        json.dumps(symbol_overrides or {}, sort_keys=True).encode()
    ).hexdigest()
    return {
        "harness": "regime_retune_pre_holdout",
        "spec_ref": "docs/superpowers/specs/es/2026-05-03-asunciones-tecnicas-pre-holdout.md §2.10",
        "cutoff_effective_iso": cutoff.isoformat(),
        "cutoff_effective_ms": cutoff_ms,
        "code_commit": code_commit,
        "ohlcv_sha256": ohlcv_sha,
        "ohlcv_path_relative": os.path.relpath(OHLCV_DB, REPO_ROOT),
        "symbol_overrides_sha256": overrides_sha,
        "ran_at_iso": datetime.now(timezone.utc).isoformat(),
        "runtime_seconds": round(runtime_seconds, 2),
        "leakage_check": leakage_check,
        "symbols": symbols,
        "grid": [{"name": g["name"], "bull_above": g["bull_above"],
                  "bear_below": g["bear_below"], "disabled": g["disabled"]} for g in GRID],
        "per_config_pnl": agg["per_config_pnl"],
        "per_config_trades": agg["per_config_trades"],
        "winner": agg["winner"],
        "winner_pnl": agg["winner_pnl"],
        "runner_up": agg["runner_up"],
        "runner_up_pnl": agg["runner_up_pnl"],
        "winner_margin_pct": round(agg["winner_margin_pct"], 4),
        "decision_flags": agg["decision_flags"],
        "per_symbol_data_ranges": ranges,
        "scope_notes": {
            "n_effective_contribution": 0,
            "promotion_to_strategy_regime_py_and_backtest_py": "deferred_to_phase_5_post_holdout_PR",
        },
    }


def _build_report(agg: dict, cells: list, cutoff: datetime,
                  ranges: dict, runtime_seconds: float, symbols: list) -> str:
    lines = []
    lines.append("# Pre-holdout Regime Threshold Re-tune Report (A.4-1.5)")
    lines.append("")
    lines.append(f"- **Cutoff (`--max-date`):** {cutoff.isoformat()}")
    lines.append(f"- **Symbols:** {', '.join(symbols)}")
    lines.append(f"- **Runtime:** {runtime_seconds:.0f}s")
    lines.append(f"- **Spec ref:** D9 §2.10 (locked grid, locked objective)")
    lines.append("")
    lines.append("## Per-config aggregate")
    lines.append("")
    lines.append("| Config | Sum net_pnl (USD) | Total trades | Margin to winner |")
    lines.append("|--------|-------------------|--------------|------------------|")
    for cfg_name in ("60_40", "70_30", "80_20", "no_detector"):
        pnl = agg["per_config_pnl"].get(cfg_name, 0.0)
        tr = agg["per_config_trades"].get(cfg_name, 0)
        if cfg_name == agg["winner"]:
            margin = "**winner**"
        elif abs(agg["winner_pnl"]) > 1e-9:
            m = (agg["winner_pnl"] - pnl) / abs(agg["winner_pnl"]) * 100
            margin = f"-{m:.2f}%"
        else:
            margin = "—"
        lines.append(f"| {cfg_name} | ${pnl:+,.2f} | {tr} | {margin} |")
    lines.append("")
    lines.append(f"**Winner:** `{agg['winner']}` (sum net_pnl = ${agg['winner_pnl']:+,.2f})")
    lines.append(f"**Runner-up:** `{agg['runner_up']}` (sum net_pnl = ${agg['runner_up_pnl']:+,.2f})")
    lines.append(f"**Margin:** {agg['winner_margin_pct']:.2f}% of |winner|")
    lines.append("")
    lines.append("## Decision flags (pre-registered per D9 §2.10)")
    lines.append("")
    eq = "==" if agg['winner'] == '60_40' else "!="
    sanity_msg = "→ HALT + DEBUG required before any commit" if agg['decision_flags']['sanity_check'] else ""
    stab_msg = "→ informational caveat: regime is operating in a flat region" if agg['decision_flags']['stability_check'] else ""
    lines.append(f"- **CHANGE detection:** `{agg['decision_flags']['change_detection']}` "
                 f"(winner {eq} current production `60_40`)")
    lines.append(f"- **Sanity check (no-detector wins):** "
                 f"`{agg['decision_flags']['sanity_check']}` {sanity_msg}")
    lines.append(f"- **Stability check (margin < 5%):** "
                 f"`{agg['decision_flags']['stability_check']}` {stab_msg}")
    lines.append("")
    lines.append("## Per-symbol breakdown")
    lines.append("")
    lines.append("| Symbol | 60_40 | 70_30 | 80_20 | no_detector |")
    lines.append("|--------|-------|-------|-------|-------------|")
    by_symbol_config: dict = {}
    for c in cells:
        by_symbol_config.setdefault(c["symbol"], {})[c["config"]] = c["net_pnl"]
    for sym in symbols:
        row = by_symbol_config.get(sym, {})
        cells_str = " | ".join(
            f"${row.get(cfg, 0.0):+,.0f}"
            for cfg in ("60_40", "70_30", "80_20", "no_detector")
        )
        lines.append(f"| {sym} | {cells_str} |")
    lines.append("")
    lines.append("## Caveats")
    lines.append("")
    lines.append("- **JUPUSDT** — earliest OHLCV bar is 2024-01-31. SMA200 (1d) and SMA100 (1h) "
                 "yield NaN over the first ~4 days of JUP train data. Same warmup degradation "
                 "applies here as in A.4-1; results for JUP are reported but should be interpreted "
                 "with this caveat.")
    lines.append("")
    lines.append("## Data ranges (per symbol × tf, all bars below cutoff)")
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


def _get_symbols() -> list[str]:
    """Return the 10 portfolio symbols from btc_scanner.DEFAULT_SYMBOLS."""
    from btc_scanner import DEFAULT_SYMBOLS
    return list(DEFAULT_SYMBOLS)


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pre-holdout regime threshold re-tune mini-harness (A.4-1.5).",
    )
    parser.add_argument("--max-date", type=str, required=True,
                        help="ISO date (YYYY-MM-DD, UTC). Holdout starts on this day; "
                             "tune sees only bars strictly before it.")
    parser.add_argument("--out-dir", type=str, default=None,
                        help="Override output directory. "
                             "Defaults to data/retune/<today>-pre-holdout/.")
    args = parser.parse_args(argv)

    cutoff = datetime.fromisoformat(args.max_date).replace(tzinfo=timezone.utc)
    cutoff_ms = int(cutoff.timestamp() * 1000)

    if not os.path.exists(OHLCV_DB):
        log.error("OHLCV DB not found at %s", OHLCV_DB)
        return 2

    symbols = _get_symbols()
    app_config = _load_config()

    if args.out_dir:
        out_dir = args.out_dir
    else:
        run_date = datetime.now(timezone.utc).date().isoformat()
        out_dir = os.path.join(REPO_ROOT, "data", "retune", f"{run_date}-pre-holdout")
    os.makedirs(out_dir, exist_ok=True)

    log.info("Regime threshold re-tune starting")
    log.info("  cutoff:  %s", cutoff.isoformat())
    log.info("  symbols: %s", ", ".join(symbols))
    log.info("  configs: %s", ", ".join(c["name"] for c in GRID))
    log.info("  out_dir: %s", out_dir)

    start = time.time()
    cells = []
    for symbol in symbols:
        for config in GRID:
            cell = _run_one_backtest(symbol, config, cutoff, app_config=app_config)
            if cell["error"]:
                log.warning("[%s][%s] error: %s", symbol, config["name"], cell["error"])
            else:
                log.info("[%s][%s] net_pnl=$%+,.2f trades=%d",
                         symbol, config["name"], cell["net_pnl"], cell["trades"])
            cells.append(cell)
    runtime_seconds = time.time() - start

    # Refuse to aggregate if any cell errored — masking failures in the sweep
    # would corrupt the comparison silently. Dump diagnostics + return rc=4
    # so the caller can route to debug rather than retry blindly.
    errored = [c for c in cells if c.get("error") is not None]
    if errored:
        log.error("Sweep had %d errored cells; refusing to aggregate. Errors: %s",
                  len(errored),
                  [(c["symbol"], c["config"], c["error"]) for c in errored])
        _atomic_write_json(
            os.path.join(out_dir, "sweep_errors.json"),
            {
                "errored_cells": errored,
                "successful_cells": [c for c in cells if c.get("error") is None],
            },
        )
        return 4

    agg = _aggregate_results(cells)

    log.info("Computing per-symbol data ranges from ohlcv.db...")
    ranges = _per_symbol_data_ranges(OHLCV_DB, symbols, cutoff_ms)
    leakage_check = _verify_no_leakage(ranges, cutoff_ms)
    log.info("Leakage check: %s", leakage_check)

    log.info("Hashing ohlcv.db...")
    ohlcv_sha = _sha256_file(OHLCV_DB)
    code_commit = _resolve_git_commit()

    manifest = _build_manifest(
        agg=agg, cutoff=cutoff, cutoff_ms=cutoff_ms,
        ohlcv_sha=ohlcv_sha, code_commit=code_commit,
        ranges=ranges, runtime_seconds=runtime_seconds,
        leakage_check=leakage_check, symbols=symbols,
        symbol_overrides=app_config.get("symbol_overrides"),
    )

    report_md = _build_report(
        agg=agg, cells=cells, cutoff=cutoff,
        ranges=ranges, runtime_seconds=runtime_seconds, symbols=symbols,
    )

    log.info("Decision flags: %s", agg["decision_flags"])

    # Sanity check is fail-closed: if no_detector wins, do NOT write the
    # canonical artefacts. Dump halted_summary.json with the diagnostic
    # bundle for post-mortem and return rc=3.
    if agg["decision_flags"]["sanity_check"]:
        log.error("SANITY CHECK FIRED: no_detector wins on pre-holdout window. "
                  "Refusing to write canonical artefacts. See halted_summary.json.")
        _atomic_write_json(
            os.path.join(out_dir, "halted_summary.json"),
            {
                "reason": "sanity_check_fired",
                "agg": agg,
                "manifest": manifest,
            },
        )
        return 3

    # Order: report + manifest first, regime_params.json LAST as the
    # durability marker for downstream consumers.
    _atomic_write_text(os.path.join(out_dir, "regime_report.md"), report_md)
    _atomic_write_json(os.path.join(out_dir, "regime_manifest.json"), manifest)
    _write_regime_params(os.path.join(out_dir, "regime_params.json"), agg)

    log.info("Artefacts written to %s", out_dir)
    log.info("  regime_report.md     — human-readable side-by-side + caveats")
    log.info("  regime_manifest.json — cutoff, hashes, decision flags, no-leakage proof")
    log.info("  regime_params.json   — winner config (durability marker, written last)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
