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
import os
import sqlite3
import time
from db.connection import _open_configured_connection, _resolve_db_file
from db.transaction import transaction

log = logging.getLogger("db.schema")


def _set_wal_mode_idempotent_with_retry() -> None:
    """Set journal_mode=WAL once per DB file, with retry on lock contention.

    Idempotent path:
      `PRAGMA journal_mode` (no assignment) returns the current mode.
      If it is already 'wal', we skip the assignment entirely. This is the
      steady-state path on every boot after the first (WAL is a persistent
      file-level property), and it removes the most common race source —
      issuing a no-op WAL switch that still requires heavy coordination.

    Retry path (rare residual case):
      If the DB is not yet in WAL mode AND `PRAGMA journal_mode=WAL` raises
      `database is locked`, retry with backoff (3 attempts: 200ms, 600ms,
      1500ms total ≤2.3s). On the fourth attempt, raise — at that point
      the system is in a state init_db cannot recover from on its own.

    `busy_timeout = 5000` is still applied first as defense against
    sub-second contention that resolves within the PRAGMA's own wait.
    """
    backoffs = (0.2, 0.6, 1.5)
    last_exc: sqlite3.OperationalError | None = None
    for attempt, delay in enumerate((0.0, *backoffs)):
        if delay > 0:
            time.sleep(delay)
        pragma_con = _open_configured_connection()
        try:
            pragma_con.execute("PRAGMA busy_timeout = 5000")
            # Idempotency probe: a bare `PRAGMA journal_mode` returns the
            # current mode without changing it. If already 'wal', skip the
            # assignment — saves a heavy-coordination lock acquisition.
            current = pragma_con.execute(
                "PRAGMA journal_mode"
            ).fetchone()
            if current and str(current[0]).lower() == "wal":
                return
            pragma_con.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if "database is locked" in msg or "database table is locked" in msg:
                last_exc = exc
                log.warning(
                    "init_db: PRAGMA journal_mode=WAL locked on attempt %d/%d; "
                    "retrying after %.1fs",
                    attempt + 1, len(backoffs) + 1, backoffs[attempt] if attempt < len(backoffs) else 0,
                )
                continue
            raise
        finally:
            pragma_con.close()
    assert last_exc is not None
    raise last_exc


def init_db() -> None:
    """Create or migrate all tables. Idempotent.

    PRAGMA journal_mode=WAL must run OUTSIDE any transaction (SQLite silently
    no-ops the change otherwise; verified empirically — see Task 8 migration
    notes). We open a one-shot raw connection for the PRAGMA, then drive all
    DDL through the standard transaction() primitive. WAL mode is a persistent
    file-level property so this only matters on first boot for a fresh DB.

    #495 history:
      - PR #508: added `PRAGMA busy_timeout = 5000` before the WAL pragma.
        Helped on read-lock contention but did not close the race where a
        background thread held a write transaction at the moment WAL ran.
      - This PR (root-cause fix): `scanner.runtime.stop_managed_threads()`
        now deterministically joins the three lifespan-owned threads
        before the next test boots, removing the orphan-writer source.
      - This function (defense in depth): skip the WAL pragma entirely
        when the DB is already in WAL mode (the steady-state on every
        boot after the first), and retry with backoff on the rare
        residual race where some other writer is still active.

    Together, the root-cause fix should make `database is locked` here
    impossible in CI; the defense-in-depth tier here keeps a single
    surviving orphan from blocking init_db indefinitely in production.
    """
    _set_wal_mode_idempotent_with_retry()

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
        # Migración: agregar columna symbol si la tabla ya existía sin ella.
        # PRAGMA-guarded (NOT try/except) so the enclosing BEGIN IMMEDIATE tx
        # remains in a clean state when the column is already present — a
        # failed ALTER inside a write-tx marks it as abortable and the
        # subsequent COMMIT silently rolls back unrelated DDL in the same tx
        # (Serrano HIGH 1, propagation of the 75b3789 pattern).
        scans_cols = {
            row[1]
            for row in con.execute("PRAGMA table_info(scans)").fetchall()
        }
        if "symbol" not in scans_cols:
            con.execute(
                "ALTER TABLE scans ADD COLUMN symbol TEXT NOT NULL DEFAULT 'BTCUSDT'"
            )
            log.info("DB migrada: columna 'symbol' añadida.")

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

    # ── D-cluster migrations: ONE transaction (Serrano HIGH 7). ──
    # All four sub-migrations participate in the same write-tx. Partial
    # failure of any step rolls the WHOLE cluster back, so the database
    # never sits in a half-migrated intermediate state. Each sub-step
    # remains idempotent on its own; the wrapping tx only changes the
    # group-failure semantics.
    #
    # Ordering constraints inside the cluster:
    #   1. _migrate_qty_positive — depends on the C2 CHECK from
    #      _migrate_qty_not_null above (already committed in its own tx).
    #   2. _migrate_tenant_id_not_null — recreates the positions table,
    #      must run after _migrate_qty_positive so both CHECKs land on
    #      the new table together.
    #   3. _migrate_unique_open_scan — installs the partial UNIQUE index
    #      against the table _migrate_tenant_id_not_null produced.
    #   4. _migrate_idempotency_keys — independent table; safe to run
    #      any time but kept in this cluster so D-cluster invariants
    #      land or roll back together.
    with transaction() as con_d:
        _migrate_qty_positive(con_d)
        _migrate_tenant_id_not_null(con_d)
        _migrate_direction_enum(con_d)
        _migrate_unique_open_scan(con_d)
        _migrate_idempotency_keys(con_d)

    # control_domain axis (posiciones EXTERNAL — spec
    # 2026-06-09-posiciones-externas-control-domain). Runs AFTER all table
    # recreations so the column is never dropped by a later recreate. Fresh DB:
    # the recreations build positions without it → this ALTER adds it. Prod
    # (already migrated): the recreations skip (idempotent) → this ALTER adds
    # it. PRAGMA-guarded for idempotency.
    with transaction() as con_cd:
        _migrate_control_domain(con_cd)

    # cash_balance_usd en capital: el saldo NO-posición del operador (cash /
    # futuros) para el equity en vivo display-only (v0.1.5). Idempotente.
    with transaction() as con_cash:
        _migrate_cash_balance(con_cash)


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
    # Step 1: Add nullable tenant_id to each per-user table.
    # PRAGMA-guarded (NOT try/except) so the enclosing BEGIN IMMEDIATE tx
    # stays clean when the column already exists — Serrano HIGH 1, same
    # pathology as 75b3789 on _migrate_idempotency_keys.
    for table in PER_USER_TABLES:
        existing_cols = {
            row[1]
            for row in con.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if "tenant_id" not in existing_cols:
            con.execute(f"ALTER TABLE {table} ADD COLUMN tenant_id INTEGER")
            log.info(f"DB migration B.1: added tenant_id column to {table}")

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
        # rows seeded before this migration). PRAGMA-guarded (NOT
        # try/except) so the enclosing BEGIN IMMEDIATE tx stays clean
        # when the column is already present — Serrano HIGH 1.
        side_effects_cols = {
            row[1]
            for row in con.execute(
                "PRAGMA table_info(agent_side_effects)"
            ).fetchall()
        }
        if "expires_at" not in side_effects_cols:
            con.execute(
                "ALTER TABLE agent_side_effects ADD COLUMN expires_at TEXT"
            )
            log.info("DB migration: added expires_at column to agent_side_effects")

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
        # Both ALTERs are idempotent via a single PRAGMA table_info probe
        # (NOT try/except — a failed ALTER inside the enclosing BEGIN
        # IMMEDIATE tx would mark it abortable and silently roll back the
        # rest of the audit migration on COMMIT; Serrano HIGH 1, same
        # pathology as 75b3789 on _migrate_idempotency_keys).
        # The backfill is also idempotent because it only touches rows
        # WHERE provider IS NULL — running it twice is a no-op.
        conv_cols = {
            row[1]
            for row in con.execute(
                "PRAGMA table_info(agent_conversations)"
            ).fetchall()
        }
        if "provider" not in conv_cols:
            con.execute(
                "ALTER TABLE agent_conversations ADD COLUMN provider TEXT"
            )
            log.info("DB migration: added provider column to agent_conversations")
        if "reasoning_tokens" not in conv_cols:
            con.execute(
                "ALTER TABLE agent_conversations ADD COLUMN reasoning_tokens INTEGER"
            )
            log.info(
                "DB migration: added reasoning_tokens column to agent_conversations"
            )

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


def _migrate_control_domain(con: sqlite3.Connection) -> None:
    """Add `control_domain` to positions (INTERNAL default / EXTERNAL).

    Distingue posiciones nacidas DEL sistema (INTERNAL — el sistema tiene
    camino de control: PositionClosure, check_position_stops) de las abiertas
    POR FUERA (EXTERNAL — observadas, nunca actuadas). `positions` confundía
    dos ejes que coincidían por accidente: provenance (de dónde vino) y control
    (quién actúa); esta columna los separa.

    Idempotente: PRAGMA-guarded ADD COLUMN (NO try/except — un ALTER fallido en
    la BEGIN IMMEDIATE marcaría la tx como abortable). NOT NULL DEFAULT
    'INTERNAL' rellena toda fila existente, así que el comportamiento de las
    filas previas se preserva (todas son INTERNAL).

    Spec: docs/superpowers/specs/es/2026-06-09-posiciones-externas-control-domain-spec.md (REV 2 §2).
    """
    cols = {
        row[1]
        for row in con.execute("PRAGMA table_info(positions)").fetchall()
    }
    if "control_domain" not in cols:
        con.execute(
            "ALTER TABLE positions ADD COLUMN control_domain TEXT NOT NULL "
            "DEFAULT 'INTERNAL'"
        )
        log.info(
            "DB migration: added control_domain column to positions "
            "(default INTERNAL)"
        )


def _migrate_cash_balance(con: sqlite3.Connection) -> None:
    """Add `cash_balance_usd` to capital (saldo no-posición del operador).

    Es el cash/futuros del operador que NO vive en una posición trackeada. Lo
    usa el equity en vivo display-only (`api.equity.compute_real_equity`):
    equity_real = cash_balance_usd + Σ(holds EXTERNAL × precio_actual). NO
    alimenta `capital.balance` ni el `portfolio_dd` del kill-switch.

    Idempotente: PRAGMA-guarded ADD COLUMN. NOT NULL DEFAULT 0 → toda fila
    existente arranca en 0 (sin efecto hasta que un operador lo fije).
    """
    cols = {
        row[1]
        for row in con.execute("PRAGMA table_info(capital)").fetchall()
    }
    if "cash_balance_usd" not in cols:
        con.execute(
            "ALTER TABLE capital ADD COLUMN cash_balance_usd REAL NOT NULL DEFAULT 0"
        )
        log.info("DB migration: added cash_balance_usd column to capital (default 0)")


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
    # Anchor on the specific CHECK constraint DDL fragment, not the bare
    # string "legacy_unmeasurable" — the latter false-positives on column
    # defaults, comments, or unrelated CHECK constraints that mention the
    # status value. Whitespace-normalized + case-folded to tolerate the
    # variations SQLite uses when echoing back CREATE TABLE in sqlite_master.
    # See #476 (Serrano F4 [HIGH, OPS/AMB]) for the failure mode.
    schema_row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='positions'"
    ).fetchone()
    if schema_row and schema_row[0]:
        normalized = "".join(schema_row[0].split()).lower()
        if "check(qtyisnotnullorstatus='legacy_unmeasurable')" in normalized:
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
        #
        # SAFETY GUARD (#474, Serrano F3 [HIGH, SEC/GAP]): bulk quarantine of
        # active rows is dangerous in production. A DB rebuilt from a stale
        # backup (or any artifact where qty was never added) would have every
        # position flipped to 'legacy_unmeasurable' silently — kill-switch and
        # notional code paths then skip these rows with a log.warning and the
        # trading state is invisibly disabled. Refuse the destructive UPDATE
        # unless the operator explicitly opts in via env flag.
        status_counts = con.execute(
            """SELECT status, COUNT(*) FROM positions
               WHERE status != 'legacy_unmeasurable'
               GROUP BY status"""
        ).fetchall()
        total_to_quarantine = sum(count for _, count in status_counts)
        if total_to_quarantine > 0:
            counts_repr = ", ".join(
                f"{count} {status!r}" for status, count in status_counts
            )
            if os.environ.get("MIGRATE_QTY_ALLOW_BULK_QUARANTINE") != "1":
                raise RuntimeError(
                    f"_migrate_qty_not_null: refusing to bulk-quarantine "
                    f"{total_to_quarantine} rows ({counts_repr}) in the "
                    f"no-qty-column branch. Set "
                    f"MIGRATE_QTY_ALLOW_BULK_QUARANTINE=1 to override "
                    f"(operator acknowledges that the rows present in this "
                    f"DB predate the qty column and should be marked "
                    f"'legacy_unmeasurable'). See #474."
                )
            log.warning(
                "_migrate_qty_not_null: bulk-quarantining %d rows under "
                "MIGRATE_QTY_ALLOW_BULK_QUARANTINE opt-in: %s",
                total_to_quarantine, counts_repr,
            )
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
    # Defensive cleanup of any orphan positions_new from a prior interrupted
    # run (#480, Serrano F12 [MEDIUM, OPS]). The idempotency probe at the top
    # of this function checks for the CHECK constraint on `positions`, NOT
    # for positions_new existence. If an orphan positions_new is on disk, the
    # next migration would abort on 'table positions_new already exists'.
    # This DROP IF EXISTS recovers the path.
    #
    # On the MECHANISM that produces such an orphan:
    #
    # A SIGKILL between this `CREATE TABLE positions_new` and the later
    # `ALTER TABLE positions_new RENAME TO positions` CANNOT leave the orphan
    # on disk under the current `transaction()` wrapper. All five steps run
    # inside one `BEGIN IMMEDIATE … COMMIT` against a WAL-mode connection
    # (db/transaction.py:36-75, journal_mode forced at db/schema.py:78). The
    # WAL commit-marker frame is written only by COMMIT; recovery on next
    # open is invisible to readers if the marker is absent. The defensive
    # DROP is correct, but the SIGKILL-between-CREATE-and-RENAME story does
    # not describe a reachable failure mode under this wrapper. See the
    # locked-in regression test at
    # `tests/test_migration_wal_atomicity.py` and the closing comment of
    # #497 (Halberg 2026-05-27 runtime verdict).
    #
    # Reachable producers of the orphan:
    #   (b1) Pre-wrapper-era code path that ran CREATE TABLE positions_new
    #        outside transaction() (auto-commit). The CREATE committed; the
    #        process died before RENAME. Modern code path can no longer
    #        produce this, but a pre-existing on-disk orphan survives.
    #   (b2) `executescript()` slip — it issues an implicit COMMIT before
    #        running, silently closing the surrounding BEGIN IMMEDIATE. If
    #        any historical helper called executescript() containing the
    #        CREATE, the CREATE committed; a crash before the RENAME left
    #        an orphan. (Hence the "use individual con.execute()" rule
    #        immediately above this block.)
    #   (b3) Concurrent writer / manual sqlite3 CLI session that ran half
    #        the migration in auto-commit mode and died.
    #
    # The DROP IF EXISTS defends against ALL THREE. The original Serrano F12
    # text described it as guarding against mid-tx kill; runtime review
    # (Halberg 2026-05-27) corrected the attribution. The defense is right;
    # the comment now matches the runtime.
    con.execute("DROP TABLE IF EXISTS positions_new")
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


def _migrate_qty_positive(con: sqlite3.Connection) -> None:
    """Extend the qty CHECK from 'NOT NULL' to '> 0' (#471 closure of qty=0 bypass).

    Production measurement (2026-05-26): 72 rows with qty=0.0 exactly (68
    closed, 2 open, 2 cancelled). These bypassed the C2 NULL check.

    Policy (Voronov dual-rung): re-status the 72 zero-qty rows as
    'legacy_unmeasurable' (admit the absence; don't invent a value), then
    extend the CHECK to require qty > 0 on non-quarantine rows.

    Idempotent: detects the qty>0 fragment in the live schema and skips.
    """
    schema_row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='positions'"
    ).fetchone()
    if not schema_row or not schema_row[0]:
        log.warning(
            "_migrate_qty_positive: positions table not found; skipping."
        )
        return
    # Normalize whitespace for the idempotency probe. Look for "qty > 0" or
    # "qty>0" anywhere in the CHECK fragment.
    normalized = "".join(schema_row[0].split()).lower()
    if "qty>0" in normalized:
        log.info(
            "_migrate_qty_positive: positions already enforces qty > 0; skipping."
        )
        return

    # 1. Quarantine zero-qty rows (any status). The C2 CHECK allowed them; the
    #    new CHECK will reject them on non-quarantine status. Re-status to
    #    legacy_unmeasurable (same quarantine bucket used by C2).
    con.execute(
        """UPDATE positions
              SET status = 'legacy_unmeasurable'
            WHERE qty = 0
              AND status != 'legacy_unmeasurable'"""
    )
    quarantined = con.execute("SELECT changes()").fetchone()[0]
    log.info(
        "_migrate_qty_positive: quarantined %d zero-qty rows as 'legacy_unmeasurable'.",
        quarantined,
    )

    # 2. Defensive sanity: any qty < 0 in legacy data also goes to quarantine.
    con.execute(
        """UPDATE positions
              SET status = 'legacy_unmeasurable'
            WHERE qty < 0
              AND status != 'legacy_unmeasurable'"""
    )
    neg_quarantined = con.execute("SELECT changes()").fetchone()[0]
    if neg_quarantined:
        log.warning(
            "_migrate_qty_positive: quarantined %d NEGATIVE-qty rows (unexpected).",
            neg_quarantined,
        )

    # 3. Recreate the table with the strengthened CHECK.
    log.info(
        "_migrate_qty_positive: recreating positions table with "
        "CHECK ((qty IS NOT NULL AND qty > 0) OR status='legacy_unmeasurable')."
    )
    existing_cols = {
        row[1] for row in con.execute("PRAGMA table_info(positions)").fetchall()
    }
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
            CHECK ((qty IS NOT NULL AND qty > 0) OR status = 'legacy_unmeasurable')
        )
        """
    )
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
    con.execute("CREATE INDEX IF NOT EXISTS idx_positions_tenant ON positions(tenant_id)")
    log.info(
        "_migrate_qty_positive: migration complete. positions enforces qty > 0."
    )


def _migrate_tenant_id_not_null(con: sqlite3.Connection) -> None:
    """Schema CHECK: tenant_id IS NOT NULL OR status IN ('legacy_unmeasurable',
    'legacy_no_tenant') — #471 (Voronov D-schema rung, tenant invariant).

    Production measurement (2026-05-26): 2018/2018 positions had
    tenant_id IS NULL. Of those, 670 are already in legacy_unmeasurable from
    C2 — the new CHECK exempts them via the OR (no double-quarantine).
    The remaining ~1348 get re-statused to 'legacy_no_tenant'.

    Idempotent: detects the 'legacy_no_tenant' literal in the live CHECK
    fragment and skips.
    """
    schema_row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='positions'"
    ).fetchone()
    if not schema_row or not schema_row[0]:
        log.warning(
            "_migrate_tenant_id_not_null: positions table not found; skipping."
        )
        return
    if "legacy_no_tenant" in schema_row[0]:
        log.info(
            "_migrate_tenant_id_not_null: positions already exempts "
            "'legacy_no_tenant'; skipping."
        )
        return

    # Column-aware: if tenant_id column missing (pre-B.1 stub schema), skip
    # the backfill UPDATE — there is nothing to re-status — but still recreate
    # the table with the CHECK so the invariant is anchored at the schema.
    existing_cols = {
        row[1] for row in con.execute("PRAGMA table_info(positions)").fetchall()
    }
    if "tenant_id" in existing_cols:
        # 1. Quarantine NULL-tenant rows that are NOT already in legacy_unmeasurable.
        #    Rows already in legacy_unmeasurable keep that status — the OR in the new
        #    CHECK will exempt them directly.
        con.execute(
            """UPDATE positions
                  SET status = 'legacy_no_tenant'
                WHERE tenant_id IS NULL
                  AND status != 'legacy_unmeasurable'"""
        )
        quarantined = con.execute("SELECT changes()").fetchone()[0]
        log.info(
            "_migrate_tenant_id_not_null: re-statused %d NULL-tenant rows as "
            "'legacy_no_tenant'.",
            quarantined,
        )
    else:
        log.info(
            "_migrate_tenant_id_not_null: skipping backfill — tenant_id column "
            "not yet present (pre-B.1 stub schema)."
        )

    # 2. Recreate the table with the strengthened CHECK. Both CHECKs (qty>0
    #    from Task 4 + tenant_id from this migration) must coexist on the
    #    new table — they compose.
    log.info(
        "_migrate_tenant_id_not_null: recreating positions with CHECK "
        "(tenant_id IS NOT NULL OR status IN ('legacy_unmeasurable','legacy_no_tenant'))."
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
            CHECK ((qty IS NOT NULL AND qty > 0) OR status = 'legacy_unmeasurable'),
            CHECK (tenant_id IS NOT NULL OR status IN ('legacy_unmeasurable', 'legacy_no_tenant'))
        )
        """
    )
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
    con.execute("CREATE INDEX IF NOT EXISTS idx_positions_tenant ON positions(tenant_id)")
    log.info(
        "_migrate_tenant_id_not_null: migration complete. positions enforces "
        "tenant_id IS NOT NULL or quarantine."
    )


def _migrate_direction_enum(con: sqlite3.Connection) -> None:
    """Schema CHECK: direction IN ('LONG', 'SHORT') OR status='legacy_unmeasurable'
    — #484 (move direction enum from Pydantic-boundary to schema rung).

    The Pydantic Literal["LONG", "SHORT"] on OpenPositionRequest catches
    malformed input at the HTTP boundary. But manual UPDATEs, legacy
    clients, and ad-hoc shell access bypass that boundary entirely. The
    CHECK constraint enforces the invariant at the schema rung — the
    strongest rung available.

    Existing rows are migrated as follows:
      1. SQL UPPER(direction) normalizes case-variants ('long' → 'LONG',
         'Short' → 'SHORT'). ASCII-only enums; UPPER() is safe.
      2. Anything that still doesn't match {'LONG', 'SHORT'} (NULL, empty,
         'NEUTRAL', garbage) is quarantined to status='legacy_unmeasurable'
         — consistent with the other CHECKs' OR exemption.

    Idempotent: detects `directionin('long','short')` in the whitespace-
    normalized + case-folded DDL and skips. Anchored on the exact CHECK
    fragment (not bare "long") to avoid false-positives — same pattern
    as _migrate_qty_not_null after the #476 fix.
    """
    schema_row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='positions'"
    ).fetchone()
    if not schema_row or not schema_row[0]:
        log.warning(
            "_migrate_direction_enum: positions table not found; skipping."
        )
        return
    normalized = "".join(schema_row[0].split()).lower()
    if "directionin('long','short')" in normalized:
        log.info(
            "_migrate_direction_enum: positions already enforces direction enum; "
            "skipping."
        )
        return

    # 1. Normalize case: 'long'/'Long' → 'LONG', etc.
    con.execute(
        """UPDATE positions
              SET direction = UPPER(direction)
            WHERE direction != UPPER(direction)"""
    )
    normalized_count = con.execute("SELECT changes()").fetchone()[0]
    log.info(
        "_migrate_direction_enum: normalized %d rows from mixed-case direction.",
        normalized_count,
    )

    # 2. Quarantine anything that doesn't normalize to LONG/SHORT. Common
    #    candidates: NULL (no DEFAULT applied — old INSERT without column),
    #    empty string, 'NEUTRAL', typos. They are NOT recoverable to a
    #    real direction so admit the absence rather than invent a value.
    con.execute(
        """UPDATE positions
              SET status = 'legacy_unmeasurable'
            WHERE (direction IS NULL OR direction NOT IN ('LONG', 'SHORT'))
              AND status != 'legacy_unmeasurable'"""
    )
    quarantined = con.execute("SELECT changes()").fetchone()[0]
    if quarantined:
        log.warning(
            "_migrate_direction_enum: quarantined %d rows with non-LONG/SHORT "
            "direction as 'legacy_unmeasurable'.",
            quarantined,
        )

    # 3. Recreate the table with the strengthened CHECK. All three CHECKs
    #    (qty + tenant_id + direction) compose; each was added incrementally
    #    by its own migration. The partial UNIQUE index from
    #    _migrate_unique_open_scan is re-created by that migration's own
    #    CREATE INDEX IF NOT EXISTS, which runs after this in init_db.
    log.info(
        "_migrate_direction_enum: recreating positions with CHECK "
        "(direction IN ('LONG', 'SHORT') OR status='legacy_unmeasurable')."
    )
    existing_cols = {
        row[1] for row in con.execute("PRAGMA table_info(positions)").fetchall()
    }
    # Defensive cleanup of any orphan positions_new from a prior interrupted
    # run (same pattern as _migrate_qty_not_null per #480).
    con.execute("DROP TABLE IF EXISTS positions_new")
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
            CHECK ((qty IS NOT NULL AND qty > 0) OR status = 'legacy_unmeasurable'),
            CHECK (tenant_id IS NOT NULL OR status IN ('legacy_unmeasurable', 'legacy_no_tenant')),
            CHECK (direction IN ('LONG', 'SHORT') OR status = 'legacy_unmeasurable')
        )
        """
    )
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
    con.execute("CREATE INDEX IF NOT EXISTS idx_positions_tenant ON positions(tenant_id)")
    log.info(
        "_migrate_direction_enum: migration complete. positions enforces "
        "direction IN ('LONG', 'SHORT') or quarantine."
    )


def _migrate_unique_open_scan(con: sqlite3.Connection) -> None:
    """Partial unique index on (tenant_id, scan_id) WHERE status='open' AND
    scan_id IS NOT NULL — #470 idempotency race closure.

    Closes the race window of two concurrent POST /positions with the same
    scan_id: the second INSERT fires sqlite3.IntegrityError, which
    BirthRegistrar maps to a 409 UniqueViolationError. Combined with the
    Idempotency-Key cache (Task 17), a retried client request is replayed
    safely; a duplicate client request hits the schema fence.

    Production measurement (2026-05-26): only 2 rows share scan_id=42, both
    closed. No open rows currently violate this index — migration is safe
    at-rest.

    Column-aware: if `positions` is missing OR either `tenant_id` / `scan_id`
    column is absent (pre-B.1 stub schema), skip — there is nothing to index
    against.

    Idempotent: CREATE INDEX IF NOT EXISTS.
    """
    schema_row = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='positions'"
    ).fetchone()
    if not schema_row:
        log.warning(
            "_migrate_unique_open_scan: positions table not found; skipping."
        )
        return
    existing_cols = {
        row[1] for row in con.execute("PRAGMA table_info(positions)").fetchall()
    }
    if "tenant_id" not in existing_cols or "scan_id" not in existing_cols:
        log.info(
            "_migrate_unique_open_scan: tenant_id and/or scan_id column "
            "missing; skipping (pre-B.1 stub schema)."
        )
        return
    con.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_positions_open_scan_unique
              ON positions (tenant_id, scan_id)
              WHERE status = 'open' AND scan_id IS NOT NULL"""
    )
    log.info(
        "_migrate_unique_open_scan: partial UNIQUE index ensured "
        "(tenant_id, scan_id) WHERE status='open' AND scan_id IS NOT NULL."
    )


def _migrate_idempotency_keys(con: sqlite3.Connection) -> None:
    """Idempotency-Key cache table — #470 (Voronov D-Tipo HTTP rung).

    Backs `api.positions_birth.IdempotencyCache`: per-(tenant, key) cache of
    serialized POST /positions results. 24h TTL enforced at read time
    (`expires_at > now`), with lazy cleanup deleting expired rows for the
    same (tenant, key) on each `get`. No background sweeper, no scheduler.

    Per Voronov: "performance + UX, no invariante existencial" — this is the
    operational cousin of the structural #470 partial UNIQUE index. The
    schema fence catches duplicate scan_id at the DB; this cache lets a
    well-behaved client retry safely without paying for a 409 round-trip.

    PRIMARY KEY (tenant_id, key) makes `INSERT OR REPLACE` the natural
    overwrite primitive. Secondary index on `expires_at` is reserved for a
    future eager sweeper if lazy cleanup proves insufficient (NOT used by
    the current `get` path; included for forward compatibility).

    `body_sha256` carries the SHA-256 of the canonical-JSON request body
    that produced `result_json`. BirthRegistrar compares fingerprints on
    cache hit; a mismatch raises DuplicateIdempotencyKeyError (409) per
    RFC 9457 idempotency semantics. The column is added via idempotent
    ALTER TABLE so installations created before the fingerprint guard
    pick it up on next boot (Serrano BLOCKER 1).

    Idempotent: CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS +
    PRAGMA-guarded ALTER for the body_sha256 column (a try/except on the
    ALTER would mark the enclosing BEGIN IMMEDIATE tx as abortable when
    the column already exists, which silently rolls back the entire
    D-cluster on subsequent COMMIT; PRAGMA check avoids contaminating
    the tx).
    """
    con.execute(
        """CREATE TABLE IF NOT EXISTS idempotency_keys (
               tenant_id      INTEGER NOT NULL,
               key            TEXT    NOT NULL,
               result_json    TEXT    NOT NULL,
               body_sha256    TEXT,
               created_at     TEXT    NOT NULL,
               expires_at     TEXT    NOT NULL,
               PRIMARY KEY (tenant_id, key)
           )"""
    )
    # ALTER TABLE for installations that created the table BEFORE the
    # body_sha256 column existed. PRAGMA-guarded (NOT try/except) so the
    # enclosing tx remains in a clean state when the column is already
    # present.
    existing_cols = {
        row[1]
        for row in con.execute("PRAGMA table_info(idempotency_keys)").fetchall()
    }
    if "body_sha256" not in existing_cols:
        con.execute(
            "ALTER TABLE idempotency_keys ADD COLUMN body_sha256 TEXT"
        )
        log.info(
            "_migrate_idempotency_keys: added body_sha256 column to "
            "idempotency_keys."
        )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_idempotency_expires "
        "ON idempotency_keys(expires_at)"
    )
    log.info(
        "_migrate_idempotency_keys: idempotency_keys table + expires index "
        "ensured."
    )
