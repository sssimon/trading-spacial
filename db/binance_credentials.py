"""Capa DB per-tenant para credenciales Binance (secret cifrada at-rest).

Una fila por tenant_id (UNIQUE INDEX idx_binance_cred_tenant). La secret SOLO
entra/sale cifrada vía db.secret_box; nunca se devuelve en claro salvo por
`db_get_decrypted_secret` (que descifra en memoria justo para firmar).

Spec: docs/superpowers/specs/es/2026-06-10-conexion-binance-solo-lectura-spec.md §2.2, §2.4.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from db.secret_box import decrypt_secret, encrypt_secret

VALID_STATUSES = {"ACTIVE", "AUTH_FAILED", "REVOKED", "RATE_BANNED", "CLOCK_SKEW"}


def db_upsert_binance_credential(
    con: sqlite3.Connection,
    *,
    tenant_id: int,
    api_key_public: str,
    secret_plaintext: str,
    scope_detected: Optional[str] = None,
    ip_whitelisted: bool = False,
    key_version: int = 1,
) -> None:
    """Inserta o reemplaza la credencial del tenant. La secret se cifra ANTES
    del INSERT (nunca en claro en reposo). El caller posee la transacción."""
    now = datetime.now(timezone.utc).isoformat()
    secret_enc = encrypt_secret(secret_plaintext)
    existing = con.execute(
        "SELECT id FROM binance_credentials WHERE tenant_id=?", (tenant_id,),
    ).fetchone()
    if existing is None:
        con.execute(
            "INSERT INTO binance_credentials (tenant_id, api_key_public, secret_enc, "
            "key_version, scope_detected, ip_whitelisted, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)",
            (tenant_id, api_key_public, secret_enc, key_version, scope_detected,
             1 if ip_whitelisted else 0, now, now),
        )
    else:
        con.execute(
            "UPDATE binance_credentials SET api_key_public=?, secret_enc=?, key_version=?, "
            "scope_detected=?, ip_whitelisted=?, status='ACTIVE', updated_at=? WHERE tenant_id=?",
            (api_key_public, secret_enc, key_version, scope_detected,
             1 if ip_whitelisted else 0, now, tenant_id),
        )


def db_get_binance_credential_raw(con: sqlite3.Connection, tenant_id: int) -> Optional[dict]:
    """Fila cruda (incluye secret_enc cifrada). NO exponer al frontend."""
    row = con.execute(
        "SELECT * FROM binance_credentials WHERE tenant_id=?", (tenant_id,),
    ).fetchone()
    return dict(row) if row else None


def db_get_decrypted_secret(con: sqlite3.Connection, tenant_id: int) -> Optional[str]:
    """Descifra la secret en memoria (justo antes de firmar). None si no hay fila."""
    row = db_get_binance_credential_raw(con, tenant_id)
    if row is None:
        return None
    return decrypt_secret(row["secret_enc"])


def db_set_credential_status(con: sqlite3.Connection, tenant_id: int, status: str) -> None:
    """Fija el estado fail-closed (ACTIVE/AUTH_FAILED/REVOKED/RATE_BANNED/CLOCK_SKEW)."""
    assert status in VALID_STATUSES, f"status inválido: {status}"
    now = datetime.now(timezone.utc).isoformat()
    con.execute(
        "UPDATE binance_credentials SET status=?, updated_at=? WHERE tenant_id=?",
        (status, now, tenant_id),
    )


def get_credential_metadata(con: sqlite3.Connection, tenant_id: int) -> Optional[dict]:
    """Metadatos seguros para el frontend: NUNCA la secret, ni cifrada."""
    row = db_get_binance_credential_raw(con, tenant_id)
    if row is None:
        return None
    pub = row["api_key_public"] or ""
    return {
        "tenant_id": row["tenant_id"],
        "api_key_last4": pub[-4:],
        "scope_detected": row["scope_detected"],
        "ip_whitelisted": bool(row["ip_whitelisted"]),
        "status": row["status"],
        "updated_at": row["updated_at"],
    }
