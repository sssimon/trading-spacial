"""Helpers SQL puros de observed_orders (Binance v0.3 — SL/TP observados).

Capa 1 (CLAUDE.md §Database access): reciben `con`, corren SQL, devuelven
data. El write-path (apply_observed_orders) vive en binance_sync.py porque
participa del flujo de sync; aquí solo lecturas para la API.

Spec: docs/superpowers/specs/es/2026-06-11-binance-v03-sl-tp-observados-spec.md §6.
"""
from __future__ import annotations

import sqlite3


def db_get_observed_orders(con: sqlite3.Connection, *, tenant_id: int) -> list[dict]:
    """Órdenes observadas del tenant, ordenadas para presentación estable
    (symbol, kind, qty DESC — la de mayor cobertura primero)."""
    rows = con.execute(
        "SELECT symbol, kind, price, qty, pct_holding, order_id, oco_group, observed_at "
        "FROM observed_orders WHERE tenant_id=? "
        "ORDER BY symbol, kind, qty DESC",
        (tenant_id,),
    ).fetchall()
    return [dict(r) for r in rows]
