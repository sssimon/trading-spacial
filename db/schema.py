"""DB schema — table definitions and migrations.

Extracted from btc_api.py:859-1107 in PR0 of the api+db domain refactor (2026-04-27).

init_db() is idempotent: CREATE TABLE IF NOT EXISTS for all tables, plus
ALTER TABLE statements wrapped in try/except to handle the case where
the column already exists (sqlite3 has no IF NOT EXISTS for ALTER).

Tables:
- scans (one row per scan; signal=1 if score reached threshold)
- webhooks_sent (audit trail of webhook deliveries)
- positions (open/closed positions; CRUD via db/positions.py in PR4)
- signal_outcomes (1h/4h/24h price tracking for back-validation)
- tune_results (auto-tune proposal lifecycle)
- notifications_sent (in-app notifications)
- symbol_health + symbol_health_events (kill-switch v1 health state)
- kill_switch_decisions + kill_switch_v2_state + kill_switch_v2_baseline
  + kill_switch_recommendations (kill-switch v2)
- portfolio_health_events (portfolio-level circuit breaker)
"""
from __future__ import annotations

import logging
import sqlite3

from db.connection import _resolve_db_file, get_db

log = logging.getLogger("db.schema")


def init_db() -> None:
    """Create or migrate all tables. Idempotent."""
    con = get_db()
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""
        CREATE TABLE IF NOT EXISTS scans (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT    NOT NULL,
            symbol      TEXT    NOT NULL DEFAULT 'BTCUSDT',
            estado      TEXT    NOT NULL,
            señal       INTEGER NOT NULL DEFAULT 0,
            setup       INTEGER NOT NULL DEFAULT 0,
            price       REAL,
            lrc_pct     REAL,
            rsi_1h      REAL,
            score       INTEGER,
            score_label TEXT,
            macro_ok    INTEGER,
            gatillo     INTEGER,
            payload     TEXT
        )
    """)
    # Migración: agregar columna symbol si la tabla ya existía sin ella
    try:
        con.execute("ALTER TABLE scans ADD COLUMN symbol TEXT NOT NULL DEFAULT 'BTCUSDT'")
        log.info("DB migrada: columna 'symbol' añadida.")
    except sqlite3.OperationalError:
        pass  # columna ya existe

    con.execute("""
        CREATE TABLE IF NOT EXISTS webhooks_sent (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id INTEGER REFERENCES scans(id),
            ts      TEXT,
            url     TEXT,
            status  INTEGER,
            ok      INTEGER
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id     INTEGER REFERENCES scans(id),
            symbol      TEXT    NOT NULL,
            direction   TEXT    NOT NULL DEFAULT 'LONG',
            status      TEXT    NOT NULL DEFAULT 'open',
            entry_price REAL    NOT NULL,
            entry_ts    TEXT    NOT NULL,
            sl_price    REAL,
            tp_price    REAL,
            size_usd    REAL,
            qty         REAL,
            exit_price  REAL,
            exit_ts     TEXT,
            exit_reason TEXT,
            pnl_usd     REAL,
            pnl_pct     REAL,
            atr_entry   REAL,
            be_mult     REAL,
            notes       TEXT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS signal_outcomes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id         INTEGER UNIQUE REFERENCES scans(id),
            symbol          TEXT    NOT NULL,
            signal_ts       TEXT    NOT NULL,
            signal_price    REAL    NOT NULL,
            score           INTEGER,
            macro_ok        INTEGER,

            -- Performance medida en intervalos
            price_1h        REAL,
            price_4h        REAL,
            price_24h       REAL,

            -- Puntos extremos en 24h
            max_runup_pct   REAL,  -- mejor retorno %
            max_drawdown_pct REAL,  -- peor retorno %

            status          TEXT NOT NULL DEFAULT 'pending', -- 'pending' | 'completed'
            last_checked_ts TEXT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS tune_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            results_json TEXT,
            report_md TEXT,
            applied_ts TEXT,
            changes_count INTEGER DEFAULT 0
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS notifications_sent (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type      TEXT    NOT NULL,
            event_key       TEXT    NOT NULL,
            priority        TEXT    NOT NULL DEFAULT 'info',
            payload_json    TEXT    NOT NULL,
            channels_sent   TEXT    NOT NULL,
            delivery_status TEXT    NOT NULL DEFAULT 'ok',
            sent_at         TEXT    NOT NULL,
            read_at         TEXT,
            error_log       TEXT
        )
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_notif_sent_unread
            ON notifications_sent(sent_at DESC) WHERE read_at IS NULL
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS symbol_health (
            symbol              TEXT PRIMARY KEY,
            state               TEXT NOT NULL DEFAULT 'NORMAL',
            state_since         TEXT NOT NULL,
            last_evaluated_at   TEXT NOT NULL,
            last_metrics_json   TEXT,
            manual_override     INTEGER NOT NULL DEFAULT 0
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS symbol_health_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol          TEXT NOT NULL,
            from_state      TEXT NOT NULL,
            to_state        TEXT NOT NULL,
            trigger_reason  TEXT NOT NULL,
            metrics_json    TEXT NOT NULL,
            ts              TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_health_events_symbol
            ON symbol_health_events(symbol, ts DESC)
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS kill_switch_decisions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ts              TEXT NOT NULL,
            scan_id         INTEGER,
            symbol          TEXT NOT NULL,
            engine          TEXT NOT NULL,
            per_symbol_tier TEXT NOT NULL,
            portfolio_tier  TEXT NOT NULL,
            velocity_active INTEGER DEFAULT 0,
            size_factor     REAL NOT NULL,
            skip            INTEGER NOT NULL,
            reasons_json    TEXT,
            slider_value    REAL
        )
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_ks_decisions_ts
            ON kill_switch_decisions(ts)
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_ks_decisions_symbol_ts
            ON kill_switch_decisions(symbol, ts)
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS kill_switch_v2_state (
            symbol                    TEXT PRIMARY KEY,
            velocity_cooldown_until   TEXT,
            velocity_last_trigger_ts  TEXT,
            updated_at                TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS kill_switch_v2_baseline (
            symbol         TEXT PRIMARY KEY,
            baseline_wr    REAL NOT NULL,
            baseline_sigma REAL NOT NULL,
            trades_count   INTEGER NOT NULL,
            computed_at    TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS kill_switch_recommendations (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ts              TEXT NOT NULL,
            triggered_by    TEXT NOT NULL,
            slider_value    REAL,
            projected_pnl   REAL,
            projected_dd    REAL,
            status          TEXT NOT NULL,
            applied_ts      TEXT,
            applied_by      TEXT,
            report_json     TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_recommendations_ts
            ON kill_switch_recommendations(ts)
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_health_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            from_tier       TEXT NOT NULL,
            to_tier         TEXT NOT NULL,
            reason          TEXT NOT NULL,
            dd_pct          REAL,
            concurrent      INTEGER,
            ts              TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_portfolio_events_ts
            ON portfolio_health_events(ts DESC)
    """)
    con.commit()
    con.close()
    log.info(f"DB inicializada: {_resolve_db_file()}")

    # Migrate: add atr_entry and be_mult columns if missing
    try:
        con_mig = get_db()
        cols = [r[1] for r in con_mig.execute("PRAGMA table_info(positions)").fetchall()]
        if "atr_entry" not in cols:
            con_mig.execute("ALTER TABLE positions ADD COLUMN atr_entry REAL")
            con_mig.commit()
            log.info("DB migration: added atr_entry column to positions")
        if "be_mult" not in cols:
            con_mig.execute("ALTER TABLE positions ADD COLUMN be_mult REAL")
            con_mig.commit()
            log.info("DB migration: added be_mult column to positions")
        con_mig.close()
    except Exception as e:
        log.warning(f"DB migration check: {e}")

    # B5 PROBATION migration: add 3 columns to symbol_health if missing (#199)
    try:
        con_mig2 = get_db()
        cols2 = [r[1] for r in con_mig2.execute("PRAGMA table_info(symbol_health)").fetchall()]
        for col, ddl in (
            ("probation_trades_remaining", "INTEGER"),
            ("probation_started_at", "TEXT"),
            ("paused_days_at_entry", "INTEGER"),
        ):
            if col not in cols2:
                con_mig2.execute(f"ALTER TABLE symbol_health ADD COLUMN {col} {ddl}")
                con_mig2.commit()
                log.info(f"DB migration: added {col} column to symbol_health")
        con_mig2.close()
    except Exception as e:
        log.warning(f"DB migration B5 PROBATION: {e}")
