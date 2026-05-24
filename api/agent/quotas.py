"""Per-tenant spend quotas for the copilot — Phase 5 of epic #400.

Two windows enforced per tenant:

  - daily: resets at midnight UTC. Default cap $1.00/day (configurable
    via `agent_quotas.daily_usd_cap` per tenant; operator override via
    a one-off UPDATE on the row).
  - monthly: resets on the 1st (UTC). Default cap $20.00/month, derived
    from 31 days × daily_cap + slack. Tracked for cost telemetry but
    not enforced as a hard 429 today — the daily cap is the spend
    governor; the monthly column exists so the metrics endpoint can
    report on it.

Pre-reg §9.1: "Quota counters reset on read, not via cron." When
check_and_charge fires, it first reads the current row, compares
window_start to "now", and if the window has elapsed it zeroes the
counter + updates the start IN THE SAME TRANSACTION as the charge.
No cron, no race between reset and increment.

Failure mode:
  - daily exceeded → QuotaExceeded raised at the boundary. Router
    translates to HTTP 429 detail="quota_exceeded".
  - DB write fails after a charge → logged but does not raise; the
    audit row already carries the cost, so reconciliation is possible
    via offline scan. Same fail-quiet discipline as audit.record_turn.

Why this lives in its own module:
  - keeps the router thin; the router just calls check_quota_pretrun()
    and record_spend(); the SQL details are encapsulated here.
  - tests can monkeypatch the public functions without faking sqlite.
  - a future "burst credit" or "weekend reduced cap" policy lives here
    without touching the request path.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from db.connection import get_db

log = logging.getLogger("api.agent.quotas")


# Defaults applied when a tenant has no row yet. The first turn for a
# tenant triggers an INSERT with these values.
DEFAULT_DAILY_USD_CAP   = 1.00
DEFAULT_MONTHLY_USD_CAP = 20.00


# ── Closed-enum reasons surfaced to the wire ──────────────────────────


REASON_OK              = "ok"
REASON_DAILY_EXCEEDED  = "quota_exceeded"  # daily cap


class QuotaExceeded(Exception):
    """Raised by check_quota_pretrun when the daily cap is at or beyond
    the limit. The router catches and translates to 429."""

    def __init__(self, *, daily_used: float, daily_cap: float):
        super().__init__(f"daily quota exceeded: {daily_used:.4f} >= {daily_cap:.4f}")
        self.daily_used = daily_used
        self.daily_cap  = daily_cap


# ── Public API ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class QuotaSnapshot:
    """Per-tenant quota state at a point in time. Returned by the
    metrics endpoint and by check_quota_pretrun for callers that want
    headroom for a 'remaining' display."""
    tenant_id:               int
    daily_usd_used:          float
    daily_usd_cap:           float
    daily_window_start:      str
    monthly_usd_used:        float
    monthly_window_start:    str


def check_quota_pretrun(tenant_id: int) -> QuotaSnapshot:
    """Pre-flight check before running an agent turn. Idempotent — does
    NOT charge anything; only reads, resets stale windows, and decides.

    Raises QuotaExceeded if `daily_usd_used >= daily_usd_cap` (after
    any due window reset). The caller does not need to short-circuit
    on a "negative remaining" check — the threshold is enforced here.
    """
    row = _read_or_seed_row(tenant_id)
    return _materialize_snapshot(row)


def record_spend(tenant_id: int, cost_usd: float) -> None:
    """Charge `cost_usd` against the tenant's daily + monthly counters
    AFTER a turn completed. Best-effort: failures are logged, not raised
    (same discipline as audit.record_turn — a charge miss is annoying;
    a 500 to the user is worse).

    The function refuses to record a negative or zero charge (we get
    cost_usd=0 from the loop when the model is the cheap fake or when
    the cost calculator can't find pricing — no point burning a write).
    """
    if cost_usd is None or cost_usd <= 0:
        return
    try:
        _apply_spend(tenant_id, float(cost_usd))
    except Exception:  # noqa: BLE001
        log.warning(
            "record_spend failed for tenant=%s cost_usd=%.4f — quota write dropped",
            tenant_id, cost_usd, exc_info=True,
        )


def get_snapshot(tenant_id: int) -> QuotaSnapshot:
    """Read-only accessor used by the metrics endpoint. Same windowing
    logic as check_quota_pretrun but does NOT raise on exceeded — the
    admin wants to SEE the breach, not be blocked from reading it."""
    row = _read_or_seed_row(tenant_id)
    return _to_snapshot(row)


# ── Implementation ────────────────────────────────────────────────────


def _now_utc() -> datetime:
    """Test seam: monkeypatch this in tests to advance the clock."""
    return datetime.now(timezone.utc)


def _today_iso() -> str:
    """ISO date for window comparison. UTC. Format: 'YYYY-MM-DD'."""
    return _now_utc().date().isoformat()


def _this_month_iso() -> str:
    """First day of the current UTC month. Format: 'YYYY-MM-01'."""
    n = _now_utc()
    return f"{n.year:04d}-{n.month:02d}-01"


def _read_or_seed_row(tenant_id: int) -> sqlite3.Row:
    """Return the agent_quotas row for `tenant_id`. INSERT default
    values if absent. Resets stale daily/monthly windows IN-LINE so
    the returned row already reflects the current window."""
    with get_db() as con:
        today = _today_iso()
        month = _this_month_iso()
        row = con.execute(
            "SELECT * FROM agent_quotas WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()
        if row is None:
            # INSERT OR IGNORE handles the concurrent-first-turn race:
            # if two parallel requests for the same brand-new tenant_id
            # both see row=None, the second INSERT silently no-ops on
            # the PRIMARY KEY violation instead of raising IntegrityError
            # (which would surface as 500 to the user). PR #408 review
            # pickup. The SELECT immediately below reads the row that
            # actually landed regardless of who won the race.
            con.execute(
                """INSERT OR IGNORE INTO agent_quotas
                   (tenant_id, daily_usd_used, daily_usd_cap, daily_window_start,
                    monthly_usd_used, monthly_window_start)
                   VALUES (?, 0, ?, ?, 0, ?)""",
                (tenant_id, DEFAULT_DAILY_USD_CAP, today, month),
            )
            con.commit()
            row = con.execute(
                "SELECT * FROM agent_quotas WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
            return row

        # Window resets — done in SQL so the read-then-write is atomic
        # under the connection's write lock (sqlite is serialized at
        # connection level for writes).
        d = dict(row)
        needs_reset = (d["daily_window_start"] != today
                       or d["monthly_window_start"] != month)
        if needs_reset:
            new_daily   = 0 if d["daily_window_start"]   != today else d["daily_usd_used"]
            new_monthly = 0 if d["monthly_window_start"] != month else d["monthly_usd_used"]
            con.execute(
                """UPDATE agent_quotas
                   SET daily_usd_used = ?, daily_window_start = ?,
                       monthly_usd_used = ?, monthly_window_start = ?
                   WHERE tenant_id = ?""",
                (new_daily, today, new_monthly, month, tenant_id),
            )
            con.commit()
            row = con.execute(
                "SELECT * FROM agent_quotas WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
        return row


def _to_snapshot(row: sqlite3.Row) -> QuotaSnapshot:
    d = dict(row)
    return QuotaSnapshot(
        tenant_id=d["tenant_id"],
        daily_usd_used=float(d["daily_usd_used"] or 0),
        daily_usd_cap=float(d["daily_usd_cap"] or DEFAULT_DAILY_USD_CAP),
        daily_window_start=d["daily_window_start"],
        monthly_usd_used=float(d["monthly_usd_used"] or 0),
        monthly_window_start=d["monthly_window_start"],
    )


def _materialize_snapshot(row: sqlite3.Row) -> QuotaSnapshot:
    """Convert a row to a snapshot AND enforce the daily threshold.
    Separate from _to_snapshot so the metrics endpoint (read-only) can
    bypass the raise."""
    snap = _to_snapshot(row)
    if snap.daily_usd_used >= snap.daily_usd_cap:
        raise QuotaExceeded(
            daily_used=snap.daily_usd_used,
            daily_cap=snap.daily_usd_cap,
        )
    return snap


def _apply_spend(tenant_id: int, cost_usd: float) -> None:
    """Increment daily + monthly counters atomically. Resets first if
    a window passed (mirrors _read_or_seed_row's reset logic)."""
    with get_db() as con:
        today = _today_iso()
        month = _this_month_iso()
        row = con.execute(
            "SELECT * FROM agent_quotas WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()
        if row is None:
            # Tenant's very first turn (paid path didn't read first —
            # rare, but possible if check_quota_pretrun fails open on
            # a DB hiccup). Seed + charge in one go. INSERT OR IGNORE
            # protects against the parallel-first-charge race the same
            # way _read_or_seed_row does (PR #408 review pickup).
            cur = con.execute(
                """INSERT OR IGNORE INTO agent_quotas
                   (tenant_id, daily_usd_used, daily_usd_cap, daily_window_start,
                    monthly_usd_used, monthly_window_start)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (tenant_id, cost_usd, DEFAULT_DAILY_USD_CAP, today, cost_usd, month),
            )
            con.commit()
            # cur.rowcount tells us which branch fired: 1 = we inserted
            # (charge already booked, done), 0 = a parallel writer beat
            # us so we re-read and fall through to the UPDATE path to
            # apply OUR cost on top of theirs.
            if cur.rowcount == 1:
                return
            row = con.execute(
                "SELECT * FROM agent_quotas WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()

        d = dict(row)
        daily   = (0 if d["daily_window_start"]   != today else d["daily_usd_used"])   + cost_usd
        monthly = (0 if d["monthly_window_start"] != month else d["monthly_usd_used"]) + cost_usd
        con.execute(
            """UPDATE agent_quotas
               SET daily_usd_used = ?, daily_window_start = ?,
                   monthly_usd_used = ?, monthly_window_start = ?
               WHERE tenant_id = ?""",
            (daily, today, monthly, month, tenant_id),
        )
        con.commit()
