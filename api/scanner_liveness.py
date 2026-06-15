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

from db.transaction import snapshot_connection as _snapshot_connection
from freshness import LiveSnapshot


def _query_scanner_facts() -> dict:
    """Lee la DB y devuelve conteos + timestamp del último scan.

    Usa snapshot_connection (WAL-concurrent, sin writer lock) porque
    esta lectura es terminal — se serializa a la respuesta HTTP, no
    alimenta ninguna mutación posterior.
    """
    with _snapshot_connection() as con:
        last_scan_ts = con.execute("SELECT MAX(ts) FROM scans").fetchone()[0]
        scans_total = con.execute("SELECT COUNT(*) FROM scans").fetchone()[0]
        signals_total = con.execute(
            'SELECT COUNT(*) FROM scans WHERE "señal" = 1'
        ).fetchone()[0]
    return {
        "last_scan_ts": last_scan_ts,
        "scans_total": scans_total,
        "signals_total": signals_total,
    }


def scanner_liveness(*, umbral_seg: float = 900.0) -> dict:
    """Devuelve el estado de liveness del scanner envuelto en LiveSnapshot.

    Args:
        umbral_seg: segundos antes de considerar el scanner 'rancio'.
                    Default 900 s (15 min) — el intervalo de scan nominal.

    Returns:
        dict con keys: scans_total, signals_total, last_scan_ts, frescura
        donde frescura = {estado, edad_seg, generated_at, umbral_seg}.
        estado es 'fresco' | 'rancio' | 'muerto'.
    """
    facts = _query_scanner_facts()
    payload = {
        "scans_total": facts["scans_total"],
        "signals_total": facts["signals_total"],
        "last_scan_ts": facts["last_scan_ts"],
    }
    return LiveSnapshot(
        payload=payload,
        generated_at=facts["last_scan_ts"],
        umbral_seg=umbral_seg,
    ).to_response()
