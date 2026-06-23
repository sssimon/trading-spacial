"""Auditoría append-only del gate de exposición — feed de calibración + rastro de
honestidad. Escritura en BATCH (una transacción por ciclo, NO N BEGIN IMMEDIATE)
para no ensanchar el burst de writes que ya causó contención de locks (2026-05-29).
tenant_id NULLABLE: la decisión es un hecho de mercado global. Spec §5."""
from __future__ import annotations

from datetime import datetime, timezone

from db.transaction import transaction

_COLS = (
    "motor", "symbol", "estado_regimen", "nivel", "es_alt",
    "regime_frescura", "votos_vivos", "enforced", "umbral_version", "tenant_id",
)


def registrar_decisiones(filas: list[dict]) -> int:
    """Inserta TODAS las filas del ciclo en UNA sola transacción. No-op si vacío."""
    if not filas:
        return 0
    ts = datetime.now(timezone.utc).isoformat()
    params = [
        (
            ts,
            f["motor"],
            f["symbol"],
            f["estado_regimen"],
            f["nivel"],
            int(bool(f["es_alt"])),
            f["regime_frescura"],
            int(f["votos_vivos"]),
            int(bool(f["enforced"])),
            f["umbral_version"],
            f.get("tenant_id"),
        )
        for f in filas
    ]
    with transaction() as con:
        con.executemany(
            """INSERT INTO regime_gate_audit
               (ts, motor, symbol, estado_regimen, nivel, es_alt,
                regime_frescura, votos_vivos, enforced, umbral_version, tenant_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            params,
        )
    return len(params)


def purgar_antiguos(dias: int) -> int:
    """Retención: borra filas con más de `dias` días. Devuelve cuántas borró."""
    from datetime import timedelta
    corte = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat()
    with transaction() as con:
        cur = con.execute("DELETE FROM regime_gate_audit WHERE ts < ?", (corte,))
        return cur.rowcount


def _query_all() -> list[dict]:
    """Helper de test: todas las filas como dicts."""
    with transaction() as con:
        rows = con.execute(
            "SELECT ts, " + ", ".join(_COLS) + " FROM regime_gate_audit ORDER BY id"
        ).fetchall()
    return [dict(zip(("ts", *_COLS), r)) for r in rows]
