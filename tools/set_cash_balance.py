"""Fija el saldo no-posición (cash/futuros) de un tenant para el equity en vivo.

`equity_real = cash_balance + Σ(holds EXTERNAL × precio_actual)`. Este tool fija
el componente `cash_balance` (la parte que NO está en posiciones trackeadas, p.ej.
el saldo de futuros de papá). Los holds se marcan solos a precio actual; el cash
se actualiza de vez en cuando con este tool. NO toca capital.balance ni el
kill-switch (display-only).

Usa la función legítima `db.capital.db_set_cash_balance`.

Usage (en el server, contra el DB_FILE de prod):
    python -m tools.set_cash_balance --tenant 2 --cash 81.63 [--dry-run]

Spec: docs/superpowers/specs/es/2026-06-09-posiciones-externas-control-domain-spec.md (v0.1.5).
"""
from __future__ import annotations

import argparse


def main() -> int:
    from db.capital import db_get_capital, db_set_cash_balance
    from db.transaction import transaction

    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", type=int, required=True)
    ap.add_argument("--cash", type=float, required=True,
                    help="saldo no-posición en USD (cash/futuros)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with transaction() as con:
        before = db_get_capital(con, args.tenant)
        before_cash = before.get("cash_balance_usd") if before else None
        if args.dry_run:
            print(f"[dry-run] tenant {args.tenant}: cash_balance {before_cash} -> {args.cash}")
            raise SystemExit(0)
        row = db_set_cash_balance(con, args.tenant, args.cash)

    print(f"cash_balance tenant {args.tenant}: antes {before_cash} -> ahora {row['cash_balance_usd']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
