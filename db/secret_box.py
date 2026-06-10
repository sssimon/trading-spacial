"""Cifrado-at-rest de secretos de broker (Fernet). Master key desde env.

Fernet = AES-128-CBC + HMAC-SHA256 (encrypt-then-MAC). NO es AEAD canónico y
NO soporta associated data. La master key (`TRADING_BINANCE_MASTER_KEY`,
url-safe base64 de 32 bytes) vive en el EnvironmentFile de systemd, fuera de
backup/repo/CI. Fail-closed: sin master key, cualquier operación lanza.

Spec: docs/superpowers/specs/es/2026-06-10-conexion-binance-solo-lectura-spec.md §2.3.
"""
from __future__ import annotations

import os

from cryptography.fernet import Fernet

_ENV_KEY = "TRADING_BINANCE_MASTER_KEY"


def _fernet() -> Fernet:
    raw = os.environ.get(_ENV_KEY)
    if not raw:
        raise RuntimeError(
            _ENV_KEY + " no está en el entorno; el cifrado-at-rest es fail-closed. "
            "Genera una con `python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"` y ponla en el EnvironmentFile."
        )
    return Fernet(raw.encode() if isinstance(raw, str) else raw)


def encrypt_secret(plaintext: str) -> bytes:
    """Cifra un secreto. Devuelve el token Fernet (bytes) para guardar en BLOB."""
    return _fernet().encrypt(plaintext.encode("utf-8"))


def decrypt_secret(token: bytes) -> str:
    """Descifra un token Fernet. Lanza si la master key no corresponde."""
    return _fernet().decrypt(token).decode("utf-8")
