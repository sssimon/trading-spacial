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
import logging
import sqlite3
from datetime import datetime, timezone

from binance_sync import (
    apply_observed_orders, apply_spot_autocreate, classify_open_orders,
    plan_spot_autocreate, reconcile_spot,
)
from data.providers.binance_account import (
    BinanceAccountClient, BinanceAuthError, BinanceClockSkew, BinanceRateBanned,
    BinanceTransportError, get_server_time_offset_ms,
)
from db.binance_credentials import (
    db_get_binance_credential_raw, db_get_decrypted_secret, db_set_credential_status,
)

log = logging.getLogger(__name__)


class _DryRunAbort(Exception):
    """Sentinel para abortar la tx en --dry-run (rollback: no persiste nada)."""


def _persist_credential_status(tenant_id: int, status: str) -> None:
    """Escribe el estado de la credencial en una tx CORTA (fail-closed tras un error
    de I/O). Aislado para no sostener el writer-lock durante la red."""
    from db.transaction import transaction
    with transaction() as con:
        con.row_factory = sqlite3.Row
        db_set_credential_status(con, tenant_id, status)


def sync_tenant(tenant_id: int, *, autocreate: bool = False, dry_run: bool = False) -> dict:
    """Reconcilia spot para un tenant. Devuelve el reporte o {status: ...} si falla.

    ARQUITECTURA DE LOCK (Halberg, revisión holística): TODO el I/O de red (balances
    + el descubrimiento de auto-creación: myTrades/ticker/exchangeInfo) corre en la
    FASE 1, FUERA de cualquier transacción. Solo los writes (reconcile UPDATEs +
    auto-create INSERTs) van en la FASE 2 en una tx CORTA. Así el writer-lock
    (BEGIN IMMEDIATE) NUNCA se sostiene durante la latencia de Binance → no
    reproduce el incidente de contención del login (2026-06-10). Una credencial
    no-ACTIVE no se sincroniza (fail-closed).

    `autocreate` (v0.2): auto-crea filas AUTO_DERIVED de holds nuevos desde el trade
    history. `dry_run`: hace los writes y los revierte (reporta sin persistir).
    """
    from db.transaction import snapshot_connection, transaction

    # ── FASE 1: lecturas (snapshot, sin writer-lock) + I/O de red (sin lock) ──
    with snapshot_connection() as ro:
        cred = db_get_binance_credential_raw(ro, tenant_id)
        if cred is None:
            return {"status": "NO_CREDENTIAL"}
        if cred["status"] != "ACTIVE":
            return {"status": cred["status"], "skipped": True}
        secret = db_get_decrypted_secret(ro, tenant_id)
        existing = {
            r["symbol"] for r in ro.execute(
                "SELECT symbol FROM positions WHERE tenant_id=? AND control_domain='EXTERNAL' "
                "AND status='open'",
                (tenant_id,),
            ).fetchall()
        }

    observed = None
    observed_skip_causa = None
    try:
        client = BinanceAccountClient(
            api_key=cred["api_key_public"], secret=secret,
            server_time_offset_ms=get_server_time_offset_ms(),
        )
        balances = client.get_spot_balances()
        # v0.3: órdenes de protección abiertas. Fallo aquí = paso OMITIDO
        # completo este ciclo (ni snapshot parcial ni limpieza por un fallo
        # de red o payload malformado — eco F8: parcial es incorrecto, no
        # incompleto). Spec §5.4.
        # No se degrada la credencial — fallo del paso ≠ fallo de la credencial.
        try:
            observed = classify_open_orders(client.get_open_orders(), balances)
        except (BinanceAuthError, BinanceClockSkew, BinanceRateBanned,
                BinanceTransportError, KeyError, ValueError, TypeError) as e:
            observed_skip_causa = str(e)
            log.warning("OBSERVED_ORDERS_SKIPPED tenant=%s causa=%s",
                        tenant_id, e)
        plan = plan_spot_autocreate(
            client=client, balances=balances, existing_symbols=existing,
        ) if autocreate else None
    except BinanceAuthError:
        _persist_credential_status(tenant_id, "AUTH_FAILED")
        return {"status": "AUTH_FAILED"}
    except BinanceClockSkew:
        _persist_credential_status(tenant_id, "CLOCK_SKEW")
        return {"status": "CLOCK_SKEW"}
    except BinanceRateBanned:
        _persist_credential_status(tenant_id, "RATE_BANNED")
        return {"status": "RATE_BANNED"}
    except BinanceTransportError:
        # Blip de red transitorio: NO cambia el estado de la credencial (sigue
        # ACTIVE), se reintenta el próximo ciclo. Fail-soft — un transporte caído
        # no es problema de la credencial. El mensaje ya viene scrubbeado.
        return {"status": "TRANSPORT_ERROR", "transient": True}

    # ── FASE 2: writes (tx CORTA, sin I/O). dry-run = rollback. ──
    holder: dict = {}
    try:
        with transaction() as con:
            con.row_factory = sqlite3.Row
            report = reconcile_spot(con, tenant_id=tenant_id, balances=balances)
            report["status"] = "ACTIVE"
            if autocreate:
                created = apply_spot_autocreate(con, tenant_id=tenant_id, plan=plan["plan"])
                report["autocreate"] = {"created": created, "abstained": plan["abstained"]}
            if observed is not None:
                report["observed_orders"] = apply_observed_orders(
                    con, tenant_id=tenant_id, classified=observed,
                    observed_at=datetime.now(timezone.utc).isoformat(),
                )
            else:
                report["observed_orders"] = {"skipped": True, "causa": observed_skip_causa}
            holder["report"] = report
            if dry_run:
                raise _DryRunAbort()   # rollback: el dry-run NO persiste
    except _DryRunAbort:
        pass
    return holder["report"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", type=int, required=True)
    ap.add_argument("--autocreate", action="store_true",
                    help="v0.2: auto-crea filas AUTO_DERIVED de holds nuevos desde el trade history")
    ap.add_argument("--dry-run", action="store_true",
                    help="con --autocreate: reporta qué haría SIN persistir (los writes se revierten)")
    args = ap.parse_args()
    report = sync_tenant(args.tenant, autocreate=args.autocreate, dry_run=args.dry_run)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
