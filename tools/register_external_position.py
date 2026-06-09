"""Registrador one-shot de una posición EXTERNAL (abierta por fuera del sistema).

Camino de nacimiento dedicado para posiciones que el operador abrió en el broker
(p.ej. Binance) sin pasar por la señal del sistema: las marca
`control_domain='EXTERNAL'` para que el sistema las OBSERVE pero nunca las actúe
(no auto-cierre, no MTM de riesgo, no cooldown, no propuesta del copiloto — CD-1).

NO usa el INSERT de señal (`db_create_position_sql`): aquella ruta es para
posiciones INTERNAL nacidas de un scan. Esta escribe `control_domain='EXTERNAL'`,
`scan_id=NULL` (no hubo señal del sistema), `size_usd=qty×entry_price`, sin SL/TP.

Idempotente por (tenant_id, symbol, entry_ts, control_domain='EXTERNAL').

PRECONDICIÓN: el código con la columna `control_domain` + la exención debe estar
DESPLEGADO en el destino ANTES de correr esto. Si se registra en un servidor que
aún corre el código viejo (sin la exención), el scanner viejo auto-cerraría la
fila — el orden es deploy → register.

Usage (en el servidor, contra el DB_FILE configurado = prod):
    python -m tools.register_external_position --tenant 2 --symbol BTCUSDT \
        --direction LONG --qty 0.01967 --entry-price 64390 \
        --entry-ts 2026-06-04T00:56:00+00:00

Spec: docs/superpowers/specs/es/2026-06-09-posiciones-externas-control-domain-spec.md (REV 2 §3).
"""
from __future__ import annotations

import argparse
import sqlite3


def register_external(
    con: sqlite3.Connection,
    *,
    tenant_id: int,
    symbol: str,
    direction: str,
    qty: float,
    entry_price: float,
    entry_ts: str,
) -> dict | None:
    """Registra una posición EXTERNAL. Devuelve la fila creada, o None si ya
    existía (idempotente). El caller posee la transacción y el commit."""
    symbol = symbol.upper()
    direction = direction.upper()
    existing = con.execute(
        "SELECT id FROM positions "
        "WHERE tenant_id=? AND symbol=? AND entry_ts=? AND control_domain='EXTERNAL'",
        (tenant_id, symbol, entry_ts),
    ).fetchone()
    if existing is not None:
        return None

    size_usd = round(qty * entry_price, 4)
    cur = con.execute(
        """INSERT INTO positions
               (scan_id, symbol, direction, status, entry_price, entry_ts,
                sl_price, tp_price, size_usd, qty, tenant_id, control_domain)
           VALUES (NULL, ?, ?, 'open', ?, ?, NULL, NULL, ?, ?, ?, 'EXTERNAL')""",
        (symbol, direction, entry_price, entry_ts, size_usd, qty, tenant_id),
    )
    cur2 = con.execute("SELECT * FROM positions WHERE id=?", (cur.lastrowid,))
    cols = [d[0] for d in cur2.description]
    return dict(zip(cols, cur2.fetchone()))


def main() -> int:
    from db.transaction import transaction

    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", type=int, required=True)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--direction", default="LONG", choices=["LONG", "SHORT"])
    ap.add_argument("--qty", type=float, required=True)
    ap.add_argument("--entry-price", type=float, required=True)
    ap.add_argument("--entry-ts", required=True, help="ISO 8601, p.ej. 2026-06-04T00:56:00+00:00")
    ap.add_argument("--dry-run", action="store_true",
                    help="no escribe; reporta qué haría")
    args = ap.parse_args()

    with transaction() as con:
        con.row_factory = sqlite3.Row
        if args.dry_run:
            existing = con.execute(
                "SELECT id FROM positions WHERE tenant_id=? AND symbol=? "
                "AND entry_ts=? AND control_domain='EXTERNAL'",
                (args.tenant, args.symbol.upper(), args.entry_ts),
            ).fetchone()
            size = round(args.qty * args.entry_price, 4)
            verb = "ya existe (no-op)" if existing else f"crearía EXTERNAL size_usd={size}"
            print(f"[dry-run] {args.symbol.upper()} {args.direction} qty={args.qty} "
                  f"@ {args.entry_price} ({args.entry_ts}) → {verb}")
            # No commit en dry-run: deshacer cualquier estado de la tx.
            raise SystemExit(0)
        row = register_external(
            con, tenant_id=args.tenant, symbol=args.symbol,
            direction=args.direction, qty=args.qty,
            entry_price=args.entry_price, entry_ts=args.entry_ts,
        )
    if row is None:
        print(f"{args.symbol.upper()} @ {args.entry_ts}: ya registrada (no-op).")
    else:
        print(f"Registrada EXTERNAL #{row['id']}: {row['symbol']} {row['direction']} "
              f"qty={row['qty']} @ {row['entry_price']} size_usd={row['size_usd']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
