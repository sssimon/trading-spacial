"""DB schema — table definitions and migrations.

Extracted from btc_api.py:859-1107 in PR0 of the api+db domain refactor (2026-04-27).

init_db() is idempotent: CREATE TABLE IF NOT EXISTS for all tables, plus
ALTER TABLE statements wrapped in try/except to handle the case where
the column already exists (sqlite3 has no IF NOT EXISTS for ALTER).

Tables:
- scans (one row per scan; signal=1 if score reached threshold) — GLOBAL
- webhooks_sent (audit trail of webhook deliveries) — GLOBAL
- positions (open/closed positions; CRUD via db/positions.py in PR4) — PER-USER
- signal_outcomes (1h/4h/24h price tracking for back-validation) — PER-USER
- tune_results (auto-tune proposal lifecycle) — GLOBAL
- notifications_sent (in-app notifications) — PER-USER
- symbol_health + symbol_health_events (kill-switch v1 health state) — GLOBAL
- kill_switch_decisions + kill_switch_v2_state + kill_switch_v2_baseline
  + kill_switch_recommendations (kill-switch v2) — GLOBAL (deferred per B.1)
- portfolio_health_events (portfolio-level circuit breaker) — PER-USER
- capital (per-user notional capital tracking) — PER-USER (new in B.1)
- user_preferences (per-user notification + filter config) — PER-USER (new in B.1)

## Multi-tenancy (Epic B #253 B.1 — 2026-05-15)

Per-user tables have a `tenant_id INTEGER` column (informal FK to users.id;
nullable in B.1, enforcement deferred to B.5 API layer + B.8 migration).
Backfill via `backfill_tenant(user_id)` for existing pre-multi-tenant rows.

Per pre-reg `docs/superpowers/plans/2026-05-15-multi-tenant-b1-schema-pre-reg.md`.
"""
from __future__ import annotations

import logging
import sqlite3
from db.connection import _open_configured_connection, _resolve_db_file
from db.transaction import transaction

log = logging.getLogger("db.schema")


def init_db() -> None:
    """Create or migrate all tables. Idempotent.

    PRAGMA journal_mode=WAL must run OUTSIDE any transaction (SQLite silently
    no-ops the change otherwise; verified empirically — see Task 8 migration
    notes). We open a one-shot raw connection for the PRAGMA, then drive all
    DDL through the standard transaction() primitive. WAL mode is a persistent
    file-level property so this only matters on first boot for a fresh DB.
    """
    pragma_con = _open_configured_connection()
    try:
        pragma_con.execute("PRAGMA journal_mode=WAL")
    finally:
        pragma_con.close()

    with transaction() as con:
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
    log.info(f"DB inicializada: {_resolve_db_file()}")

    # Migrate: add atr_entry and be_mult columns if missing
    try:
        with transaction() as con_mig:
            cols = [r[1] for r in con_mig.execute("PRAGMA table_info(positions)").fetchall()]
            if "atr_entry" not in cols:
                con_mig.execute("ALTER TABLE positions ADD COLUMN atr_entry REAL")
                log.info("DB migration: added atr_entry column to positions")
            if "be_mult" not in cols:
                con_mig.execute("ALTER TABLE positions ADD COLUMN be_mult REAL")
                log.info("DB migration: added be_mult column to positions")
    except Exception as e:
        log.warning(f"DB migration check: {e}")

    # B5 PROBATION migration: add 3 columns to symbol_health if missing (#199)
    try:
        with transaction() as con_mig2:
            cols2 = [r[1] for r in con_mig2.execute("PRAGMA table_info(symbol_health)").fetchall()]
            for col, ddl in (
                ("probation_trades_remaining", "INTEGER"),
                ("probation_started_at", "TEXT"),
                ("paused_days_at_entry", "INTEGER"),
            ):
                if col not in cols2:
                    con_mig2.execute(f"ALTER TABLE symbol_health ADD COLUMN {col} {ddl}")
                    log.info(f"DB migration: added {col} column to symbol_health")
    except Exception as e:
        log.warning(f"DB migration B5 PROBATION: {e}")

    # Multi-tenant B.1 migration (Epic B #253, issue #254) — 2026-05-15.
    # Adds nullable tenant_id to per-user tables + new capital + user_preferences.
    # Pre-reg: docs/superpowers/plans/2026-05-15-multi-tenant-b1-schema-pre-reg.md
    with transaction() as con_b1:
        _migrate_multi_tenant_b1(con_b1)

    # Agent (copilot) audit + budget tables — epic #400 Phase 1.
    # Pre-reg §9 / §10.1: every turn is audited, every side-effect carries
    # an idempotency key, every tenant has a daily/monthly USD budget that
    # resets computed-on-read (no cron).
    with transaction() as con_audit:
        _migrate_agent_audit(con_audit)

    # Agent (copilot) per-tenant conversation history — epic #428 H.1.
    # Persists raw user/assistant text + reasoning + tool chips + proposals
    # so users can revisit past chats. Separate from agent_conversations
    # (which stays as audit ledger) so the retention policies are
    # independent. Pre-reg D.1 in
    # docs/superpowers/specs/es/2026-05-22-conversation-history-pre-reg.md.
    with transaction() as con_hist:
        _migrate_agent_history(con_hist)

    # qty NOT NULL enforcement migration — #467 (Voronov amended).
    # MUST run LAST: depends on positions having atr_entry/be_mult/tenant_id
    # columns from earlier migrations. Backfills qty where possible, quarantines
    # the residue as status='legacy_unmeasurable', recreates positions with
    # CHECK (qty IS NOT NULL OR status='legacy_unmeasurable').
    # See `Capas de enforcement de invariantes` in CLAUDE.md.
    with transaction() as con_qty:
        _migrate_qty_not_null(con_qty)


# Per-user tables that need a tenant_id column (Epic B B.1).
# kill_switch_* tables intentionally NOT in this list — kept global for B.1
# per pre-reg §2.3 (conservative default; future sub-issue may move them).
PER_USER_TABLES: tuple[str, ...] = (
    "positions",
    "signal_outcomes",
    "notifications_sent",
    "portfolio_health_events",
)


def _migrate_multi_tenant_b1(con: sqlite3.Connection) -> None:
    """Idempotent multi-tenant B.1 migration: add tenant_id to per-user tables
    + new capital + user_preferences tables + indexes.

    Pre-reg §3.1-§3.2: ALTER TABLE in try/except (column-exists handling); new
    tables via CREATE TABLE IF NOT EXISTS; new indexes via CREATE INDEX IF NOT
    EXISTS. Safe to call repeatedly.

    Task 8 (#446): `con` is now mandatory positional.
    """
    # Step 1: Add nullable tenant_id to each per-user table
    for table in PER_USER_TABLES:
        try:
            con.execute(f"ALTER TABLE {table} ADD COLUMN tenant_id INTEGER")
            log.info(f"DB migration B.1: added tenant_id column to {table}")
        except sqlite3.OperationalError:
            pass  # column already exists

    # Step 2: Create capital table (single row per user)
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS capital (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id         INTEGER NOT NULL,
            balance           REAL NOT NULL,
            peak_balance      REAL NOT NULL,
            max_drawdown_pct  REAL,
            updated_at        TEXT NOT NULL
        )
        """
    )
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_capital_tenant "
        "ON capital(tenant_id)"
    )

    # Step 3: Create user_preferences table (single row per user)
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS user_preferences (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id            INTEGER NOT NULL,
            symbol_filter_json   TEXT,
            min_score            INTEGER DEFAULT 4,
            notify_channels_json TEXT,
            updated_at           TEXT NOT NULL
        )
        """
    )
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_user_prefs_tenant "
        "ON user_preferences(tenant_id)"
    )

    # Step 4: Tenant-scoped indexes on per-user tables
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_positions_tenant "
        "ON positions(tenant_id)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_signal_outcomes_tenant "
        "ON signal_outcomes(tenant_id)"
    )
    # New tenant-aware unread index (does NOT drop the existing global one;
    # both can coexist — SQLite picks the more selective for each query)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_notif_tenant_unread "
        "ON notifications_sent(tenant_id, sent_at DESC) WHERE read_at IS NULL"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_portfolio_events_tenant_ts "
        "ON portfolio_health_events(tenant_id, ts DESC)"
    )


def _migrate_agent_audit(con: sqlite3.Connection) -> None:
    """Idempotent Phase 1 migration for the copilot audit + budget surface.

    Creates three tables and their indexes. Safe to call repeatedly:
    CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS.

    Schema source: docs/superpowers/specs/es/2026-05-19-trading-copilot-production-grade-pre-reg.md §9.1.

    Task 8 (#446): `con` is now mandatory positional.
    """
    try:
        # agent_conversations: every turn (user / assistant / tool_result)
        # written by the server-side loop. content_json is the redacted
        # payload — full tool_use input/output is NOT persisted here; the
        # tool side-effect surface lives in agent_side_effects below.
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_conversations (
                id                          INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id                   INTEGER NOT NULL,
                surface                     TEXT    NOT NULL,
                conversation_id             TEXT    NOT NULL,
                ts                          TEXT    NOT NULL,
                role                        TEXT,
                model                       TEXT,
                input_tokens                INTEGER,
                output_tokens               INTEGER,
                cache_read_input_tokens     INTEGER,
                cache_creation_input_tokens INTEGER,
                latency_ms                  INTEGER,
                cost_usd                    REAL,
                content_json                TEXT,
                refused                     INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_conv_tenant_ts "
            "ON agent_conversations(tenant_id, ts DESC)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_conv_conversation "
            "ON agent_conversations(conversation_id, ts ASC)"
        )

        # agent_side_effects: the propose/confirm ledger. idempotency_key
        # is UNIQUE so a double-click confirm cannot execute twice. action
        # is one of: close_position | reactivate_symbol | apply_tune.
        # result: ok | error | conflict | expired.
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_side_effects (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id       INTEGER NOT NULL,
                conversation_id TEXT,
                ts              TEXT    NOT NULL,
                action          TEXT    NOT NULL,
                args_json       TEXT,
                idempotency_key TEXT    NOT NULL UNIQUE,
                result          TEXT,
                http_status     INTEGER
            )
            """
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_side_effects_tenant_ts "
            "ON agent_side_effects(tenant_id, ts DESC)"
        )

        # agent_quotas: one row per tenant. daily_window_start and
        # monthly_window_start drive the computed-on-read reset (pre-reg
        # §9.1) — no cron needed. UNIQUE on tenant_id so upserts via
        # ON CONFLICT(tenant_id) DO UPDATE work.
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_quotas (
                tenant_id            INTEGER PRIMARY KEY,
                daily_usd_used       REAL    NOT NULL DEFAULT 0,
                daily_usd_cap        REAL    NOT NULL DEFAULT 1.0,
                daily_window_start   TEXT    NOT NULL,
                monthly_usd_used     REAL    NOT NULL DEFAULT 0,
                monthly_window_start TEXT    NOT NULL
            )
            """
        )

        # Phase 3 (#400): agent_side_effects gains `expires_at` so the
        # confirm endpoint can short-circuit expired proposals without
        # re-deriving the TTL from the signed payload. Idempotent ADD
        # COLUMN (the existing rows have NULL — they predate the column,
        # and a NULL expires_at is treated as "no TTL enforcement" for
        # rows seeded before this migration).
        try:
            con.execute("ALTER TABLE agent_side_effects ADD COLUMN expires_at TEXT")
            log.info("DB migration: added expires_at column to agent_side_effects")
        except sqlite3.OperationalError:
            pass  # column already exists

        # Fase 4 of the multi-provider epic: agent_conversations gains
        # `provider` + `reasoning_tokens` columns so /agent/metrics can
        # report per-provider spend + R1 reasoning telemetry.
        #   - provider: closed enum 'anthropic' | 'deepseek' | ... (the
        #     vendor name from PROVIDER_NAME_BY_PREFIX). NULL for rows
        #     pre-Fase-4 if backfill skipped them (it shouldn't — the
        #     UPDATE below covers every known prefix).
        #   - reasoning_tokens: only DS-reasoner populates this today
        #     (DS's usage.completion_tokens_details.reasoning_tokens
        #     field). NULL or 0 elsewhere; metrics treat NULL as 0.
        # Both ALTERs are idempotent (try/except on OperationalError).
        # The backfill is also idempotent because it only touches rows
        # WHERE provider IS NULL — running it twice is a no-op.
        try:
            con.execute("ALTER TABLE agent_conversations ADD COLUMN provider TEXT")
            log.info("DB migration: added provider column to agent_conversations")
        except sqlite3.OperationalError:
            pass  # column already exists
        try:
            con.execute("ALTER TABLE agent_conversations ADD COLUMN reasoning_tokens INTEGER")
            log.info("DB migration: added reasoning_tokens column to agent_conversations")
        except sqlite3.OperationalError:
            pass  # column already exists

        # Backfill provider from model. Only touches rows where provider
        # IS NULL (i.e. pre-Fase-4 rows) — safe to re-run. The mapping
        # uses model-prefix matching that mirrors PROVIDER_NAME_BY_PREFIX
        # in api/agent/providers/registry.py; if a future provider adds
        # a new prefix, mirror it HERE too (single source of truth would
        # be ideal but importing the registry from db.schema introduces
        # a circular at startup).
        try:
            con.execute(
                "UPDATE agent_conversations SET provider = 'anthropic' "
                "WHERE provider IS NULL AND model LIKE 'claude-%'"
            )
            con.execute(
                "UPDATE agent_conversations SET provider = 'deepseek' "
                "WHERE provider IS NULL AND model LIKE 'deepseek-%'"
            )
            log.info("DB migration: backfilled provider column from model prefix")
        except sqlite3.OperationalError as e:
            log.warning("DB migration: provider backfill failed: %s", e)

        log.info("DB migration: agent_conversations + agent_side_effects + agent_quotas ready")
    except Exception as e:  # noqa: BLE001
        log.warning("DB migration agent audit: %s", e)


def _migrate_agent_history(con: sqlite3.Connection) -> None:
    """Idempotent H.1 migration for the per-tenant conversation history.

    Creates two tables and their indexes. Safe to call repeatedly:
    CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS.

    Tables (pre-reg §Schemas in
    docs/superpowers/specs/es/2026-05-22-conversation-history-pre-reg.md):

      - agent_messages: raw user/assistant text + DS-R1 reasoning +
        tool chips + signed proposal envelopes. One row per turn-half.
        TTL via expires_at (computed-on-read, mirror agent_quotas).

      - agent_conversation_meta: one row per conversation_id with
        title (derived from first user message), surface, ts range,
        message_count, pinned flag. Sidebar list reads from here.

    NOT touched by this migration: agent_conversations (audit ledger),
    agent_side_effects (proposals execution log), agent_quotas (cost
    budget). Those keep their own lifecycle and retention.

    Task 8 (#446): `con` is now mandatory positional.
    """
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id       INTEGER NOT NULL,
                conversation_id TEXT    NOT NULL,
                ts              TEXT    NOT NULL,
                role            TEXT    NOT NULL,
                content         TEXT    NOT NULL,
                reasoning       TEXT,
                tool_chips_json TEXT,
                proposals_json  TEXT,
                expires_at      TEXT    NOT NULL
            )
            """
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_messages_tenant_conv_ts "
            "ON agent_messages(tenant_id, conversation_id, ts ASC)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_messages_tenant_ts "
            "ON agent_messages(tenant_id, ts DESC)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_messages_expires "
            "ON agent_messages(expires_at)"
        )

        # conversation_id is the natural PK — UUID generated by the
        # frontend (newConversationId() in frontend/src/agent/client.ts).
        # The explicit NOT NULL is required: SQLite enforces NOT NULL
        # implicitly only on `INTEGER PRIMARY KEY` (the rowid alias),
        # not on TEXT PRIMARY KEY — without it, NULL would slip past
        # the PK check and corrupt the index.
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_conversation_meta (
                conversation_id TEXT    NOT NULL PRIMARY KEY,
                tenant_id       INTEGER NOT NULL,
                title           TEXT,
                surface         TEXT    NOT NULL,
                first_ts        TEXT    NOT NULL,
                last_ts         TEXT    NOT NULL,
                message_count   INTEGER NOT NULL DEFAULT 0,
                pinned          INTEGER NOT NULL DEFAULT 0,
                expires_at      TEXT    NOT NULL
            )
            """
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_conv_meta_tenant_last "
            "ON agent_conversation_meta(tenant_id, last_ts DESC)"
        )
        # Pinned conversations float to the top of the sidebar; the
        # secondary key on last_ts DESC keeps non-pinned ordered by
        # recency within their bucket. Index ordering matters: SQLite
        # uses leading columns for filtering and trailing for ordering.
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_conv_meta_tenant_pinned "
            "ON agent_conversation_meta(tenant_id, pinned DESC, last_ts DESC)"
        )

        log.info("DB migration: agent_messages + agent_conversation_meta ready")
    except Exception as e:  # noqa: BLE001
        log.warning("DB migration agent history: %s", e)


def backfill_tenant(
    con: sqlite3.Connection,
    user_id: int,
) -> dict[str, int]:
    """Set `tenant_id = user_id` for all rows where tenant_id IS NULL.

    Pre-reg §3.3: idempotent — running twice is a no-op (no NULL rows after
    first run). Returns `{table_name: rows_updated}` for observability.

    NOT called from init_db(). Caller (B.8 migration script or test setup)
    invokes explicitly. Typical usage:

      from db.transaction import transaction
      from db.schema import backfill_tenant
      with transaction() as con:
          counts = backfill_tenant(con, samuel_user_id)
      log.info(f"Backfill complete: {counts}")

    Task 8 (#446): `con` is now mandatory positional first arg.

    Args:
        con: open sqlite3 Connection from a surrounding transaction.
        user_id: target user ID to receive ownership of pre-multi-tenant rows.

    Returns:
        Dict mapping table name to count of rows updated.
    """
    affected: dict[str, int] = {}
    for table in PER_USER_TABLES:
        cursor = con.execute(
            f"UPDATE {table} SET tenant_id = ? WHERE tenant_id IS NULL",
            (user_id,),
        )
        affected[table] = cursor.rowcount
    return affected


def _migrate_qty_not_null(con: sqlite3.Connection) -> None:
    """Move 'qty != NULL' from convención to schema (#467, Voronov amended).

    Per Voronov post-Task-1 measurement: original Path D (abort on
    unbackfillable) was unrealizable in production (670 unbackfillable rows
    of 2018). Revised policy (C) — quarantine:

    1. Backfill: for rows where qty IS NULL AND size_usd IS NOT NULL AND
       entry_price > 0, set qty = size_usd / entry_price.
    2. For remaining NULL rows (legacy bug residue + test fixture debris):
       UPDATE status = 'legacy_unmeasurable'. Keep qty NULL. Status admits
       the absence; the schema CHECK below exempts this status.
    3. Recreate the positions table with:
       CHECK (qty IS NOT NULL OR status = 'legacy_unmeasurable')

    Idempotent: detects existing CHECK constraint and skips.
    """
    # Idempotency check: SQLite stores CREATE TABLE in sqlite_master.
    schema_row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='positions'"
    ).fetchone()
    if schema_row and schema_row[0] and "legacy_unmeasurable" in schema_row[0]:
        log.info(
            "_migrate_qty_not_null: positions table already has the quarantine CHECK; skipping."
        )
        return

    # Column-aware migration: an old/stub positions table may not yet have
    # size_usd or qty columns (the earlier _migrate_* helpers are tolerant
    # of missing columns; this one must be too). Query the live schema and
    # gate each step on which columns actually exist.
    existing_cols = {
        row[1] for row in con.execute("PRAGMA table_info(positions)").fetchall()
    }
    has_size_usd = "size_usd" in existing_cols
    has_qty = "qty" in existing_cols
    has_entry_price = "entry_price" in existing_cols

    # 1. Backfill aggressively — only when both source and target columns exist.
    if has_qty and has_size_usd and has_entry_price:
        con.execute(
            """UPDATE positions
               SET qty = size_usd / entry_price
               WHERE qty IS NULL
                 AND size_usd IS NOT NULL
                 AND entry_price IS NOT NULL
                 AND entry_price > 0"""
        )
        backfilled = con.execute("SELECT changes()").fetchone()[0]
        log.info("_migrate_qty_not_null: backfilled qty for %d rows.", backfilled)
    else:
        log.info(
            "_migrate_qty_not_null: skipping backfill — columns not yet present "
            "(has_qty=%s, has_size_usd=%s, has_entry_price=%s).",
            has_qty,
            has_size_usd,
            has_entry_price,
        )

    # 2. Quarantine rows that will fail the CHECK constraint after recreation.
    if has_qty:
        # Standard path: only rows whose qty is actually NULL need quarantine.
        con.execute(
            """UPDATE positions
               SET status = 'legacy_unmeasurable'
               WHERE qty IS NULL
                 AND status != 'legacy_unmeasurable'"""
        )
    else:
        # No qty column at all — after recreation every existing row will land
        # with qty=NULL in the new schema, so all of them must be quarantined
        # up-front or the CHECK constraint would reject the INSERT.
        con.execute(
            """UPDATE positions
               SET status = 'legacy_unmeasurable'
               WHERE status != 'legacy_unmeasurable'"""
        )
    quarantined = con.execute("SELECT changes()").fetchone()[0]
    log.info(
        "_migrate_qty_not_null: quarantined %d rows as status='legacy_unmeasurable'.",
        quarantined,
    )

    # 3. Recreate positions table with CHECK constraint.
    # SQLite pattern: CREATE TABLE positions_new (...); INSERT ...; DROP; RENAME.
    # NOTE: use individual con.execute() — executescript() issues an implicit
    # COMMIT that would close the surrounding transaction(). DDL inside
    # transaction() is safe in SQLite (DDL participates in the active tx).
    log.info(
        "_migrate_qty_not_null: recreating positions table with CHECK constraint "
        "(qty IS NOT NULL OR status = 'legacy_unmeasurable')."
    )
    con.execute(
        """
        CREATE TABLE positions_new (
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
            notes       TEXT,
            atr_entry   REAL,
            be_mult     REAL,
            tenant_id   INTEGER,
            CHECK (qty IS NOT NULL OR status = 'legacy_unmeasurable')
        )
        """
    )
    # Build the SELECT dynamically: missing source columns land as NULL in the
    # new table. Order MUST mirror TARGET_COLS so positional INSERT lines up.
    TARGET_COLS = [
        "id", "scan_id", "symbol", "direction", "status", "entry_price",
        "entry_ts", "sl_price", "tp_price", "size_usd", "qty",
        "exit_price", "exit_ts", "exit_reason", "pnl_usd", "pnl_pct",
        "notes", "atr_entry", "be_mult", "tenant_id",
    ]
    select_expressions = [
        col if col in existing_cols else "NULL"
        for col in TARGET_COLS
    ]
    insert_sql = (
        f"INSERT INTO positions_new ({', '.join(TARGET_COLS)}) "
        f"SELECT {', '.join(select_expressions)} FROM positions"
    )
    con.execute(insert_sql)
    con.execute("DROP TABLE positions")
    con.execute("ALTER TABLE positions_new RENAME TO positions")
    con.execute("CREATE INDEX idx_positions_tenant ON positions(tenant_id)")
    log.info(
        "_migrate_qty_not_null: migration complete. "
        "positions enforces qty IS NOT NULL OR status='legacy_unmeasurable'."
    )
