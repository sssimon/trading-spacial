"""Agent rollout health monitor — Phase 6 of epic #400.

Runs the 4 monitor queries the spec §12 calls for during the 48h bake,
evaluates each against its threshold, and prints a pass/fail summary.
Read-only — no DB writes, no API calls.

Usage:
    python scripts/agent_health_check.py                # default 24h window
    python scripts/agent_health_check.py --window 6h    # last 6 hours
    python scripts/agent_health_check.py --window 1h    # last hour (post-flip smoke)
    python scripts/agent_health_check.py --json         # machine-readable

Exit code:
    0 — all metrics within thresholds
    1 — at least one metric crossed its abort threshold
    2 — script error (DB unreachable, schema mismatch, etc)

Thresholds (per pre-reg §12 + §14):
    - cache_hit_rate              >= 0.50  (target 0.70 post-warmup)
    - error_rate                  <= 0.05  (sustained > 0.05 → abort)
    - p95_latency_ms              <= 4000  (target per §14)
    - daily_spend_usd             <  5.00  (informational; breaker hits it)

The script does NOT trip the breaker. If a threshold is crossed, the
operator decides (flip cfg.agent.breaker_open=true via config.json,
investigate, or accept the breach as a one-off). The script's job is
to surface the signal.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone


# ── Thresholds ────────────────────────────────────────────────────────


CACHE_HIT_RATE_MIN     = 0.50   # abort if SUSTAINED below; target 0.70
ERROR_RATE_MAX         = 0.05
P95_LATENCY_MS_MAX     = 4000
DAILY_SPEND_USD_MAX    = 5.00


# ── Config / DB path ──────────────────────────────────────────────────


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _db_path() -> str:
    """Resolve the signals.db location the same way btc_api.py does."""
    # The runtime canonicalizes to <repo>/signals.db; no special env var
    # is needed for the bake host.
    return os.path.join(_repo_root(), "signals.db")


# ── Window parsing ────────────────────────────────────────────────────


def _parse_window(s: str) -> timedelta:
    """Parse a window string. '24h', '6h', '1h', '30m' are supported."""
    s = s.strip().lower()
    if s.endswith("h"):
        return timedelta(hours=int(s[:-1]))
    if s.endswith("m"):
        return timedelta(minutes=int(s[:-1]))
    raise ValueError(f"unsupported window {s!r}; use e.g. '24h', '6h', '30m'")


# ── Query helpers ─────────────────────────────────────────────────────


def _open() -> sqlite3.Connection:
    p = _db_path()
    if not os.path.exists(p):
        raise FileNotFoundError(f"signals.db not found at {p}")
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    return con


def _cutoff_iso(window: timedelta) -> str:
    return (datetime.now(timezone.utc) - window).isoformat()


def query_cache_hit_rate(con: sqlite3.Connection, cutoff: str) -> tuple[float, int]:
    """Cache hit rate = cache_read / (cache_read + input_tokens).

    A value near 1.0 means every byte the model saw came from cache.
    Near 0.0 means cache is cold. Pre-reg §14 targets ≥70% post-warmup.
    Returns (rate, sample_size).
    """
    row = con.execute(
        "SELECT "
        "  COALESCE(SUM(cache_read_input_tokens), 0) AS cache_read, "
        "  COALESCE(SUM(input_tokens),            0) AS uncached, "
        "  COUNT(*) AS n "
        "FROM agent_conversations "
        "WHERE ts >= ? AND role = 'assistant'",
        (cutoff,),
    ).fetchone()
    d = dict(row)
    cache_read = float(d["cache_read"] or 0)
    uncached = float(d["uncached"] or 0)
    total = cache_read + uncached
    rate = (cache_read / total) if total > 0 else 0.0
    return rate, int(d["n"] or 0)


def query_error_rate(con: sqlite3.Connection, cutoff: str) -> tuple[float, int, int]:
    """Error rate = error_rows / total_rows in the window.
    Returns (rate, errors, total)."""
    row = con.execute(
        "SELECT "
        "  COUNT(*) AS total, "
        "  SUM(CASE WHEN role = 'error' THEN 1 ELSE 0 END) AS errors "
        "FROM agent_conversations WHERE ts >= ?",
        (cutoff,),
    ).fetchone()
    d = dict(row)
    total = int(d["total"] or 0)
    errors = int(d["errors"] or 0)
    rate = (errors / total) if total > 0 else 0.0
    return rate, errors, total


def query_p95_latency_ms(con: sqlite3.Connection, cutoff: str) -> tuple[int | None, int]:
    """p95 of latency_ms across assistant turns in the window.

    Implemented as an OFFSET-based percentile (sqlite has no PERCENTILE
    aggregate). Returns (p95_ms or None if no data, sample_size).
    """
    n_row = con.execute(
        "SELECT COUNT(*) AS n FROM agent_conversations "
        "WHERE ts >= ? AND role = 'assistant' AND latency_ms IS NOT NULL",
        (cutoff,),
    ).fetchone()
    n = int(dict(n_row)["n"] or 0)
    if n == 0:
        return None, 0
    offset = max(0, (n * 95 // 100) - 1)
    row = con.execute(
        "SELECT latency_ms FROM agent_conversations "
        "WHERE ts >= ? AND role = 'assistant' AND latency_ms IS NOT NULL "
        "ORDER BY latency_ms LIMIT 1 OFFSET ?",
        (cutoff, offset),
    ).fetchone()
    return int(dict(row)["latency_ms"]), n


def query_daily_spend_by_provider(con: sqlite3.Connection,
                                    cutoff: str) -> dict[str, float]:
    """Fase 4 of the multi-provider epic: return spend breakdown by
    provider. NULL provider buckets as 'unknown' (legacy rows pre-
    backfill). Mirrors the /agent/metrics today.by_provider shape but
    operates on the same window as query_daily_spend_usd."""
    rows = con.execute(
        "SELECT COALESCE(provider, 'unknown') AS provider, "
        "       COALESCE(SUM(cost_usd), 0) AS total_usd "
        "FROM agent_conversations "
        "WHERE ts >= ? AND role = 'assistant' "
        "GROUP BY COALESCE(provider, 'unknown')",
        (cutoff,),
    ).fetchall()
    return {dict(r)["provider"]: float(dict(r)["total_usd"] or 0) for r in rows}


def query_daily_spend_usd(con: sqlite3.Connection, cutoff: str) -> float:
    """Sum cost_usd of assistant rows in the window. Mirrors what
    api/agent/circuit_breaker.py reads."""
    row = con.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) AS total "
        "FROM agent_conversations "
        "WHERE ts >= ? AND role = 'assistant'",
        (cutoff,),
    ).fetchone()
    return float(dict(row)["total"] or 0)


def query_top_errors(con: sqlite3.Connection, cutoff: str, limit: int = 5) -> list[dict]:
    """Closed-enum error reason breakdown for context when error_rate
    crosses threshold. Returns [{reason, count}, ...]."""
    rows = con.execute(
        "SELECT content_json AS reason_json, COUNT(*) AS count "
        "FROM agent_conversations "
        "WHERE ts >= ? AND role = 'error' "
        "GROUP BY content_json "
        "ORDER BY count DESC "
        "LIMIT ?",
        (cutoff, limit),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        raw = d.get("reason_json")
        try:
            reason = json.loads(raw) if raw else "unknown"
        except (TypeError, ValueError):
            reason = "unknown"
        out.append({"reason": reason, "count": int(d["count"] or 0)})
    return out


# ── Reporting ─────────────────────────────────────────────────────────


@dataclass
class MetricResult:
    name:     str
    value:    float | int | str | None
    threshold: float | int | str
    direction: str   # "min" → value should be >= threshold; "max" → value should be <= threshold
    ok:       bool
    detail:   str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _evaluate_metrics(con: sqlite3.Connection, window: timedelta) -> list[MetricResult]:
    cutoff = _cutoff_iso(window)
    results: list[MetricResult] = []

    # 1. Cache hit rate
    rate, n = query_cache_hit_rate(con, cutoff)
    detail = f"sample n={n}" + (" (warmup - need more turns)" if n < 5 else "")
    results.append(MetricResult(
        name="cache_hit_rate",
        value=round(rate, 4),
        threshold=CACHE_HIT_RATE_MIN,
        direction="min",
        ok=(rate >= CACHE_HIT_RATE_MIN) or n < 5,  # waive during warmup
        detail=detail,
    ))

    # 2. Error rate
    rate, errs, total = query_error_rate(con, cutoff)
    results.append(MetricResult(
        name="error_rate",
        value=round(rate, 4),
        threshold=ERROR_RATE_MAX,
        direction="max",
        ok=(rate <= ERROR_RATE_MAX),
        detail=f"{errs}/{total} rows are errors",
    ))

    # 3. p95 latency
    p95, n = query_p95_latency_ms(con, cutoff)
    if p95 is None:
        results.append(MetricResult(
            name="p95_latency_ms",
            value=None,
            threshold=P95_LATENCY_MS_MAX,
            direction="max",
            ok=True,
            detail="no assistant rows in window",
        ))
    else:
        results.append(MetricResult(
            name="p95_latency_ms",
            value=p95,
            threshold=P95_LATENCY_MS_MAX,
            direction="max",
            ok=(p95 <= P95_LATENCY_MS_MAX),
            detail=f"sample n={n}",
        ))

    # 4. Daily spend
    spend = query_daily_spend_usd(con, cutoff)
    results.append(MetricResult(
        name="daily_spend_usd",
        value=round(spend, 4),
        threshold=DAILY_SPEND_USD_MAX,
        direction="max",
        ok=(spend < DAILY_SPEND_USD_MAX),
        detail=f"breaker auto-trips at >= ${DAILY_SPEND_USD_MAX:.2f}",
    ))

    return results


def _color(text: str, code: str) -> str:
    """ANSI color if stdout is a TTY, otherwise plain text."""
    if not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


def _print_text_report(results: list[MetricResult], window: timedelta,
                        top_errors: list[dict],
                        by_provider: dict[str, float] | None = None) -> None:
    print()
    print(_color("AGENT HEALTH CHECK", "1"))
    print(f"  window: last {int(window.total_seconds() // 3600)}h "
          f"({window.total_seconds():.0f}s)")
    print(f"  cutoff: {_cutoff_iso(window)}")
    print()
    name_width = max(len(r.name) for r in results)
    for r in results:
        if r.ok:
            tag = _color("OK   ", "32")
        else:
            tag = _color("BREACH", "31;1")
        # ASCII compare symbols — Windows default console (cp1252)
        # can't encode ≥/≤. Keeps the output portable across OSes.
        cmp = ">=" if r.direction == "min" else "<="
        threshold_str = (f"{r.threshold:.2f}" if isinstance(r.threshold, float)
                          else str(r.threshold))
        value_str = "-" if r.value is None else (
            f"{r.value:.4f}" if isinstance(r.value, float) else str(r.value)
        )
        print(f"  {tag}  {r.name.ljust(name_width)}  "
              f"value={value_str}  expected {cmp} {threshold_str}  "
              f"({r.detail})")
    if by_provider:
        print()
        print("  Spend breakdown by provider:")
        for prov in sorted(by_provider):
            print(f"    - {prov:12s} ${by_provider[prov]:.4f}")
    if top_errors:
        print()
        print("  Top error reasons in window:")
        for e in top_errors:
            print(f"    - {e['reason']:30s} x{e['count']}")
    print()
    breaches = [r for r in results if not r.ok]
    if breaches:
        print(_color(
            f"  [FAIL] {len(breaches)} metric(s) crossed threshold - "
            f"check the rollout runbook abort criteria.", "31;1",
        ))
    else:
        print(_color("  [OK] all metrics within thresholds.", "32"))
    print()


def _print_json_report(results: list[MetricResult], window: timedelta,
                        top_errors: list[dict],
                        by_provider: dict[str, float] | None = None) -> None:
    out = {
        "window_seconds":     int(window.total_seconds()),
        "cutoff_iso":         _cutoff_iso(window),
        "metrics":            [r.to_dict() for r in results],
        "top_errors_in_window": top_errors,
        "spend_by_provider":  by_provider or {},
        "all_ok":             all(r.ok for r in results),
    }
    print(json.dumps(out, indent=2, default=str))


# ── Main ──────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent rollout health check")
    parser.add_argument("--window", default="24h",
                         help="time window to evaluate (e.g. 24h, 6h, 1h, 30m)")
    parser.add_argument("--json", action="store_true",
                         help="machine-readable JSON instead of text report")
    args = parser.parse_args()

    try:
        window = _parse_window(args.window)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    try:
        with _open() as con:
            results = _evaluate_metrics(con, window)
            top_errors = query_top_errors(con, _cutoff_iso(window))
            # Fase 4 of the multi-provider epic: surface per-provider
            # spend breakdown so the operator can tell DS vs Anthropic
            # spend in the same report.
            by_provider = query_daily_spend_by_provider(con, _cutoff_iso(window))
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except sqlite3.DatabaseError as e:
        msg = str(e)
        if "no such table" in msg and "agent_conversations" in msg:
            # Pre-flip state: the agent audit tables don't exist yet
            # because btc_api.init_db() hasn't run after the Phase 1
            # schema landed. This is benign — the operator will see it
            # the first time they run the script before restarting the
            # server. Surface a clear message instead of the raw DB
            # error.
            print(
                "error: agent_conversations table not found in signals.db.\n"
                "       The Phase 1 schema migration runs on btc_api.init_db().\n"
                "       Restart btc_api.py (or call init_db() in a REPL) and\n"
                "       re-run this check. No data is lost — the migration is\n"
                "       idempotent.",
                file=sys.stderr,
            )
            return 2
        print(f"error: db query failed: {e}", file=sys.stderr)
        return 2

    if args.json:
        _print_json_report(results, window, top_errors, by_provider)
    else:
        _print_text_report(results, window, top_errors, by_provider)

    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
