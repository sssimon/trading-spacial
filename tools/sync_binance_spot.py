"""CLI one-shot: corre la reconciliación spot para un tenant (Cassian cut-line).

Lee la credencial cifrada, descifra en memoria, consulta balances, reconcilia.
Maneja el estado de credencial fail-closed (AUTH_FAILED/RATE_BANNED/CLOCK_SKEW).
Correrlo a mano una vez al día ya entrega el valor central (dejar de teclear qty).
El auto-loop en el ciclo de scan = v0.1.1.

Usage: python -m tools.sync_binance_spot --tenant 2

Spec: docs/superpowers/specs/es/2026-06-10-conexion-binance-solo-lectura-spec.md §4, §7.
"""
from __future__ import annotations

import argparse
import sqlite3

from binance_sync import autocreate_spot_holdings, reconcile_spot
from data.providers.binance_account import (
    BinanceAccountClient, BinanceAuthError, BinanceClockSkew, BinanceRateBanned,
    BinanceTransportError, get_server_time_offset_ms,
)
from db.binance_credentials import (
    db_get_binance_credential_raw, db_get_decrypted_secret, db_set_credential_status,
)


class _DryRunAbort(Exception):
    """Sentinel para abortar la tx en --dry-run (rollback: no persiste nada)."""


def sync_tenant(
    con: sqlite3.Connection, tenant_id: int, *, autocreate: bool = False, dry_run: bool = False,
) -> dict:
    """Reconcilia spot para un tenant. Devuelve el reporte o {status: ...} si falla.
    Una credencial no-ACTIVE no se sincroniza (fail-closed).

    `autocreate` (v0.2): tras reconciliar, auto-crea filas AUTO_DERIVED de los holds
    nuevos (no-registrados) desde el trade history. `dry_run`: reporta qué crearía
    sin escribir (el CLI además hace rollback de la tx)."""
    cred = db_get_binance_credential_raw(con, tenant_id)
    if cred is None:
        return {"status": "NO_CREDENTIAL"}
    if cred["status"] != "ACTIVE":
        return {"status": cred["status"], "skipped": True}

    secret = db_get_decrypted_secret(con, tenant_id)
    try:
        client = BinanceAccountClient(
            api_key=cred["api_key_public"], secret=secret,
            server_time_offset_ms=get_server_time_offset_ms(),
        )
        balances = client.get_spot_balances()
    except BinanceAuthError:
        db_set_credential_status(con, tenant_id, "AUTH_FAILED")
        return {"status": "AUTH_FAILED"}
    except BinanceClockSkew:
        db_set_credential_status(con, tenant_id, "CLOCK_SKEW")
        return {"status": "CLOCK_SKEW"}
    except BinanceRateBanned:
        db_set_credential_status(con, tenant_id, "RATE_BANNED")
        return {"status": "RATE_BANNED"}
    except BinanceTransportError:
        # Blip de red transitorio: NO cambia el estado de la credencial (sigue
        # ACTIVE), se reintenta el próximo ciclo. Fail-soft, no fail-closed —
        # un transporte caído no es un problema de la credencial. El mensaje ya
        # viene scrubbeado (sin la firma) desde el cliente.
        return {"status": "TRANSPORT_ERROR", "transient": True}

    report = reconcile_spot(con, tenant_id=tenant_id, balances=balances)
    report["status"] = "ACTIVE"
    if autocreate:
        report["autocreate"] = autocreate_spot_holdings(
            con, tenant_id=tenant_id, client=client, balances=balances, dry_run=dry_run,
        )
    return report


def main() -> int:
    from db.transaction import transaction
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", type=int, required=True)
    ap.add_argument("--autocreate", action="store_true",
                    help="v0.2: auto-crea filas AUTO_DERIVED de holds nuevos desde el trade history")
    ap.add_argument("--dry-run", action="store_true",
                    help="con --autocreate: reporta qué crearía SIN escribir nada (rollback)")
    args = ap.parse_args()
    holder: dict = {}
    try:
        with transaction() as con:
            con.row_factory = sqlite3.Row
            holder["report"] = sync_tenant(
                con, args.tenant, autocreate=args.autocreate, dry_run=args.dry_run,
            )
            if args.dry_run:
                raise _DryRunAbort()   # rollback: el dry-run NO persiste (ni reconcile)
    except _DryRunAbort:
        pass
    print(holder["report"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
