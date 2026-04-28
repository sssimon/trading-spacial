"""Shared FastAPI dependencies (auth, etc.).

verify_api_key replicates the guard from btc_api.py:267-274 byte-for-byte:
- Uses Security(APIKeyHeader) so the X-API-Key header shows up in Swagger.
- Reads cfg["api_key"] (which load_config already merges from the
  TRADING_API_KEY env var via the env_map at btc_api.py:231-247).
- Empty/missing api_key in config → open access (backward compatible
  with dev setups that have no api_key configured).
- Constant-time compare via hmac.compare_digest.

No separate env-var fallback: load_config() is the single source of truth.
"""
from __future__ import annotations

import hmac

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(key: str = Security(_api_key_header)) -> None:
    """Verify API key for sensitive endpoints. If no key configured, allow all."""
    # Lazy import to avoid circular dep with api/config.py once that lands (PR2).
    from api.config import load_config  # noqa: PLC0415

    cfg = load_config()
    expected = cfg.get("api_key", "").strip()
    if not expected:
        return  # No key configured = open access (backward compatible)
    if not key or not hmac.compare_digest(key, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
