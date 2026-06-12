"""Helpers SQL puros del ledger de conducta (instrumento, Fase 1).

Reciben `con` (no abren transacción propia) — capa de helpers SQL del proyecto.
La tabla conduct_episodes guarda un EpisodioDeConducción REALIZED por posición
falsada: la conducta medida vs. el plan derivado de D.1, con su procedencia
(observado|declarado). Escrita solo por tools.lifecycle_falsifier.

Spec: docs/superpowers/specs/es/2026-06-12-instrumento-lifecycle-conducta-design.md §8.
"""
from __future__ import annotations

import sqlite3


def _b(v):
    """bool|None → int|None para SQLite (preserva NULL)."""
    return None if v is None else int(bool(v))


def db_put_episode(con: sqlite3.Connection, *, position_id, symbol, tenant_id,
                   entry_ts, exit_ts, conduct: dict, plan_json: str,
                   reproduced: bool, created_ts: str) -> None:
    con.execute(
        """INSERT INTO conduct_episodes
           (position_id, symbol, tenant_id, entry_ts, exit_ts, procedencia,
            entry_en_zona, sl_respetado, adherencia_be, rungs_honrados,
            cierre_en_plan, hold_hours, close_reason, plan_json, reproduced, created_ts)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (position_id, symbol, tenant_id, entry_ts, exit_ts, conduct["procedencia"],
         _b(conduct["entry_en_zona"]), _b(conduct["sl_respetado"]),
         _b(conduct["adherencia_be"]), conduct["rungs_honrados"],
         _b(conduct["cierre_en_plan"]), conduct["hold_hours"],
         conduct["close_reason"], plan_json, int(bool(reproduced)), created_ts),
    )


def db_get_episodes(con: sqlite3.Connection, *, tenant_id: int) -> list:
    cur = con.execute(
        "SELECT * FROM conduct_episodes WHERE tenant_id = ? ORDER BY entry_ts",
        (tenant_id,),
    )
    return cur.fetchall()
