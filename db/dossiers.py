"""Helpers SQL puros de la caché de dossiers (capa 1). Reciben `con`, corren
SQL, devuelven data. Sin transaction(), sin side-effects. Spec §4."""
from __future__ import annotations

import sqlite3


def db_get_dossier(con: sqlite3.Connection, symbol: str) -> dict | None:
    """Fila de caché {dossier_json, generated_at} para el símbolo, o None."""
    row = con.execute(
        "SELECT dossier_json, generated_at FROM project_dossiers WHERE symbol=?",
        (symbol,),
    ).fetchone()
    if row is None:
        return None
    return {"dossier_json": row[0], "generated_at": row[1]}


def db_put_dossier(con: sqlite3.Connection, *, symbol: str, dossier_json: str,
                   generated_at: str) -> None:
    """Upsert del dossier (global, PK por symbol)."""
    con.execute(
        "INSERT OR REPLACE INTO project_dossiers(symbol, dossier_json, generated_at) "
        "VALUES (?,?,?)", (symbol, dossier_json, generated_at),
    )
