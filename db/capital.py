"""Capital DB layer — per-user capital state.

Introduced in B.5 follow-up B (PR for Epic B #253). Schema from B.1 #254.

Single row per tenant_id (enforced by UNIQUE INDEX idx_capital_tenant).
Pre-reg: docs/superpowers/plans/2026-05-16-multi-tenant-b5-capital-prefs-pre-reg.md
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from db.connection import get_db

log = logging.getLogger("db.capital")

# Default starting balance for a tenant whose first position closes before
# any explicit PUT /capital was issued. Matches the per-symbol convention in
# backtest.py:80 — keeping the value consistent across simulator + live ledger.
INITIAL_CAPITAL_DEFAULT = 10_000.0


def db_get_capital(tenant_id: int) -> Optional[dict]:
    """Return current capital row for tenant, or None if uninitialized."""
    con = get_db()
    try:
        row = con.execute(
            "SELECT * FROM capital WHERE tenant_id = ?", (tenant_id,),
        ).fetchone()
    finally:
        con.close()
    return dict(row) if row else None


def apply_pnl_to_capital(tenant_id: int, pnl_usd: float) -> Optional[dict]:
    """B.2 hook: a position closed for `tenant_id` with realized `pnl_usd`.

    Updates the tenant's capital row with monotonic-peak + current-drawdown
    semantics. If no prior row exists, auto-init from INITIAL_CAPITAL_DEFAULT
    (the first close stamps the row).

    Returns the resulting capital row. Locks are documented in
    docs/superpowers/plans/2026-05-16-multi-tenant-b2-capital-tracker-pre-reg.md §2.3.
    """
    row = db_get_capital(tenant_id)
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
        tenant_id,
        balance=new_balance,
        peak_balance=new_peak,
        max_drawdown_pct=new_dd_pct,
    )


def db_upsert_capital(
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
    """
    now = datetime.now(timezone.utc).isoformat()
    con = get_db()
    try:
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
        con.commit()
        row = con.execute(
            "SELECT * FROM capital WHERE tenant_id = ?", (tenant_id,),
        ).fetchone()
    finally:
        con.close()
    return dict(row)
