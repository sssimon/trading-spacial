"""Equity en vivo (real_equity) — marca los holds del operador a precio actual.

`equity_real = cash_balance_usd + Σ(posiciones EXTERNAL abiertas: qty × precio)`.

Display-only y on-read: se computa fresco en cada lectura, NO persiste, NO toca
`capital.balance` ni el `portfolio_dd` del kill-switch. Las bolsas que el operador
aguanta por fuera (EXTERNAL) son sus tenencias spot reales; su valor se marca al
precio del último scan. Las posiciones INTERNAL (apalancadas, de señal) NO entran
aquí: su valor de equity no es qty×precio (notional ≠ equity).

Spec: docs/superpowers/specs/es/2026-06-09-posiciones-externas-control-domain-spec.md (v0.1.5).
"""
from __future__ import annotations

import sqlite3
from typing import Mapping, Optional

from db.capital import db_get_capital


def compute_real_equity(
    con: sqlite3.Connection,
    *,
    tenant_id: int,
    price_lookup: Mapping[str, float],
) -> dict:
    """equity_real = cash + Σ(holds EXTERNAL × precio). Cómputo puro, read-only.

    `price_lookup`: symbol → precio actual. Un símbolo sin precio se lista en
    `missing_prices` y NO se suma (no se inventa un valor).
    """
    cap = db_get_capital(con, tenant_id)
    cash = float(cap["cash_balance_usd"]) if cap and cap.get("cash_balance_usd") is not None else 0.0

    rows = con.execute(
        "SELECT symbol, qty FROM positions "
        "WHERE tenant_id = ? AND status = 'open' AND control_domain = 'EXTERNAL' "
        "ORDER BY symbol",
        (tenant_id,),
    ).fetchall()

    holds: list[dict] = []
    holds_value = 0.0
    missing: list[str] = []
    for r in rows:
        symbol = r["symbol"] if isinstance(r, sqlite3.Row) else r[0]
        qty = r["qty"] if isinstance(r, sqlite3.Row) else r[1]
        price: Optional[float] = price_lookup.get(symbol)
        if price is None or qty is None:
            missing.append(symbol)
            continue
        value = float(qty) * float(price)
        holds_value += value
        holds.append({
            "symbol": symbol,
            "qty": float(qty),
            "price": float(price),
            "value_usd": round(value, 4),
        })

    return {
        "cash_balance_usd": round(cash, 4),
        "holds": holds,
        "holds_value_usd": round(holds_value, 4),
        "real_equity_usd": round(cash + holds_value, 4),
        "missing_prices": missing,
    }
