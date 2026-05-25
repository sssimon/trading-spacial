"""Backfill: anchor a user's capital row from a hand-set initial balance.

B.2 #255 — one-shot CLI for the production migration step.

Semantics (locked in pre-reg §2.5):
- If a capital row already exists for `--user-id` and `--force` is absent,
  refuse with exit code 1 (idempotency guard).
- With `--force`, overwrite balance + peak_balance to `--initial-balance`
  and clear max_drawdown_pct.
- Does NOT replay historical closed-position P&L. Initial balance is the
  contractual anchor; pre-script closes are NOT rolled in.

Examples:
    python scripts/backfill_capital_for_user.py --user-id 1 --initial-balance 10000
    python scripts/backfill_capital_for_user.py --user-id 2 --initial-balance 5000 --force
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from db.capital import db_get_capital, db_upsert_capital  # noqa: E402
from db.transaction import transaction  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("backfill_capital")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--user-id", type=int, required=True,
                        help="The auth users.id whose capital to backfill")
    parser.add_argument("--initial-balance", type=float, required=True,
                        help="Anchor balance in USD. Sets balance = peak = this value.")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite if a capital row already exists (default: refuse).")
    args = parser.parse_args()

    if args.initial_balance < 0:
        log.error("--initial-balance must be >= 0 (got %s)", args.initial_balance)
        return 2

    # Task 5 (#446): both helpers require `con` positional.
    with transaction() as con:
        existing = db_get_capital(con, args.user_id)
    if existing is not None and not args.force:
        log.error(
            "Capital row already exists for user_id=%s (balance=%s, peak=%s). "
            "Re-run with --force to overwrite.",
            args.user_id, existing["balance"], existing["peak_balance"],
        )
        return 1

    with transaction() as con:
        row = db_upsert_capital(
            con,
            args.user_id,
            balance=args.initial_balance,
            peak_balance=args.initial_balance,
            max_drawdown_pct=None,
        )
    log.info(
        "Backfilled capital for user_id=%s: balance=%s, peak=%s (force=%s)",
        args.user_id, row["balance"], row["peak_balance"], args.force,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
