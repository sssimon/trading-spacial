"""Consume tune_results.json, classify each (symbol, direction) via tiers,
emit a patched config.json. Human must review the diff before committing.

Spec: docs/superpowers/specs/es/2026-04-20-per-direction-atr-params-design.md §8
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from btc_scanner import _classify_tune_result  # noqa: E402


def _triplet(best: dict) -> dict:
    return {k: best[k] for k in ("atr_sl_mult", "atr_tp_mult", "atr_be_mult")}


def build_override_for_symbol(long_entry: dict, short_entry: dict) -> dict | None:
    """Decide the override block for one symbol given its tuning results.

    Returns None if both directions are disabled (→ remove symbol from overrides).
    """
    def tier_of(entry):
        best = (entry or {}).get("best")
        if not best:
            return "disabled"
        return _classify_tune_result(best.get("N", 0), best.get("pf"))

    long_tier = tier_of(long_entry)
    short_tier = tier_of(short_entry)

    long_best = (long_entry or {}).get("best")
    short_best = (short_entry or {}).get("best")

    # Both disabled → symbol removed
    if long_tier == "disabled" and short_tier == "disabled":
        return None

    # Both dedicated → form 2 {long: {...}, short: {...}}
    if long_tier == "dedicated" and short_tier == "dedicated":
        return {"long": _triplet(long_best), "short": _triplet(short_best)}

    # Both fallback → form 1 (flat) with the triplet of the higher-pnl direction
    if long_tier == "fallback" and short_tier == "fallback":
        winner = long_best if long_best["pnl"] >= short_best["pnl"] else short_best
        return _triplet(winner)

    # Mixed — form 4 (flat + per-dir partial) or form 3 (null disable)
    block: dict = {}
    candidates = [(long_tier, long_best, "long"), (short_tier, short_best, "short")]
    non_disabled = [(t, b, d) for t, b, d in candidates if t != "disabled" and b is not None]
    base = max(non_disabled, key=lambda x: x[1]["pnl"])
    block.update(_triplet(base[1]))

    for tier, best, d in candidates:
        if tier == "dedicated" and d != base[2]:
            block[d] = _triplet(best)
        elif tier == "disabled":
            block[d] = None

    return block


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tune-results", required=True)
    ap.add_argument("--base-config", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    tune = json.loads(Path(args.tune_results).read_text())
    cfg = json.loads(Path(args.base_config).read_text())

    overrides = {}
    removed = []
    for sym, per_dir in tune["results"].items():
        block = build_override_for_symbol(per_dir.get("long", {}), per_dir.get("short", {}))
        if block is None:
            removed.append(sym)
        else:
            overrides[sym] = block

    cfg["symbol_overrides"] = overrides

    out = Path(args.output)
    out.write_text(json.dumps(cfg, indent=2))

    print(f"Wrote {out}")
    print(f"  {len(overrides)} symbols with overrides")
    if removed:
        print(f"  {len(removed)} symbols REMOVED (both directions disabled): {removed}")


if __name__ == "__main__":
    main()
