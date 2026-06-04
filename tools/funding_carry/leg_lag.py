"""Funding-carry execution-realism v0.2 — Unidad 2 (spec 2026-06-03 REV 2.1 §4).

DESCRIPTIVE ONLY — NO VERDICT, by design: sigma_T x NOTIONAL is a 2nd-moment
quantity, and a risk bound is only comparable against a risk budget (2nd moment),
which does not exist yet. Renaming a drag to a bound does not co-locate the types
(Axiom-0 / Richter). This module measures and tabulates; interpretation waits.

One-shot, paper-only. Approximations declared in spec §6: sqrt(T) sub-minute
extrapolation (§6.2), T is an ASSUMED window (§6.3), mark-basis != executable
basis (§6.4). Fail-LOUD: a short klines series raises FetchFailed (never sigma
over 25h labeled 30d)."""
from __future__ import annotations
import json
import logging
import math
import os
import statistics
from datetime import datetime, timezone
from .constants import (SHADOW_SYMBOLS, NOTIONAL, LEG_LAG_DAYS, LEG_LAG_T_SWEEP,
                        SPOT_KLINES_1M, FAPI_MARK_KLINES,
                        EXEC_REALISM_OUTPUT_DIR, EXEC_REALISM_VERSION)

log = logging.getLogger("funding_carry.leg_lag")


def basis_sigma_1m(spot_closes: list[tuple[int, float]],
                   perp_closes: list[tuple[int, float]]) -> float:
    """std of per-minute changes of the relative basis (perp-spot)/spot, computed
    over timestamps present in BOTH series (inner join). Returns 0.0 when fewer
    than 2 deltas exist (degenerate, not an error — the table will show it)."""
    spot = dict(spot_closes)
    perp = dict(perp_closes)
    ts = sorted(set(spot) & set(perp))
    basis = [(perp[t] - spot[t]) / spot[t] for t in ts]
    deltas = [b2 - b1 for b1, b2 in zip(basis, basis[1:])]
    if len(deltas) < 2:
        return 0.0
    return statistics.stdev(deltas)


def scale_to_window(sigma_1m: float, t_seconds: float) -> float:
    """Random-walk scaling: sigma_T = sigma_1m * sqrt(T/60). Declared approximation
    (spec §6.2) — a description, not a measurement."""
    return sigma_1m * math.sqrt(t_seconds / 60.0)


def _atomic_write(path: str, text: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _table_md(res: dict) -> str:
    head = ("# U2 — basis sigma table (DESCRIPTIVE, no verdict)\n\n"
            f"run_ts_utc: {res['run_ts_utc']} · days={LEG_LAG_DAYS} · "
            f"notional/leg={NOTIONAL} · version={res['version']}\n\n"
            "A 2nd-moment scale. NOT comparable against costs or carry (1st moment);\n"
            "interpretation waits for a declared risk budget (spec §4).\n\n"
            "| symbol | sigma_1m | " +
            " | ".join(f"per-event USD T={t}s" for t in LEG_LAG_T_SWEEP) + " | " +
            " | ".join(f"hold-cont USD T={t}s" for t in LEG_LAG_T_SWEEP) + " |\n" +
            "|" + "---|" * (2 + 2 * len(LEG_LAG_T_SWEEP)) + "\n")
    rows = []
    for s, r in res["per_symbol"].items():
        cells = [s, f"{r['sigma_1m']:.3e}"]
        cells += [f"{r['per_event_usd'][t]:.2f}" for t in LEG_LAG_T_SWEEP]
        cells += [f"{r['hold_continuo_usd'][t]:.2f}" for t in LEG_LAG_T_SWEEP]
        rows.append("| " + " | ".join(cells) + " |")
    return head + "\n".join(rows) + "\n"


def run(*, now_ms: int, out_dir: str = EXEC_REALISM_OUTPUT_DIR,
        symbols: tuple = SHADOW_SYMBOLS, days: float = LEG_LAG_DAYS) -> dict:
    """One-shot U2: paginated 1m klines (hard-fail on short series), per-symbol
    sigma table over the full T sweep. Two labeled columns per T: per-event and
    sqrt(2) hold-continuo aggregate (Adrian REV2-F12). FetchFailed propagates —
    manual one-shot, crash loud and re-run."""
    from . import live_ingest
    run_ts = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat()
    per: dict = {}
    for i, s in enumerate(symbols):
        spot = live_ingest.fetch_klines_1m_paginated(
            s, base_url=SPOT_KLINES_1M, days=days, end_ms=now_ms)
        perp = live_ingest.fetch_klines_1m_paginated(
            s, base_url=FAPI_MARK_KLINES, days=days, end_ms=now_ms)
        s1 = basis_sigma_1m(spot, perp)
        log.info("leg_lag %s: sigma_1m=%.3e (%d/%d)", s, s1, i + 1, len(symbols))
        per_event = {t: scale_to_window(s1, t) * NOTIONAL for t in LEG_LAG_T_SWEEP}
        per[s] = {"sigma_1m": s1,
                  "per_event_usd": per_event,
                  "hold_continuo_usd": {t: v * math.sqrt(2.0)
                                        for t, v in per_event.items()}}
    res = {"run_ts_utc": run_ts, "version": EXEC_REALISM_VERSION,
           "days": days, "per_symbol": per}
    os.makedirs(out_dir, exist_ok=True)
    _atomic_write(os.path.join(out_dir, "leg_lag.json"), json.dumps(res, indent=2))
    _atomic_write(os.path.join(out_dir, "leg_lag.md"), _table_md(res))
    return res


if __name__ == "__main__":
    import time
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(run(now_ms=int(time.time() * 1000)), indent=2))
