"""Fija el equity REAL de un tenant en la tabla `capital`.

El balance que la plataforma muestra es NOCIONAL ($10k de arranque + roll-in de
P&L de trades INTERNAL). Para reflejar la cuenta real del operador (p.ej. el
total en Binance de papá), este tool fija balance = equity y **resetea el peak
al mismo valor** con drawdown 0.

El reset del peak NO es opcional: `db_upsert_capital` preserva el peak viejo si
no se le pasa uno. Bajar el balance dejando el peak nocional (más alto) haría que
el sistema calcule un drawdown enorme (p.ej. (10138-2026)/10138 ≈ 80%) y dispare
el kill-switch de portafolio. Por eso peak=equity, dd=0 (baseline real fresco).

Usa la función legítima `db.capital.db_upsert_capital` (NO SQL crudo).

Usage (en el server, contra el DB_FILE de prod):
    python -m tools.set_capital --tenant 2 --equity 2026 [--dry-run]

Nota de semántica: a partir de aquí el balance es real; los cierres de trades
INTERNAL futuros rodarán su P&L sobre este valor. Las posiciones EXTERNAL no
ruedan a capital (CD-1). Re-correr el tool con el equity actualizado de Binance
es el flujo previsto (el saldo real se mueve).
"""
from __future__ import annotations

import argparse
import sqlite3

from db.capital import db_upsert_capital


def set_capital(con: sqlite3.Connection, *, tenant_id: int, equity: float) -> dict:
    """balance=peak=equity, dd=0. Baseline real fresco (reset de peak obligatorio).
    El caller posee la transacción y el commit."""
    return db_upsert_capital(
        con,
        tenant_id,
        balance=float(equity),
        peak_balance=float(equity),
        max_drawdown_pct=0.0,
    )


def main() -> int:
    from db.capital import db_get_capital
    from db.transaction import transaction

    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", type=int, required=True)
    ap.add_argument("--equity", type=float, required=True)
    ap.add_argument("--dry-run", action="store_true",
                    help="no escribe; reporta el antes y el después propuesto")
    args = ap.parse_args()

    with transaction() as con:
        before = db_get_capital(con, args.tenant)
        if args.dry_run:
            print(f"[dry-run] tenant {args.tenant}: antes={before}")
            print(f"[dry-run] -> balance={args.equity} peak={args.equity} dd=0.0")
            raise SystemExit(0)
        row = set_capital(con, tenant_id=args.tenant, equity=args.equity)

    print(f"capital tenant {args.tenant}: antes balance="
          f"{before['balance'] if before else None} peak="
          f"{before['peak_balance'] if before else None}")
    print(f"capital tenant {args.tenant}: ahora balance={row['balance']} "
          f"peak={row['peak_balance']} dd={row['max_drawdown_pct']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
