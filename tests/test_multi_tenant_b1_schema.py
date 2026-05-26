"""Tests for multi-tenant B.1 schema changes (#254).

Pre-reg: docs/superpowers/plans/2026-05-15-multi-tenant-b1-schema-pre-reg.md

Tests cover:
- Fresh DB creation produces all multi-tenant artifacts
- Migration on existing-style DB adds tenant_id without breaking existing rows
- Idempotency (running init_db twice is safe)
- backfill_tenant correctly updates NULL tenant_ids
- Index existence + UNIQUE constraints
- NULL tenant_id allowed pre-backfill (B.1 doesn't enforce NOT NULL)
- Schema introspection matches spec
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Test pattern: monkeypatch btc_api.DB_FILE so init_db() uses tmp path
@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Fresh empty DB at tmp_path/test.db — uses real btc_api like existing test pattern."""
    import btc_api
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(btc_api, "DB_FILE", str(db_path))
    yield db_path


@pytest.fixture
def initialized_db(tmp_db):
    from db.schema import init_db
    init_db()
    return tmp_db


def _get_columns(db_path: Path, table: str) -> dict[str, str]:
    """Return {column_name: type} for given table."""
    con = sqlite3.connect(db_path)
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    con.close()
    return {r[1]: r[2] for r in rows}


def _get_indexes(db_path: Path, table: str) -> list[str]:
    """List index names on a table."""
    con = sqlite3.connect(db_path)
    rows = con.execute(f"PRAGMA index_list({table})").fetchall()
    con.close()
    return [r[1] for r in rows]


# ---------------------------------------------------------------------------
# Per-user tables — tenant_id column added
# ---------------------------------------------------------------------------


class TestTenantIdColumnAdded:
    def test_positions_has_tenant_id(self, initialized_db):
        cols = _get_columns(initialized_db, "positions")
        assert "tenant_id" in cols
        assert cols["tenant_id"] == "INTEGER"

    def test_signal_outcomes_has_tenant_id(self, initialized_db):
        cols = _get_columns(initialized_db, "signal_outcomes")
        assert "tenant_id" in cols
        assert cols["tenant_id"] == "INTEGER"

    def test_notifications_sent_has_tenant_id(self, initialized_db):
        cols = _get_columns(initialized_db, "notifications_sent")
        assert "tenant_id" in cols

    def test_portfolio_health_events_has_tenant_id(self, initialized_db):
        cols = _get_columns(initialized_db, "portfolio_health_events")
        assert "tenant_id" in cols


class TestGlobalTablesDoNotHaveTenantId:
    """Global tables (per pre-reg §2.2) must NOT have tenant_id."""

    def test_scans_no_tenant_id(self, initialized_db):
        cols = _get_columns(initialized_db, "scans")
        assert "tenant_id" not in cols

    def test_tune_results_no_tenant_id(self, initialized_db):
        cols = _get_columns(initialized_db, "tune_results")
        assert "tenant_id" not in cols

    def test_symbol_health_no_tenant_id(self, initialized_db):
        cols = _get_columns(initialized_db, "symbol_health")
        assert "tenant_id" not in cols


class TestDeferredKillSwitchTables:
    """Per pre-reg §2.3, kill_switch_* tables intentionally kept global in B.1."""

    def test_kill_switch_decisions_no_tenant_id(self, initialized_db):
        cols = _get_columns(initialized_db, "kill_switch_decisions")
        assert "tenant_id" not in cols, "kill_switch tables deferred per B.1 pre-reg §2.3"

    def test_kill_switch_v2_state_no_tenant_id(self, initialized_db):
        cols = _get_columns(initialized_db, "kill_switch_v2_state")
        assert "tenant_id" not in cols


# ---------------------------------------------------------------------------
# New tables — capital + user_preferences
# ---------------------------------------------------------------------------


class TestNewTables:
    def test_capital_table_exists(self, initialized_db):
        cols = _get_columns(initialized_db, "capital")
        assert "id" in cols
        assert "tenant_id" in cols
        assert "balance" in cols
        assert "peak_balance" in cols
        assert "max_drawdown_pct" in cols
        assert "updated_at" in cols

    def test_user_preferences_table_exists(self, initialized_db):
        cols = _get_columns(initialized_db, "user_preferences")
        assert "id" in cols
        assert "tenant_id" in cols
        assert "symbol_filter_json" in cols
        assert "min_score" in cols
        assert "notify_channels_json" in cols
        assert "updated_at" in cols

    def test_capital_unique_tenant(self, initialized_db):
        """Cannot have two capital rows for same tenant."""
        con = sqlite3.connect(initialized_db)
        now = datetime.now(timezone.utc).isoformat()
        con.execute(
            "INSERT INTO capital(tenant_id, balance, peak_balance, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (1, 10000.0, 10000.0, now),
        )
        con.commit()
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO capital(tenant_id, balance, peak_balance, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (1, 11000.0, 11000.0, now),
            )
            con.commit()
        con.close()

    def test_user_prefs_unique_tenant(self, initialized_db):
        """Cannot have two user_preferences rows for same tenant."""
        con = sqlite3.connect(initialized_db)
        now = datetime.now(timezone.utc).isoformat()
        con.execute(
            "INSERT INTO user_preferences(tenant_id, min_score, updated_at) "
            "VALUES (?, ?, ?)",
            (1, 4, now),
        )
        con.commit()
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO user_preferences(tenant_id, min_score, updated_at) "
                "VALUES (?, ?, ?)",
                (1, 5, now),
            )
            con.commit()
        con.close()


# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------


class TestIndexes:
    def test_positions_tenant_index(self, initialized_db):
        indexes = _get_indexes(initialized_db, "positions")
        assert "idx_positions_tenant" in indexes

    def test_signal_outcomes_tenant_index(self, initialized_db):
        indexes = _get_indexes(initialized_db, "signal_outcomes")
        assert "idx_signal_outcomes_tenant" in indexes

    def test_notif_tenant_unread_index(self, initialized_db):
        indexes = _get_indexes(initialized_db, "notifications_sent")
        assert "idx_notif_tenant_unread" in indexes
        # Old global index still present (both coexist per pre-reg §3.2)
        assert "idx_notif_sent_unread" in indexes

    def test_portfolio_events_tenant_ts_index(self, initialized_db):
        indexes = _get_indexes(initialized_db, "portfolio_health_events")
        assert "idx_portfolio_events_tenant_ts" in indexes

    def test_capital_tenant_unique_index(self, initialized_db):
        indexes = _get_indexes(initialized_db, "capital")
        assert "idx_capital_tenant" in indexes

    def test_user_prefs_tenant_unique_index(self, initialized_db):
        indexes = _get_indexes(initialized_db, "user_preferences")
        assert "idx_user_prefs_tenant" in indexes


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_init_db_twice_is_safe(self, tmp_db):
        """Running init_db() multiple times should not raise nor duplicate."""
        from db.schema import init_db
        init_db()
        init_db()  # second call must not fail
        # Verify tables + columns + indexes still in expected state
        cols = _get_columns(tmp_db, "positions")
        assert "tenant_id" in cols
        # Verify no double-column scenarios
        all_cols = list(cols.keys())
        assert all_cols.count("tenant_id") == 1


# ---------------------------------------------------------------------------
# NULL tenant_id allowed (B.1 doesn't enforce NOT NULL)
# ---------------------------------------------------------------------------


class TestNullTenantIdAllowed:
    def test_insert_position_with_null_tenant(self, initialized_db):
        """B.1 left tenant_id nullable; D (#471) enforces NOT NULL via CHECK
        except via the documented `legacy_no_tenant` escape hatch. Rows with
        NULL tenant_id must adopt this status to satisfy the constraint."""
        con = sqlite3.connect(initialized_db)
        con.execute(
            "INSERT INTO positions(symbol, direction, status, entry_price, entry_ts, qty, tenant_id) "
            "VALUES (?, ?, ?, ?, ?, 1.0, NULL)",
            ("BTCUSDT", "LONG", "legacy_no_tenant", 80000.0, "2026-05-15T12:00:00Z"),
        )
        con.commit()
        row = con.execute(
            "SELECT tenant_id FROM positions WHERE symbol='BTCUSDT' LIMIT 1"
        ).fetchone()
        assert row[0] is None
        con.close()


# ---------------------------------------------------------------------------
# backfill_tenant
# ---------------------------------------------------------------------------


class TestBackfillTenant:
    def test_backfill_updates_null_rows(self, initialized_db):
        """backfill_tenant sets tenant_id = user_id for NULL rows."""
        from db.schema import backfill_tenant

        con = sqlite3.connect(initialized_db)
        # Insert positions with NULL tenant_id (simulating pre-multi-tenant data)
        for symbol, price in (("BTCUSDT", 80000), ("ETHUSDT", 2300), ("RUNEUSDT", 0.5)):
            con.execute(
                "INSERT INTO positions(symbol, direction, status, entry_price, entry_ts, qty, tenant_id) "
                "VALUES (?, 'LONG', 'legacy_no_tenant', ?, '2026-05-15T12:00:00Z', 1.0, NULL)",
                (symbol, price),
            )
        con.commit()
        con.close()

        from db.transaction import transaction
        with transaction() as _con:
            affected = backfill_tenant(_con, user_id=99)
        assert affected["positions"] == 3
        # Other tables had 0 NULL rows
        assert affected["signal_outcomes"] == 0
        assert affected["notifications_sent"] == 0
        assert affected["portfolio_health_events"] == 0

        # Verify rows actually updated
        con = sqlite3.connect(initialized_db)
        rows = con.execute("SELECT tenant_id FROM positions").fetchall()
        assert all(r[0] == 99 for r in rows)
        con.close()

    def test_backfill_idempotent(self, initialized_db):
        """Second backfill call is a no-op."""
        from db.schema import backfill_tenant

        con = sqlite3.connect(initialized_db)
        con.execute(
            "INSERT INTO positions(symbol, direction, status, entry_price, entry_ts, qty, tenant_id) "
            "VALUES (?, 'LONG', 'legacy_no_tenant', ?, '2026-05-15T12:00:00Z', 1.0, NULL)",
            ("BTCUSDT", 80000),
        )
        con.commit()
        con.close()

        from db.transaction import transaction
        with transaction() as _con:
            first = backfill_tenant(_con, user_id=42)
        assert first["positions"] == 1
        with transaction() as _con:
            second = backfill_tenant(_con, user_id=42)
        assert second["positions"] == 0  # idempotent

    def test_backfill_preserves_existing_tenant_ids(self, initialized_db):
        """Rows with existing tenant_id are NOT overwritten."""
        from db.schema import backfill_tenant

        con = sqlite3.connect(initialized_db)
        # One row with explicit tenant_id, one with NULL
        con.execute(
            "INSERT INTO positions(symbol, direction, status, entry_price, entry_ts, qty, tenant_id) "
            "VALUES ('BTCUSDT', 'LONG', 'open', 80000, '2026-05-15T12:00:00Z', 1.0, 7)"
        )
        con.execute(
            "INSERT INTO positions(symbol, direction, status, entry_price, entry_ts, qty, tenant_id) "
            "VALUES ('ETHUSDT', 'LONG', 'legacy_no_tenant', 2300, '2026-05-15T12:00:00Z', 1.0, NULL)"
        )
        con.commit()
        con.close()

        from db.transaction import transaction
        with transaction() as _con:
            affected = backfill_tenant(_con, user_id=99)
        assert affected["positions"] == 1  # only the NULL one updated

        con = sqlite3.connect(initialized_db)
        rows = dict(con.execute("SELECT symbol, tenant_id FROM positions").fetchall())
        assert rows["BTCUSDT"] == 7
        assert rows["ETHUSDT"] == 99
        con.close()


# ---------------------------------------------------------------------------
# Migration on existing-style DB (no tenant_id, no new tables)
# ---------------------------------------------------------------------------


class TestMigrationOnExistingDB:
    def test_migration_adds_tenant_id_without_breaking_rows(self, tmp_db, monkeypatch):
        """Simulate pre-B.1 DB (positions table without tenant_id), run init_db().

        Existing rows should remain accessible; tenant_id column added as NULL.

        Note: this fixture exercises the no-qty-column branch of
        _migrate_qty_not_null (the stub schema has no qty column). Per #474,
        that branch refuses to bulk-quarantine non-empty tables unless the
        operator explicitly opts in via env flag. The test sets the flag
        because the stub-schema scenario is legitimate (this is what the
        flag was designed for: pre-qty DBs being upgraded).
        """
        monkeypatch.setenv("MIGRATE_QTY_ALLOW_BULK_QUARANTINE", "1")

        # Step 1: Create OLD-style positions table (without tenant_id)
        con = sqlite3.connect(tmp_db)
        con.execute("""
            CREATE TABLE positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL DEFAULT 'LONG',
                status TEXT NOT NULL DEFAULT 'open',
                entry_price REAL NOT NULL,
                entry_ts TEXT NOT NULL
            )
        """)
        con.execute(
            "INSERT INTO positions(symbol, direction, status, entry_price, entry_ts) "
            "VALUES ('BTCUSDT', 'LONG', 'open', 80000, '2026-05-15T12:00:00Z')"
        )
        con.commit()
        con.close()

        # Step 2: Run init_db() — should add tenant_id without errors
        from db.schema import init_db
        init_db()

        # Step 3: Verify tenant_id column added + existing row preserved + tenant_id NULL
        cols = _get_columns(tmp_db, "positions")
        assert "tenant_id" in cols

        con = sqlite3.connect(tmp_db)
        row = con.execute("SELECT symbol, tenant_id FROM positions WHERE id=1").fetchone()
        assert row[0] == "BTCUSDT"
        assert row[1] is None
        con.close()


# ---------------------------------------------------------------------------
# Schema introspection
# ---------------------------------------------------------------------------


class TestPerUserTablesConstant:
    """Verify PER_USER_TABLES constant matches pre-reg §2.1."""

    def test_per_user_tables_membership(self):
        from db.schema import PER_USER_TABLES
        expected = {
            "positions", "signal_outcomes", "notifications_sent",
            "portfolio_health_events",
        }
        assert set(PER_USER_TABLES) == expected

    def test_kill_switch_not_in_per_user(self):
        """Pre-reg §2.3: kill_switch_* explicitly deferred from per-user list."""
        from db.schema import PER_USER_TABLES
        for table in PER_USER_TABLES:
            assert "kill_switch" not in table
