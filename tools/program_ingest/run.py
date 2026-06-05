"""T0 orchestrator: enumerar universo → universe.json → bulk download → coverage.json.

Usage: python -m tools.program_ingest.run [--universe-only]
"""
from __future__ import annotations
import json
import pathlib
import sys
import time

from .constants import OUTPUT_DIR, PROGRAM_DB, WINDOW_END, WINDOW_START
from .download import backfill_ingest_log, download_symbol, init_db
from .universe import enumerate_universe


def main(argv: list[str]) -> int:
    out = pathlib.Path(OUTPUT_DIR)
    out.mkdir(parents=True, exist_ok=True)

    universe_path = out / "universe.json"
    if universe_path.exists():
        universe = json.loads(universe_path.read_text(encoding="utf-8"))
        print(f"universe.json existente ({universe['counts']}), reusado")
    else:
        print("enumerando listing de Binance Vision...")
        universe = enumerate_universe()
        universe["enumerated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        universe_path.write_text(
            json.dumps(universe, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(f"universe.json escrito: {universe['counts']}")

    if "--universe-only" in argv:
        return 0

    init_db(PROGRAM_DB)
    backfilled = backfill_ingest_log(PROGRAM_DB)   # resume across pre-ledger runs
    if backfilled:
        print(f"ingest_log backfilled: {backfilled} (symbol, month) entries")
    panel = universe["panel"]
    coverage: dict[str, dict] = {}
    t0 = time.time()
    for i, (sym, meta) in enumerate(sorted(panel.items())):
        months = [m for m in _month_range(meta["first_month"], meta["last_month"])]
        per_month = download_symbol(sym, months)
        gaps = [m for m, n in per_month.items() if n == 0]
        coverage[sym] = {
            "rows": sum(per_month.values()),
            "months_fetched": sum(1 for n in per_month.values() if n > 0),
            "gap_months": gaps,
        }
        elapsed = time.time() - t0
        print(f"[{i + 1}/{len(panel)}] {sym}: {coverage[sym]['rows']} rows "
              f"({len(gaps)} gaps) — {elapsed:.0f}s", file=sys.stderr)
    (out / "coverage.json").write_text(
        json.dumps({
            "window": [WINDOW_START, WINDOW_END],
            "db": PROGRAM_DB,
            "symbols": coverage,
            "totals": {
                "symbols": len(coverage),
                "rows": sum(c["rows"] for c in coverage.values()),
            },
        }, indent=2, sort_keys=True), encoding="utf-8",
    )
    print(f"coverage.json escrito: {len(coverage)} símbolos")
    return 0


def _month_range(first: str, last: str) -> list[str]:
    fy, fm = int(first[:4]), int(first[5:7])
    ly, lm = int(last[:4]), int(last[5:7])
    res, y, m = [], fy, fm
    while (y, m) <= (ly, lm):
        res.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return res


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
