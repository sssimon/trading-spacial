#!/usr/bin/env python3
"""
Auto-Tune — Walk-forward parameter optimization for the trading portfolio.

Usage:
    python auto_tune.py                        # full optimization
    python auto_tune.py --symbol DOGEUSDT      # single symbol
    python auto_tune.py --apply                # apply config_proposed.json
    python auto_tune.py --dry-run              # show what would change
"""

import os
import sys
import json
import time
import random
import shutil
import sqlite3
import argparse
import logging
import itertools
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta

import copy
import requests

import pandas as pd
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
log = logging.getLogger("auto_tune")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

DB_FILE = os.path.join(SCRIPT_DIR, "signals.db")


def save_tune_result(results: list, report_md: str, status: str = "pending"):
    """Save tune results to DB."""
    changes = [r for r in results if r.get("recommendation") == "CHANGE"]
    con = sqlite3.connect(DB_FILE)
    con.execute("""
        CREATE TABLE IF NOT EXISTS tune_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            results_json TEXT,
            report_md TEXT,
            applied_ts TEXT,
            changes_count INTEGER DEFAULT 0
        )
    """)
    now = datetime.now(timezone.utc).isoformat()
    applied_ts = now if status == "applied" else None
    con.execute(
        "INSERT INTO tune_results (ts, status, results_json, report_md, applied_ts, changes_count) VALUES (?, ?, ?, ?, ?, ?)",
        (now, status, json.dumps(results, default=str), report_md, applied_ts, len(changes))
    )
    con.commit()
    con.close()


GRID = {
    "atr_sl_mult": [0.5, 0.7, 1.0, 1.2, 1.5, 2.0, 2.5],
    "atr_tp_mult": [2.0, 3.0, 4.0, 5.0, 6.0],
    "atr_be_mult": [1.5, 2.0, 2.5],
}

MIN_IMPROVEMENT_PCT = 15.0
MIN_TRADES = 50
MIN_PF_VALIDATE = 1.1
TOP_N_TO_VALIDATE = 5


def calculate_periods(today=None):
    """Return (train_start, train_end, val_start, val_end) as UTC datetimes.

    Train window : today - 15 months  →  today - 3 months  (~12 months)
    Validate window: today - 3 months  →  today             (~3 months)
    """
    if today is None:
        today = datetime.now(tz=timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    train_start = today - relativedelta(months=15)
    train_end = today - relativedelta(months=3)
    val_start = train_end
    val_end = today

    return train_start, train_end, val_start, val_end


def generate_combos() -> list:
    """Return all parameter combinations from GRID as a list of dicts.

    Total = 7 x 5 x 3 = 105 combinations.
    """
    keys = list(GRID.keys())
    values = [GRID[k] for k in keys]
    combos = [dict(zip(keys, combo)) for combo in itertools.product(*values)]
    return combos


def should_recommend(current_pnl: float, proposed_pnl: float, total_trades: int, pf_validate: float) -> bool:
    """Return True only when ALL acceptance criteria are met.

    Criteria:
      1. pf_validate >= MIN_PF_VALIDATE (1.1)
      2. total_trades >= MIN_TRADES (50)
      3. If current_pnl <= 0: proposed_pnl must be > 0
         If current_pnl > 0:  improvement must be >= MIN_IMPROVEMENT_PCT (15%)
    """
    if pf_validate < MIN_PF_VALIDATE:
        return False

    if total_trades < MIN_TRADES:
        return False

    if current_pnl <= 0:
        return proposed_pnl > 0

    improvement_pct = (proposed_pnl - current_pnl) / current_pnl * 100.0
    return improvement_pct >= MIN_IMPROVEMENT_PCT


def load_config() -> dict:
    """Delegate to btc_api.load_config() so we pick up config.defaults.json
    (symbol_overrides) + config.secrets.json + legacy config.json layering.
    """
    import btc_api
    return btc_api.load_config()


def get_current_params(symbol: str, config: dict) -> dict:
    """Extract ATR parameters for a symbol from config with defaults."""
    overrides = config.get("symbol_overrides", {})
    sym_cfg = overrides.get(symbol, {})
    if not isinstance(sym_cfg, dict):
        sym_cfg = {}
    return {
        "atr_sl_mult": sym_cfg.get("atr_sl_mult", 1.0),
        "atr_tp_mult": sym_cfg.get("atr_tp_mult", 4.0),
        "atr_be_mult": sym_cfg.get("atr_be_mult", 1.5),
    }


def get_portfolio_symbols(config: dict) -> list:
    """Return active portfolio symbols (not disabled in overrides)."""
    from btc_scanner import DEFAULT_SYMBOLS
    overrides = config.get("symbol_overrides", {})
    active = []
    for sym in DEFAULT_SYMBOLS:
        sym_cfg = overrides.get(sym, {})
        # A symbol is disabled if its override is exactly False
        if sym_cfg is False:
            continue
        active.append(sym)
    return active


def _slice_below_cutoff(df: pd.DataFrame, cutoff_naive: datetime, symbol: str, name: str) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    if df.index.dtype.kind != "M":
        return df
    sliced = df[df.index < cutoff_naive]
    if not sliced.empty:
        assert sliced.index.max() < cutoff_naive, (
            f"no-leakage violation: {symbol} {name} max ts "
            f"{sliced.index.max()} >= cutoff {cutoff_naive}"
        )
    return sliced


def run_backtest_with_params(symbol: str, params: dict,
                             sim_start: datetime, sim_end: datetime,
                             *, cutoff: datetime = None):
    """Run a backtest for a symbol with given ATR params over a date range.

    When ``cutoff`` is provided, all OHLCV bars with timestamp ``>= cutoff``
    are stripped before the simulator runs, and an assertion verifies the
    invariant (max bar time strictly < cutoff). The cutoff applies to F&G
    and funding rate frames as well, since those are also consumed by the
    regime detector. Defaults to ``None`` for backward compatibility — the
    legacy code path is byte-identical.

    Returns (trades, metrics).
    """
    from backtest import (
        get_cached_data, simulate_strategy, calculate_metrics,
        get_historical_fear_greed, get_historical_funding_rate,
    )

    # Load data (cached)
    df1h = get_cached_data(symbol, "1h", start_date=sim_start - relativedelta(months=2))
    df4h = get_cached_data(symbol, "4h", start_date=sim_start - relativedelta(months=2))
    df5m = get_cached_data(symbol, "5m", start_date=sim_start - relativedelta(months=1))
    df1d = get_cached_data(symbol, "1d", start_date=sim_start - relativedelta(months=12))

    df_fng = get_historical_fear_greed()
    df_funding = get_historical_funding_rate()

    if cutoff is not None:
        cutoff_naive = cutoff.replace(tzinfo=None) if cutoff.tzinfo else cutoff
        df1h = _slice_below_cutoff(df1h, cutoff_naive, symbol, "df1h")
        df4h = _slice_below_cutoff(df4h, cutoff_naive, symbol, "df4h")
        df5m = _slice_below_cutoff(df5m, cutoff_naive, symbol, "df5m")
        df1d = _slice_below_cutoff(df1d, cutoff_naive, symbol, "df1d")
        df_fng = _slice_below_cutoff(df_fng, cutoff_naive, symbol, "df_fng")
        df_funding = _slice_below_cutoff(df_funding, cutoff_naive, symbol, "df_funding")

    if df1h.empty or df4h.empty or df5m.empty:
        return [], {"error": "No data", "total_trades": 0, "net_pnl": 0, "profit_factor": 0}

    trades, equity_curve = simulate_strategy(
        df1h, df4h, df5m, symbol,
        sl_mode="atr",
        atr_sl_mult=params["atr_sl_mult"],
        atr_tp_mult=params["atr_tp_mult"],
        atr_be_mult=params["atr_be_mult"],
        df1d=df1d,
        sim_start=sim_start,
        sim_end=sim_end,
        df_fng=df_fng,
        df_funding=df_funding,
    )

    if not trades:
        return [], {"error": "No trades", "total_trades": 0, "net_pnl": 0, "profit_factor": 0}

    metrics = calculate_metrics(trades, equity_curve)
    return trades, metrics


def optimize_symbol(symbol: str, config: dict, today=None, *, cutoff: datetime = None) -> dict:
    """Walk-forward optimization for a single symbol.

    1. Calculate train/validate periods
    2. Baseline: run current params on VALIDATE
    3. Grid search 105 combos on TRAIN, sort by P&L, take top 5
    4. Run top 5 on VALIDATE
    5. Check should_recommend for each
    6. Return result dict

    ``cutoff`` (optional): no-leakage upper bound propagated to every
    backtest call. When set, runners drop bars ``>= cutoff`` before
    simulation and assert the invariant. The pre-holdout retune wrapper
    sets this to the holdout start date.
    """
    train_start, train_end, val_start, val_end = calculate_periods(today)
    current_params = get_current_params(symbol, config)

    # Baseline: current params on validate period
    log.info(f"  {symbol}: baseline on validate ({val_start.date()} -> {val_end.date()})...")
    _, baseline_metrics = run_backtest_with_params(symbol, current_params, val_start, val_end, cutoff=cutoff)
    current_val_pnl = baseline_metrics.get("net_pnl", 0)

    # Grid search on train period
    combos = generate_combos()
    log.info(f"  {symbol}: grid search ({len(combos)} combos) on train ({train_start.date()} -> {train_end.date()})...")

    train_results = []
    for combo in combos:
        _, metrics = run_backtest_with_params(symbol, combo, train_start, train_end, cutoff=cutoff)
        train_results.append({
            "params": combo,
            "pnl": metrics.get("net_pnl", 0),
            "trades": metrics.get("total_trades", 0),
            "pf": metrics.get("profit_factor", 0),
        })

    # Sort by P&L descending, take top N
    train_results.sort(key=lambda x: x["pnl"], reverse=True)
    top_candidates = train_results[:TOP_N_TO_VALIDATE]

    if not top_candidates or top_candidates[0]["pnl"] <= 0:
        return {
            "symbol": symbol,
            "current_params": current_params,
            "current_val_pnl": current_val_pnl,
            "proposed_params": None,
            "proposal_detail": None,
            "recommendation": "NO_DATA",
        }

    # Validate top candidates
    log.info(f"  {symbol}: validating top {len(top_candidates)} candidates...")
    best_proposal = None

    for candidate in top_candidates:
        params = candidate["params"]
        trades_val, val_metrics = run_backtest_with_params(symbol, params, val_start, val_end, cutoff=cutoff)
        val_pnl = val_metrics.get("net_pnl", 0)
        val_pf = val_metrics.get("profit_factor", 0)
        val_trades = val_metrics.get("total_trades", 0)
        total_trades = candidate["trades"] + val_trades

        if should_recommend(current_val_pnl, val_pnl, total_trades, val_pf):
            improvement_pct = 0.0
            if current_val_pnl > 0:
                improvement_pct = (val_pnl - current_val_pnl) / current_val_pnl * 100.0
            elif current_val_pnl <= 0 and val_pnl > 0:
                improvement_pct = 100.0  # from negative/zero to positive

            detail = {
                "params": params,
                "val_pnl": val_pnl,
                "val_pf": val_pf,
                "train_pnl": candidate["pnl"],
                "val_trades": val_trades,
                "total_trades": total_trades,
                "improvement_pct": round(improvement_pct, 1),
            }

            if best_proposal is None or val_pnl > best_proposal["val_pnl"]:
                best_proposal = detail

    if best_proposal:
        return {
            "symbol": symbol,
            "current_params": current_params,
            "current_val_pnl": current_val_pnl,
            "proposed_params": best_proposal["params"],
            "proposal_detail": best_proposal,
            "recommendation": "CHANGE",
        }

    return {
        "symbol": symbol,
        "current_params": current_params,
        "current_val_pnl": current_val_pnl,
        "proposed_params": None,
        "proposal_detail": None,
        "recommendation": "KEEP",
    }


def generate_report(results: list, elapsed_seconds: float) -> str:
    """Generate a Markdown report summarizing auto-tune results."""
    changes = [r for r in results if r.get("recommendation") == "CHANGE"]
    keeps = [r for r in results if r.get("recommendation") in ("KEEP", "NO_DATA", "ERROR")]

    lines = []
    lines.append("# Auto-Tune Report")
    lines.append("")
    lines.append(f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Symbols analyzed:** {len(results)}")
    lines.append(f"**Changes recommended:** {len(changes)}")
    lines.append(f"**Time elapsed:** {elapsed_seconds:.0f}s")
    lines.append("")

    if changes:
        lines.append("## Proposed Changes")
        lines.append("")
        lines.append("| Symbol | Curr SL | Curr TP | Curr BE | New SL | New TP | New BE | Val P&L Curr | Val P&L New | PF Val | Improvement |")
        lines.append("|--------|---------|---------|---------|--------|--------|--------|-------------|-------------|--------|-------------|")
        for r in changes:
            cp = r["current_params"]
            pp = r["proposed_params"]
            d = r["proposal_detail"]
            lines.append(
                f"| {r['symbol']} "
                f"| {cp['atr_sl_mult']} | {cp['atr_tp_mult']} | {cp['atr_be_mult']} "
                f"| {pp['atr_sl_mult']} | {pp['atr_tp_mult']} | {pp['atr_be_mult']} "
                f"| ${r['current_val_pnl']:.0f} | ${d['val_pnl']:.0f} "
                f"| {d['val_pf']:.2f} | +{d['improvement_pct']:.1f}% |"
            )
        lines.append("")

    if keeps:
        lines.append("## No Changes")
        lines.append("")
        lines.append("| Symbol | SL | TP | BE | Reason |")
        lines.append("|--------|----|----|----|----- --|")
        for r in keeps:
            cp = r.get("current_params", {})
            reason = r.get("recommendation", "KEEP")
            lines.append(
                f"| {r.get('symbol', '?')} "
                f"| {cp.get('atr_sl_mult', '-')} | {cp.get('atr_tp_mult', '-')} | {cp.get('atr_be_mult', '-')} "
                f"| {reason} |"
            )
        lines.append("")

    return "\n".join(lines)


def build_telegram_message(results: list) -> str:
    """Build a short Telegram summary of auto-tune results."""
    changes = [r for r in results if r.get("recommendation") == "CHANGE"]
    total = len(results)

    lines = []
    lines.append(f"Auto-Tune: {total} symbols analyzed, {len(changes)} changes recommended")

    if changes:
        lines.append("")
        for r in changes:
            d = r["proposal_detail"]
            pp = r["proposed_params"]
            lines.append(
                f"  {r['symbol']}: SL={pp['atr_sl_mult']}, TP={pp['atr_tp_mult']}, "
                f"BE={pp['atr_be_mult']} (+{d['improvement_pct']:.0f}%)"
            )
        lines.append("")
        lines.append("Apply: python auto_tune.py --apply")
    else:
        lines.append("No parameter changes needed.")

    return "\n".join(lines)


def send_telegram(message: str, config: dict):
    """Send a message via Telegram Bot API. Skip if not configured."""
    token = config.get("telegram_bot_token", "")
    chat_id = config.get("telegram_chat_id", "")
    if not token or not chat_id:
        log.warning("Telegram not configured (missing token or chat_id), skipping notification")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": chat_id, "text": message}, timeout=15)
        if r.status_code == 200:
            log.info("Telegram notification sent")
        else:
            log.warning(f"Telegram API returned {r.status_code}: {r.text}")
    except Exception as e:
        log.warning(f"Failed to send Telegram message: {e}")


def write_config_proposed(results: list, config: dict, output_dir: str = None) -> str:
    """Write config_proposed.json with updated symbol_overrides. Return path or None."""
    changes = [r for r in results if r.get("recommendation") == "CHANGE"]
    if not changes:
        return None

    proposed = copy.deepcopy(config)
    if "symbol_overrides" not in proposed:
        proposed["symbol_overrides"] = {}

    for r in changes:
        sym = r["symbol"]
        pp = r["proposed_params"]
        if sym not in proposed["symbol_overrides"]:
            proposed["symbol_overrides"][sym] = {}
        if not isinstance(proposed["symbol_overrides"][sym], dict):
            proposed["symbol_overrides"][sym] = {}
        proposed["symbol_overrides"][sym]["atr_sl_mult"] = pp["atr_sl_mult"]
        proposed["symbol_overrides"][sym]["atr_tp_mult"] = pp["atr_tp_mult"]
        proposed["symbol_overrides"][sym]["atr_be_mult"] = pp["atr_be_mult"]

    out_dir = output_dir or SCRIPT_DIR
    proposed_path = os.path.join(out_dir, "config_proposed.json")
    with open(proposed_path, "w", encoding="utf-8") as f:
        json.dump(proposed, f, indent=2, ensure_ascii=False)
    log.info(f"Wrote config_proposed.json: {proposed_path}")
    return proposed_path


def apply_config(config_path: str, proposed_path: str, confirm: bool = False) -> str:
    """Apply proposed config over current config.

    Shows diff, asks for confirmation (unless confirm=True), creates backup,
    writes proposed to config_path. Returns backup path or None.
    """
    if not os.path.exists(proposed_path):
        log.error(f"Proposed config not found: {proposed_path}")
        return None

    with open(proposed_path, "r", encoding="utf-8") as f:
        proposed = json.load(f)

    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            current = json.load(f)
    else:
        current = {}

    # Show diff
    log.info("Changes to apply:")
    proposed_overrides = proposed.get("symbol_overrides", {})
    current_overrides = current.get("symbol_overrides", {})
    for sym in proposed_overrides:
        new_vals = proposed_overrides[sym]
        old_vals = current_overrides.get(sym, {})
        if not isinstance(old_vals, dict):
            old_vals = {}
        if new_vals != old_vals:
            log.info(f"  {sym}: {old_vals} -> {new_vals}")

    if not confirm:
        response = input("Apply these changes? [y/N]: ").strip().lower()
        if response != "y":
            log.info("Cancelled.")
            return None

    # Create backup
    backup_path = config_path.replace(
        ".json",
        f"_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    if os.path.exists(config_path):
        shutil.copy2(config_path, backup_path)
        log.info(f"Backup saved: {backup_path}")
    else:
        # No existing config to back up; create empty backup
        with open(backup_path, "w") as f:
            json.dump({}, f)

    # Write proposed as new config
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(proposed, f, indent=2, ensure_ascii=False)
    log.info(f"Config updated: {config_path}")

    return backup_path


DEFAULT_SEED = 42


def initialize_seed(config: dict) -> int:
    """Seed Python ``random`` and NumPy from ``config['auto_tune']['seed']``.

    Defensive: the current grid-search code path is fully deterministic
    (full enumeration via itertools.product) and consumes no RNG, so this
    has no behavioral effect today. It exists to blunt the impact of any
    future change that introduces RNG (sampling, tie-breaking, shuffles)
    by guaranteeing a reproducible seed is in scope before optimization
    begins. Returns the seed used so callers can record it.
    """
    seed = int(config.get("auto_tune", {}).get("seed", DEFAULT_SEED))
    random.seed(seed)
    np.random.seed(seed)
    return seed


def main():
    parser = argparse.ArgumentParser(
        description="Auto-Tune: Walk-forward parameter optimization",
        epilog="Cron: 0 3 1 * * cd /path/to/trading-spacial && python auto_tune.py"
    )
    parser.add_argument("--symbol", type=str, help="Optimize single symbol")
    parser.add_argument("--apply", action="store_true", help="Apply config_proposed.json")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change")
    parser.add_argument(
        "--max-date",
        type=str,
        default=None,
        help=(
            "ISO date (YYYY-MM-DD, UTC) treated as today's reference. "
            "Walk-forward windows derive from this and OHLCV bars >= this "
            "date are excluded from every backtest call (no-leakage "
            "guarantee). Default: actual current UTC date."
        ),
    )
    args = parser.parse_args()

    if args.apply:
        # Try config_proposed.json first (legacy), then DB
        proposed_path = os.path.join(SCRIPT_DIR, "config_proposed.json")
        config_path = os.path.join(SCRIPT_DIR, "config.json")

        if os.path.exists(proposed_path):
            backup = apply_config(config_path, proposed_path)
            if backup:
                print(f"Config updated from file. Backup: {backup}")
        else:
            # Check DB for pending
            try:
                con = sqlite3.connect(DB_FILE)
                con.row_factory = sqlite3.Row
                row = con.execute("SELECT * FROM tune_results WHERE status='pending' ORDER BY id DESC LIMIT 1").fetchone()
                con.close()
                if row and row["results_json"]:
                    results = json.loads(row["results_json"])
                    config = load_config()
                    proposed_path_tmp = write_config_proposed(results, config)
                    if proposed_path_tmp:
                        backup = apply_config(config_path, proposed_path_tmp, confirm=False)
                        if backup:
                            # Update DB status
                            con2 = sqlite3.connect(DB_FILE)
                            con2.execute("UPDATE tune_results SET status='applied', applied_ts=? WHERE id=?",
                                        (datetime.now(timezone.utc).isoformat(), row["id"]))
                            con2.commit()
                            con2.close()
                            os.remove(proposed_path_tmp)
                            print(f"Config updated from DB. Backup: {backup}")
                else:
                    print("No pending tune results found.")
            except Exception as e:
                print(f"Error: {e}")
        return

    config = load_config()
    symbols = [args.symbol.upper()] if args.symbol else get_portfolio_symbols(config)

    seed = initialize_seed(config)

    cutoff = None
    today = None
    if args.max_date:
        cutoff = datetime.fromisoformat(args.max_date).replace(tzinfo=timezone.utc)
        today = cutoff

    print(f"Auto-Tune: {len(symbols)} symbols, walk-forward optimization")
    print(f"Grid: {len(generate_combos())} combos per symbol")
    print(f"Seed: {seed}")
    if cutoff is not None:
        print(f"Cutoff (--max-date): {cutoff.isoformat()} (no-leakage assertions enabled)")

    start_time = time.time()
    results = []

    for sym in symbols:
        try:
            result = optimize_symbol(sym, config, today=today, cutoff=cutoff)
            results.append(result)
            rec = result["recommendation"]
            if rec == "CHANGE":
                d = result["proposal_detail"]
                print(f"  {sym}: CAMBIAR -> mejora +{d['improvement_pct']}%")
            else:
                print(f"  {sym}: mantener params actuales")
        except Exception as e:
            log.error(f"  {sym}: ERROR - {e}")
            results.append({
                "symbol": sym, "recommendation": "ERROR",
                "current_params": get_current_params(sym, config),
                "current_val_pnl": 0, "proposed_params": None, "proposal_detail": None,
            })

    elapsed = time.time() - start_time
    report = generate_report(results, elapsed)

    auto_approve = config.get("auto_approve_tune", True)

    if not args.dry_run:
        # Save report file (always)
        report_dir = os.path.join(SCRIPT_DIR, "data", "backtest")
        os.makedirs(report_dir, exist_ok=True)
        report_date = datetime.now().strftime("%Y%m%d")
        report_path = os.path.join(report_dir, f"tune_report_{report_date}.md")
        with open(report_path, "w") as f:
            f.write(report)
        print(f"\nReport: {report_path}")

        if auto_approve:
            # Auto mode: apply changes silently
            if any(r.get("recommendation") == "CHANGE" for r in results):
                proposed_path = write_config_proposed(results, config)
                if proposed_path:
                    cfg_path = os.path.join(SCRIPT_DIR, "config.json")
                    apply_config(cfg_path, proposed_path, confirm=True)
                    os.remove(proposed_path)
            save_tune_result(results, report, status="applied")
            telegram_msg = build_telegram_message(results)
            telegram_msg += "\n\n_Modo auto-approve: cambios aplicados automaticamente._"
            send_telegram(telegram_msg, config)
        else:
            # Manual mode: save as pending for frontend approval
            save_tune_result(results, report, status="pending")
            telegram_msg = build_telegram_message(results)
            telegram_msg += "\n\n_Revisar y aprobar en el dashboard._"
            send_telegram(telegram_msg, config)
    else:
        print("\n--- DRY RUN ---")
        print(report)


if __name__ == "__main__":
    main()
