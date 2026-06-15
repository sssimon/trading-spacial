"""Liveness del scanner derivada de la DB (no de memoria de proceso).

Para que una API web-only reporte la verdad del trading-scanner.service
incluso cuando no hay scanner thread corriendo en el mismo proceso.

Cumple el non-negotiable #8: el estado vivo cruza un límite de proceso,
así que su frescura debe vivir en el contrato (LiveSnapshot), NO en memoria.

Schema verificado en db/schema.py líneas 126-142 y db/signals.py líneas 39-44:
- Columna de tiempo: ts (TEXT NOT NULL)
- Columna de señal:  señal (INTEGER NOT NULL DEFAULT 0, vale 1 cuando activa)
"""
from __future__ import annotations

import sqlite3

from db.transaction import snapshot_connection as _snapshot_connection
from freshness import LiveSnapshot

_EMPTY_FACTS = {"last_scan_ts": None}


def _query_scanner_facts() -> dict:
    """Devuelve el timestamp del último scan — BARATO.

    Usa `ORDER BY id DESC LIMIT 1` (la PK `id` está indexada → ~0.1 ms),
    NO `MAX(ts)`/`COUNT(*)` que son full-scans (~8 s c/u) sobre la tabla
    `scans` grande de prod y tumbaban el health probe (regresión PR1: el
    `/health` con tres full-scans tardaba >20 s → timeout del deploy).
    La frescura (#8) solo necesita el último ts; los conteos eran stats
    informativas y NO valían un full-scan en un endpoint caliente.

    snapshot_connection (WAL-concurrent, sin writer lock). Tolerante a la
    tabla ausente: una API web-only que consulta antes de que el
    scanner-service cree el schema degrada a 'muerto', nunca 500.
    """
    try:
        with _snapshot_connection() as con:
            row = con.execute(
                "SELECT ts FROM scans ORDER BY id DESC LIMIT 1"
            ).fetchone()
            last_scan_ts = row[0] if row else None
    except sqlite3.OperationalError:
        return dict(_EMPTY_FACTS)
    return {"last_scan_ts": _normalize_scan_ts(last_scan_ts)}


def _normalize_scan_ts(ts: str | None) -> str | None:
    """El `ts` de la tabla `scans` se guarda como 'YYYY-MM-DD HH:MM:SS UTC'
    (NO ISO-8601 — viene del campo `timestamp` del reporte del scanner). El
    sufijo ' UTC' rompe `datetime.fromisoformat` en `LiveSnapshot._edad_seg`
    → edad=None → 'muerto' permanente. Lo normalizamos a un offset parseable.
    Idempotente para ts ya en ISO (sin ' UTC' que reemplazar)."""
    if not ts:
        return None
    return ts.replace(" UTC", "+00:00")


def scanner_liveness(*, umbral_seg: float = 900.0) -> dict:
    """Liveness del scanner (último scan ts + frescura) envuelto en LiveSnapshot.

    Args:
        umbral_seg: segundos antes de considerar el scanner 'rancio'.
                    Default 900 s (3× el intervalo nominal de 300 s).

    Returns:
        dict con keys: last_scan_ts, frescura{estado, edad_seg, generated_at,
        umbral_seg}. estado es 'fresco' | 'rancio' | 'muerto'.
    """
    facts = _query_scanner_facts()
    payload = {"last_scan_ts": facts["last_scan_ts"]}
    return LiveSnapshot(
        payload=payload,
        generated_at=facts["last_scan_ts"],
        umbral_seg=umbral_seg,
    ).to_response()
