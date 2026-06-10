"""CLI one-shot: onboardea la API key read-only spot del tenant (gate BNC-9).

Antes de guardar UNA key real, exige: (a) lectura spot OK; (b) la sonda
order/test confirma trading DESHABILITADO; (c) IP-whitelist declarada. Sin los
tres, NO persiste. La secret se cifra antes del INSERT.

Usage (en el server, contra DB_FILE = prod):
    python -m tools.set_binance_key --tenant 2 --api-key <PUB> --secret <SECRET> --ip-whitelisted

Spec: docs/superpowers/specs/es/2026-06-10-conexion-binance-solo-lectura-spec.md §2.4 (BNC-9).
"""
from __future__ import annotations

import argparse
import sqlite3

from db.binance_credentials import db_upsert_binance_credential


def onboard_credential(con, *, tenant_id, api_key, secret, client, ip_whitelisted):
    """Gate BNC-9 + persistencia. `client` = BinanceAccountClient (inyectable para test)."""
    if not ip_whitelisted:
        raise ValueError("IP-whitelist obligatorio (BNC-9): la IP estática del VPS "
                         "debe estar en la key de Binance antes de aceptarla.")
    client.get_spot_balances()                       # (a) lectura spot OK (lanza si -2015)
    if not client.probe_trading_disabled():          # (b) trading debe estar OFF
        raise ValueError("la key tiene trading habilitado; v0.1 exige read-only. "
                         "Crea una key con SOLO 'Enable Reading' y vuelve a intentar.")
    db_upsert_binance_credential(
        con, tenant_id=tenant_id, api_key_public=api_key, secret_plaintext=secret,
        scope_detected="READ_ONLY_SPOT", ip_whitelisted=True,
    )


def main() -> int:
    from db.transaction import transaction
    from data.providers.binance_account import BinanceAccountClient, get_server_time_offset_ms

    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", type=int, required=True)
    ap.add_argument("--api-key", required=True)
    ap.add_argument("--secret", required=True)
    ap.add_argument("--ip-whitelisted", action="store_true",
                    help="confirma que la IP del VPS está whitelisted en Binance")
    args = ap.parse_args()

    client = BinanceAccountClient(
        api_key=args.api_key, secret=args.secret,
        server_time_offset_ms=get_server_time_offset_ms(),
    )
    with transaction() as con:
        con.row_factory = sqlite3.Row
        onboard_credential(con, tenant_id=args.tenant, api_key=args.api_key,
                           secret=args.secret, client=client,
                           ip_whitelisted=args.ip_whitelisted)
    print("Credencial Binance read-only spot guardada para tenant {}.".format(args.tenant))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
