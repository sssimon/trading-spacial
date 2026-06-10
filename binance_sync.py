"""Reconciliación SPOT read-only: balances reales de Binance → filas EXTERNAL.

v0.1 es UPDATE-only + reporte (NO inserta): actualiza la `qty` de las filas
EXTERNAL spot registradas (autoridad = Binance, §4.1), adopta `market='SPOT'`
en el primer sync (bootstrap), y reporta holds no-registrados para registro
manual (no auto-crea: entry_price es NOT NULL, sin cost-basis = v0.2).

`closed_pending` (derivado de qty≈0 con credencial ACTIVE) = señal para que el
humano confirme el cierre (CD-5: el sistema no escribe `closed`). El equity se
auto-corrige porque qty→0 (compute_real_equity hace qty×precio).

Spec: docs/superpowers/specs/es/2026-06-10-conexion-binance-solo-lectura-spec.md §4.
"""
from __future__ import annotations

import sqlite3

_QUOTES = ("USDT", "USDC", "BUSD", "FDUSD")


def base_asset(symbol: str) -> str:
    """'BTCUSDT' → 'BTC'. Asume quote en _QUOTES (spot v0.1)."""
    s = symbol.upper()
    for q in _QUOTES:
        if s.endswith(q):
            return s[: -len(q)]
    return s


def reconcile_spot(
    con: sqlite3.Connection, *, tenant_id: int, balances: dict[str, float], dust: float = 1e-6,
) -> dict:
    """Reconcilia las filas EXTERNAL spot del tenant contra los balances reales.

    `balances`: {asset: free+locked} de get_spot_balances(). El caller posee la tx.
    """
    rows = con.execute(
        "SELECT id, symbol, qty FROM positions "
        "WHERE tenant_id=? AND status='open' AND control_domain='EXTERNAL' "
        "AND (market='SPOT' OR market IS NULL)",
        (tenant_id,),
    ).fetchall()

    reconciled: list[str] = []
    closed_pending: list[str] = []
    tracked_assets: set[str] = set()

    for r in rows:
        symbol = r["symbol"]
        asset = base_asset(symbol)
        tracked_assets.add(asset)
        real_qty = float(balances.get(asset, 0.0))
        # market='SPOT' adoptado en el mismo UPDATE (el trigger lo permite: EXTERNAL).
        con.execute(
            "UPDATE positions SET qty=?, market='SPOT' WHERE id=?",
            (real_qty, r["id"]),
        )
        if real_qty <= dust:
            closed_pending.append(symbol)   # señal de cierre observado (derivado)
        else:
            reconciled.append(symbol)

    # Holds reales no-registrados (asset con balance > dust sin fila): se REPORTAN.
    untracked: list[str] = []
    for asset, amount in balances.items():
        if amount > dust and asset not in tracked_assets and asset not in _QUOTES:
            untracked.append(asset + "USDT")

    return {
        "reconciled": reconciled,
        "closed_pending": closed_pending,
        "untracked": untracked,
    }
