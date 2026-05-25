"""B.8 #261 — one-shot production migration to multi-tenant ownership.

Stamps `tenant_id = --user-id` on all pre-multi-tenant rows (where tenant_id
IS NULL) across the per-user tables defined in `db.schema.PER_USER_TABLES`,
and creates an initial `capital` row anchored at `--initial-balance`.

Pre-reg: docs/superpowers/plans/2026-05-16-multi-tenant-b8-migration-pre-reg.md

Default mode is DRY-RUN. Real writes require explicit `--execute`.

Examples:
    # Preview what would change (default — no writes)
    python scripts/migrate_to_multitenant.py --user-id 1

    # Real migration (Samuel's user_id = 1, initial balance $10K)
    python scripts/migrate_to_multitenant.py --user-id 1 --execute

    # Re-run after fixing initial balance (capital row already exists)
    python scripts/migrate_to_multitenant.py --user-id 1 --execute --force \\
        --initial-balance 12500.0
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from db.capital import (  # noqa: E402
    INITIAL_CAPITAL_DEFAULT,
    db_get_capital,
    db_upsert_capital,
)
from db.schema import PER_USER_TABLES, backfill_tenant  # noqa: E402
from db.transaction import transaction  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("migrate_to_multitenant")


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _validate_user(user_id: int) -> Optional[dict]:
    """Return user row dict or None if not found."""
    with transaction() as con:
        row = con.execute(
            "SELECT id, email FROM users WHERE id = ?", (user_id,),
        ).fetchone()
    return dict(row) if row else None


def _list_known_users() -> list[dict]:
    with transaction() as con:
        rows = con.execute("SELECT id, email FROM users ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def _snapshot_counts() -> dict[str, dict[str, int]]:
    """For each per-user table: total + null_tenant counts."""
    out: dict[str, dict[str, int]] = {}
    with transaction() as con:
        for table in PER_USER_TABLES:
            total = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            null_n = con.execute(
                f"SELECT COUNT(*) FROM {table} WHERE tenant_id IS NULL"
            ).fetchone()[0]
            out[table] = {"total": int(total), "null_tenant": int(null_n)}
    return out


def _spot_check_positions(tenant_id: int, limit: int = 10) -> list[dict]:
    with transaction() as con:
        rows = con.execute(
            "SELECT id, symbol, status, entry_ts, exit_ts, pnl_usd "
            "FROM positions WHERE tenant_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (tenant_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------


def _format_snapshot(snap: dict[str, dict[str, int]]) -> str:
    lines = []
    for table, counts in snap.items():
        lines.append(
            f"  {table:30s} total={counts['total']:6d}  "
            f"null_tenant={counts['null_tenant']:6d}"
        )
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    mode = "EXECUTE" if args.execute else "DRY-RUN"
    log.info("=" * 60)
    log.info("Multi-tenant migration B.8 — mode=%s", mode)
    log.info("=" * 60)

    # 1. Validate user
    user = _validate_user(args.user_id)
    if user is None:
        log.error("user_id=%s not found in users table.", args.user_id)
        known = _list_known_users()
        if known:
            log.error("Known users: %s", [f"{u['id']}:{u['email']}" for u in known])
        else:
            log.error("No users exist yet — run setup first.")
        return 2
    log.info("Target user: id=%s, email=%s", user["id"], user["email"])

    # 2. Pre-snapshot
    pre = _snapshot_counts()
    log.info("Pre-migration row counts:\n%s", _format_snapshot(pre))
    # Task 5 (#446): db_get_capital requires `con` positional.
    with transaction() as con:
        pre_capital = db_get_capital(con, args.user_id)
    log.info(
        "Pre-migration capital row: %s",
        "EXISTS" if pre_capital else "absent",
    )

    null_total = sum(c["null_tenant"] for c in pre.values())
    if null_total == 0 and pre_capital is not None:
        log.info("All tables already migrated AND capital row exists — no-op.")
        return 0

    log.info(
        "Plan: stamp tenant_id=%s on %d NULL-tenant rows%s",
        args.user_id,
        null_total,
        ("; create capital row" if pre_capital is None else
         ("; overwrite capital row" if args.force else "; capital row unchanged")),
    )
    log.info("Initial balance anchor: $%s", args.initial_balance)

    if not args.execute:
        log.info("DRY-RUN — exiting without writes. Re-run with --execute to apply.")
        return 0

    # 3. Refuse capital overwrite unless --force
    if pre_capital is not None and not args.force:
        log.error(
            "Capital row already exists for user_id=%s (balance=%s, peak=%s). "
            "Re-run with --force to overwrite.",
            args.user_id, pre_capital["balance"], pre_capital["peak_balance"],
        )
        return 1

    # 4. Real migration
    log.info("Running backfill_tenant(user_id=%s)…", args.user_id)
    affected = backfill_tenant(args.user_id)
    log.info(
        "backfill_tenant results: %s",
        ", ".join(f"{t}={n}" for t, n in affected.items()),
    )

    log.info("Upserting capital row…")
    # Task 5 (#446): db_upsert_capital requires `con` positional.
    with transaction() as con:
        capital_row = db_upsert_capital(
            con,
            args.user_id,
            balance=args.initial_balance,
            peak_balance=args.initial_balance,
            max_drawdown_pct=None,
        )
    log.info(
        "Capital row: balance=%s, peak=%s",
        capital_row["balance"], capital_row["peak_balance"],
    )

    # 5. Post-snapshot validation
    post = _snapshot_counts()
    log.info("Post-migration row counts:\n%s", _format_snapshot(post))

    failed = False
    for table in PER_USER_TABLES:
        if post[table]["null_tenant"] != 0:
            log.error(
                "Validation FAILED: %s still has %d NULL-tenant rows",
                table, post[table]["null_tenant"],
            )
            failed = True
        if post[table]["total"] != pre[table]["total"]:
            log.error(
                "Validation FAILED: %s total changed pre=%d post=%d",
                table, pre[table]["total"], post[table]["total"],
            )
            failed = True
    if failed:
        return 1

    # 6. Spot check
    log.info("Spot check — 10 most recent positions visible to tenant:")
    rows = _spot_check_positions(args.user_id, limit=10)
    if not rows:
        log.info("  (no positions migrated)")
    else:
        for r in rows:
            log.info(
                "  pos #%-5d  %-10s  status=%-9s  entry=%s  exit=%s  pnl=%s",
                r["id"], r["symbol"], r["status"],
                r["entry_ts"], r["exit_ts"], r["pnl_usd"],
            )

    log.info("=" * 60)
    log.info("Migration complete. Exit 0.")
    log.info("=" * 60)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--user-id", type=int, required=True,
        help="Target users.id whose ownership stamps NULL-tenant rows.",
    )
    parser.add_argument(
        "--initial-balance", type=float, default=INITIAL_CAPITAL_DEFAULT,
        help=f"Anchor balance for the capital row (default ${INITIAL_CAPITAL_DEFAULT}).",
    )
    mode_grp = parser.add_mutually_exclusive_group()
    mode_grp.add_argument(
        "--dry-run", action="store_true",
        help="Preview only (default behavior — no writes).",
    )
    mode_grp.add_argument(
        "--execute", action="store_true",
        help="Apply changes for real. Required for any write.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Overwrite existing capital row (default: refuse).",
    )
    args = parser.parse_args()

    if args.initial_balance < 0:
        log.error("--initial-balance must be >= 0 (got %s)", args.initial_balance)
        return 2

    return run(args)


if __name__ == "__main__":
    sys.exit(main())
