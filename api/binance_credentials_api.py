"""Endpoint read-only de ESTADO de la credencial Binance — NUNCA la secret.

Devuelve solo metadatos seguros (existe/no, últimos 4 de la api_key pública,
scope, ip_whitelisted, status). La secret no sale del backend ni cifrada.

Spec: docs/superpowers/specs/es/2026-06-10-conexion-binance-solo-lectura-spec.md §2.4 (BNC-2).
"""
from __future__ import annotations

import sqlite3

from db.binance_credentials import get_credential_metadata


def credential_status_payload(con: sqlite3.Connection, tenant_id: int) -> dict:
    """Payload seguro para el frontend. {connected: False} si no hay credencial."""
    meta = get_credential_metadata(con, tenant_id)
    if meta is None:
        return {"connected": False}
    return {"connected": True, **meta}
