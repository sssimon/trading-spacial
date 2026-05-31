"""CLI driver: Pass 1 (base stream) -> Pass 2 (replay per engine/slider) ->
Pass 3 (gate) -> write report.md + results.json + derivation_audit.md.

Read-only on OHLCV; never touches signals.db or production state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess

from tools.ks_stress_replay.base_stream import (
    generate_base_stream, flag_bankruptcies, HOLDOUT_CUTOFF, CURATED_SYMBOLS,
)
from tools.ks_stress_replay.overlays import NoneOverlay, V1Overlay, V2Overlay
from tools.ks_stress_replay.replay import replay
from tools.ks_stress_replay.metrics import evaluate_gate

SLIDER_GRID = [30, 50, 70]
CAPITAL_BASE = 1000.0
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def assemble_frontier(results: dict) -> tuple[dict, dict]:
    """Split a flat {engine_label -> point} dict into (v1_point, {slider -> point})."""
    v1_point = results["v1"]
    v2_points = {
        int(label.split("@")[1]): pt
        for label, pt in results.items()
        if label.startswith("v2@")
    }
    return v1_point, v2_points


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
        ).decode().strip()
    except Exception:
        return "unknown"


def run(out_dir: str, symbols: list[str], ran_at_iso: str) -> dict:
    base = generate_base_stream(symbols)
    bankruptcies = flag_bankruptcies(base)

    results: dict = {}
    results["none"] = replay(base, NoneOverlay(), CAPITAL_BASE)
    results["v1"] = replay(base, V1Overlay({"kill_switch": {}}), CAPITAL_BASE)
    for slider in SLIDER_GRID:
        ov = V2Overlay({}, slider=float(slider), capital_base=CAPITAL_BASE)
        results[f"v2@{slider}"] = replay(base, ov, CAPITAL_BASE)

    v1_point, v2_points = assemble_frontier(results)
    verdict, winning_slider = evaluate_gate(v1_point, v2_points)

    payload = {
        "ran_at": ran_at_iso,
        "cutoff": HOLDOUT_CUTOFF.isoformat(),
        "capital_base": CAPITAL_BASE,
        "slider_grid": SLIDER_GRID,
        "symbols": symbols,
        "bankruptcies": bankruptcies,
        "results": results,
        "verdict": verdict,
        "winning_slider": winning_slider,
        "code_commit": _git_commit(),
        "ohlcv_sha256": _sha256(os.path.join(REPO_ROOT, "data", "ohlcv.db")),
    }

    os.makedirs(out_dir, exist_ok=True)
    _atomic_write_json(os.path.join(out_dir, "results.json"), payload)
    _atomic_write_text(os.path.join(out_dir, "report.md"), _render_report(payload))
    _atomic_write_text(
        os.path.join(out_dir, "derivation_audit.md"), _render_audit(payload),
    )
    return payload


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


def _render_report(p: dict) -> str:
    lines = [
        "# KS Stress-Replay v1 vs v2 — report",
        "",
        f"- Ran at: {p['ran_at']}",
        f"- Cutoff (holdout-safe): {p['cutoff']}",
        f"- Verdict: **{p['verdict']}**"
        + (f" (winning slider: {p['winning_slider']})" if p["winning_slider"] is not None else ""),
        f"- Bankruptcies (flagged, post-bankrupt trades truncated): {p['bankruptcies'] or 'none'}",
        "",
        "> Absolute P&L is NOT a baseline (pre-#223/#224 inflation, NON-NEGOTIABLE #5).",
        "> Only the RELATIVE v1-vs-v2 comparison on the shared base stream is the conclusion.",
        "",
        "| engine | max_dd | total_pnl | taken | skipped | engagements |",
        "|---|---|---|---|---|---|",
    ]
    for label in ["none", "v1"] + [f"v2@{s}" for s in p["slider_grid"]]:
        r = p["results"][label]
        lines.append(
            f"| {label} | {r['max_dd']:.4f} | {r['total_pnl']:.2f} | "
            f"{r['taken']} | {r['skipped']} | {r['engagements']} |"
        )
    return "\n".join(lines) + "\n"


def _render_audit(p: dict) -> str:
    return (
        "# KS Stress-Replay — derivation audit\n\n"
        f"- ran_at: {p['ran_at']}\n"
        f"- cutoff: {p['cutoff']} (bars strictly < cutoff; NON-NEGOTIABLE #3)\n"
        f"- capital_base: {p['capital_base']} (identical across engines; cancels in relative comparison)\n"
        f"- slider_grid: {p['slider_grid']}\n"
        f"- symbols: {p['symbols']}\n"
        f"- bankruptcies: {p['bankruptcies']}\n"
        f"- code_commit: {p['code_commit']}\n"
        f"- ohlcv_sha256: {p['ohlcv_sha256']}\n"
        f"- verdict: {p['verdict']}; winning_slider: {p['winning_slider']}\n\n"
        "Gate (pre-registered): STRONG=DD margin (>=3pp OR >=15% rel) AND PnL>=v1; "
        "PASS=same DD margin AND PnL within 10% band of v1; else FAIL.\n"
        "regime_score=None (NEUTRAL) for v2 replay — per-bar regime is Approach B, out of scope.\n"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="KS stress-replay v1 vs v2")
    ap.add_argument("--out-dir", default=os.path.join(
        REPO_ROOT, "data", "retune", "2026-05-31-ks-stress-replay"))
    ap.add_argument("--symbols", nargs="*", default=CURATED_SYMBOLS)
    ap.add_argument("--ran-at", required=True,
                    help="ISO timestamp (passed in; Date.now is not available in scripts)")
    args = ap.parse_args()
    payload = run(args.out_dir, args.symbols, args.ran_at)
    print(f"verdict={payload['verdict']} winning_slider={payload['winning_slider']}")


if __name__ == "__main__":
    main()
