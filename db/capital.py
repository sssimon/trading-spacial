"""Capital DB layer — per-user capital state.

Introduced in B.5 follow-up B (PR for Epic B #253). Schema from B.1 #254.

Single row per tenant_id (enforced by UNIQUE INDEX idx_capital_tenant).
Pre-reg: docs/superpowers/plans/2026-05-16-multi-tenant-b5-capital-prefs-pre-reg.md
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("db.capital")

# Default starting balance for a tenant whose first position closes before
# any explicit PUT /capital was issued. Matches the per-symbol convention in
# backtest.py:80 — keeping the value consistent across simulator + live ledger.
INITIAL_CAPITAL_DEFAULT = 10_000.0


def db_get_capital(
    con: sqlite3.Connection,
    tenant_id: int,
) -> Optional[dict]:
    """Return current capital row for tenant, or None if uninitialized.

    Task 5 (#446): `con` is now mandatory positional. Callers must pass an
    open `sqlite3.Connection` from a surrounding `transaction()` block.
    """
    row = con.execute(
        "SELECT * FROM capital WHERE tenant_id = ?", (tenant_id,),
    ).fetchone()
    return dict(row) if row else None


def db_list_active_tenant_ids(
    con: sqlite3.Connection,
) -> list[int]:
    """Return tenant ids with an existing capital row, sorted ascending.

    Used by background processes (scanner, calibrator, shadow emitter) to
    iterate over every tenant the system actually serves — replacing the
    legacy single-tenant single-pass pattern. An empty list means "no
    onboarded tenants" and callers should treat that as a no-op rather
    than computing implicit single-system aggregates.

    Task 5 (#446): `con` is now mandatory positional.
    """
    rows = con.execute(
        "SELECT tenant_id FROM capital ORDER BY tenant_id ASC"
    ).fetchall()
    return [int(r[0]) for r in rows]


def apply_pnl_to_capital(
    con: sqlite3.Connection,
    tenant_id: int,
    pnl_usd: float,
) -> Optional[dict]:
    """B.2 hook: a position closed for `tenant_id` with realized `pnl_usd`.

    Updates the tenant's capital row with monotonic-peak + current-drawdown
    semantics. If no prior row exists, auto-init from INITIAL_CAPITAL_DEFAULT
    (the first close stamps the row).

    Returns the resulting capital row. Locks are documented in
    docs/superpowers/plans/2026-05-16-multi-tenant-b2-capital-tracker-pre-reg.md §2.3.

    Task 5 (#446): `con` is now mandatory positional first arg. The helper
    is pure Cat. 1 SQL — get → compute → upsert all run inline on the
    caller's connection. Atomicity is the caller's responsibility (open
    one `transaction()` around the close + capital roll-in).
    """
    row = db_get_capital(con, tenant_id)
    if row is None:
        prior_balance = INITIAL_CAPITAL_DEFAULT
        prior_peak = INITIAL_CAPITAL_DEFAULT
    else:
        prior_balance = float(row["balance"])
        prior_peak = float(row["peak_balance"])

    new_balance = prior_balance + float(pnl_usd)
    new_peak = max(prior_peak, new_balance)  # monotonic — never decreases
    if new_peak > 0:
        new_dd_pct = (new_peak - new_balance) / new_peak * 100.0
    else:
        new_dd_pct = None  # peak ≤ 0 leaves drawdown undefined

    return db_upsert_capital(
        con,
        tenant_id,
        balance=new_balance,
        peak_balance=new_peak,
        max_drawdown_pct=new_dd_pct,
    )


def db_upsert_capital(
    con: sqlite3.Connection,
    tenant_id: int,
    *,
    balance: float,
    peak_balance: Optional[float] = None,
    max_drawdown_pct: Optional[float] = None,
) -> dict:
    """Insert or replace capital row for tenant.

    Semantics:
    - If row exists and peak_balance not given, preserve existing peak.
    - If row absent and peak_balance not given, peak_balance := balance.
    - max_drawdown_pct: preserve when not given (existing row) OR None (new row).

    Returns the resulting row.

    Task 5 (#446): `con` is now mandatory positional first arg.
    """
    now = datetime.now(timezone.utc).isoformat()
    existing = con.execute(
        "SELECT id, peak_balance, max_drawdown_pct FROM capital WHERE tenant_id = ?",
        (tenant_id,),
    ).fetchone()

    if existing is None:
        effective_peak = peak_balance if peak_balance is not None else balance
        effective_dd = max_drawdown_pct
        con.execute(
            """INSERT INTO capital (tenant_id, balance, peak_balance,
                                    max_drawdown_pct, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (tenant_id, balance, effective_peak, effective_dd, now),
        )
    else:
        effective_peak = peak_balance if peak_balance is not None else existing["peak_balance"]
        effective_dd = (
            max_drawdown_pct if max_drawdown_pct is not None
            else existing["max_drawdown_pct"]
        )
        con.execute(
            """UPDATE capital
               SET balance = ?, peak_balance = ?, max_drawdown_pct = ?,
                   updated_at = ?
               WHERE tenant_id = ?""",
            (balance, effective_peak, effective_dd, now, tenant_id),
        )
    row = con.execute(
        "SELECT * FROM capital WHERE tenant_id = ?", (tenant_id,),
    ).fetchone()
    return dict(row)
