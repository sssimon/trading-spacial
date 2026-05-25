"""Tests for the per-tenant conversation-history schema (#428 H.1).

Pre-reg: docs/superpowers/specs/es/2026-05-22-conversation-history-pre-reg.md

Tests cover:
- Both tables exist with all expected columns + types.
- NOT NULL constraints on the columns that the write path will populate.
- PRIMARY KEY shape on agent_conversation_meta (conversation_id, not autoinc).
- Indexes present (tenant_conv_ts ASC, tenant_ts DESC, expires, conv_meta).
- Idempotency: init_db() twice does not raise nor duplicate columns.
- Existing audit tables (agent_conversations, agent_side_effects,
  agent_quotas) are NOT touched by this migration.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Fresh empty DB at tmp_path/test.db, monkey-patched into btc_api."""
    import btc_api
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(btc_api, "DB_FILE", str(db_path))
    yield db_path


@pytest.fixture
def initialized_db(tmp_db):
    from db.schema import init_db
    init_db()
    return tmp_db


def _get_columns(db_path: Path, table: str) -> dict[str, tuple[str, int, int]]:
    """Return {col_name: (type, notnull, pk)}."""
    con = sqlite3.connect(db_path)
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    con.close()
    # PRAGMA table_info returns (cid, name, type, notnull, dflt_value, pk)
    return {r[1]: (r[2], r[3], r[5]) for r in rows}


def _get_indexes(db_path: Path, table: str) -> list[str]:
    con = sqlite3.connect(db_path)
    rows = con.execute(f"PRAGMA index_list({table})").fetchall()
    con.close()
    return [r[1] for r in rows]


def _get_index_columns(db_path: Path, index_name: str) -> list[str]:
    """Ordered list of columns on a given index."""
    con = sqlite3.connect(db_path)
    rows = con.execute(f"PRAGMA index_info({index_name})").fetchall()
    con.close()
    # PRAGMA index_info returns (seqno, cid, name)
    return [r[2] for r in sorted(rows, key=lambda r: r[0])]


# ---------------------------------------------------------------------------
# agent_messages
# ---------------------------------------------------------------------------


class TestAgentMessagesTable:
    def test_table_exists(self, initialized_db):
        cols = _get_columns(initialized_db, "agent_messages")
        assert cols, "agent_messages table missing"

    def test_columns_present(self, initialized_db):
        cols = _get_columns(initialized_db, "agent_messages")
        expected = {
            "id", "tenant_id", "conversation_id", "ts", "role",
            "content", "reasoning", "tool_chips_json", "proposals_json",
            "expires_at",
        }
        assert expected.issubset(cols.keys()), f"missing: {expected - cols.keys()}"

    def test_id_is_primary_key(self, initialized_db):
        cols = _get_columns(initialized_db, "agent_messages")
        assert cols["id"][2] == 1, "id should be primary key"

    def test_not_null_constraints(self, initialized_db):
        """Columns the write path populates MUST be NOT NULL.

        Nullable by design: reasoning (only DS-R1 populates), tool_chips_json,
        proposals_json (only assistant-with-tools turns populate).
        """
        cols = _get_columns(initialized_db, "agent_messages")
        for not_null_col in ("tenant_id", "conversation_id", "ts", "role",
                             "content", "expires_at"):
            assert cols[not_null_col][1] == 1, f"{not_null_col} should be NOT NULL"
        for nullable_col in ("reasoning", "tool_chips_json", "proposals_json"):
            assert cols[nullable_col][1] == 0, f"{nullable_col} should be nullable"


class TestAgentMessagesIndexes:
    def test_tenant_conv_ts_index_exists(self, initialized_db):
        assert "idx_agent_messages_tenant_conv_ts" in _get_indexes(
            initialized_db, "agent_messages",
        )

    def test_tenant_ts_index_exists(self, initialized_db):
        assert "idx_agent_messages_tenant_ts" in _get_indexes(
            initialized_db, "agent_messages",
        )

    def test_expires_index_exists(self, initialized_db):
        assert "idx_agent_messages_expires" in _get_indexes(
            initialized_db, "agent_messages",
        )

    def test_tenant_conv_ts_column_order(self, initialized_db):
        """Composite index must be (tenant_id, conversation_id, ts) in that
        order — query pattern is filter by tenant + conversation_id,
        then order by ts ASC for transcript reconstruction."""
        cols = _get_index_columns(
            initialized_db, "idx_agent_messages_tenant_conv_ts",
        )
        assert cols == ["tenant_id", "conversation_id", "ts"]

    def test_tenant_ts_column_order(self, initialized_db):
        """For sidebar 'recent across all conversations' query."""
        cols = _get_index_columns(initialized_db, "idx_agent_messages_tenant_ts")
        assert cols == ["tenant_id", "ts"]


class TestAgentMessagesInsert:
    """Smoke-test inserts to verify the column shape actually accepts the
    write path's typical payloads. No SQL constraint surprises."""

    def test_insert_user_message_minimal(self, initialized_db):
        con = sqlite3.connect(initialized_db)
        con.execute(
            "INSERT INTO agent_messages "
            "(tenant_id, conversation_id, ts, role, content, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (1, "conv-abc", "2026-05-22T08:00:00Z", "user",
             "que opinas de PENDLE?", "2026-08-20T08:00:00Z"),
        )
        con.commit()
        row = con.execute(
            "SELECT role, content, reasoning, tool_chips_json, proposals_json "
            "FROM agent_messages WHERE conversation_id='conv-abc'"
        ).fetchone()
        assert row == ("user", "que opinas de PENDLE?", None, None, None)
        con.close()

    def test_insert_assistant_with_reasoning_and_chips(self, initialized_db):
        con = sqlite3.connect(initialized_db)
        con.execute(
            "INSERT INTO agent_messages "
            "(tenant_id, conversation_id, ts, role, content, reasoning, "
            "tool_chips_json, proposals_json, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1, "conv-abc", "2026-05-22T08:00:01Z", "assistant",
             "PENDLE está en zona LRC baja...", "<reasoning>think...</reasoning>",
             '[{"tool":"get_symbol_state","status":"ok"}]',
             '[{"proposal_id":"p1","action":"close_position"}]',
             "2026-08-20T08:00:00Z"),
        )
        con.commit()
        row = con.execute(
            "SELECT reasoning, tool_chips_json IS NOT NULL, proposals_json IS NOT NULL "
            "FROM agent_messages WHERE id=1"
        ).fetchone()
        assert row[0].startswith("<reasoning>")
        assert row[1] == 1
        assert row[2] == 1
        con.close()

    def test_insert_missing_required_field_raises(self, initialized_db):
        """Omitting a NOT NULL column raises IntegrityError."""
        con = sqlite3.connect(initialized_db)
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                # Missing `content` — NOT NULL
                "INSERT INTO agent_messages "
                "(tenant_id, conversation_id, ts, role, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (1, "c", "2026-05-22T08:00:00Z", "user", "2026-08-20T08:00:00Z"),
            )
            con.commit()
        con.close()


# ---------------------------------------------------------------------------
# agent_conversation_meta
# ---------------------------------------------------------------------------


class TestAgentConversationMetaTable:
    def test_table_exists(self, initialized_db):
        cols = _get_columns(initialized_db, "agent_conversation_meta")
        assert cols, "agent_conversation_meta table missing"

    def test_columns_present(self, initialized_db):
        cols = _get_columns(initialized_db, "agent_conversation_meta")
        expected = {
            "conversation_id", "tenant_id", "title", "surface",
            "first_ts", "last_ts", "message_count", "pinned", "expires_at",
        }
        assert expected.issubset(cols.keys()), f"missing: {expected - cols.keys()}"

    def test_conversation_id_is_primary_key(self, initialized_db):
        """Natural PK — UUID generated by the frontend."""
        cols = _get_columns(initialized_db, "agent_conversation_meta")
        assert cols["conversation_id"][2] == 1, "conversation_id should be PK"

    def test_not_null_constraints(self, initialized_db):
        cols = _get_columns(initialized_db, "agent_conversation_meta")
        for not_null_col in ("conversation_id", "tenant_id", "surface",
                             "first_ts", "last_ts", "message_count",
                             "pinned", "expires_at"):
            assert cols[not_null_col][1] == 1, f"{not_null_col} should be NOT NULL"
        # title is nullable — derived after first user turn lands
        assert cols["title"][1] == 0


class TestAgentConversationMetaIndexes:
    def test_tenant_last_index_exists(self, initialized_db):
        assert "idx_agent_conv_meta_tenant_last" in _get_indexes(
            initialized_db, "agent_conversation_meta",
        )

    def test_tenant_pinned_index_exists(self, initialized_db):
        assert "idx_agent_conv_meta_tenant_pinned" in _get_indexes(
            initialized_db, "agent_conversation_meta",
        )

    def test_tenant_pinned_column_order(self, initialized_db):
        """Sidebar query orders by pinned DESC then last_ts DESC; the
        index must agree on that column order."""
        cols = _get_index_columns(
            initialized_db, "idx_agent_conv_meta_tenant_pinned",
        )
        assert cols == ["tenant_id", "pinned", "last_ts"]


class TestAgentConversationMetaInsert:
    def test_conversation_id_unique(self, initialized_db):
        """PK on conversation_id rejects duplicates."""
        con = sqlite3.connect(initialized_db)
        con.execute(
            "INSERT INTO agent_conversation_meta "
            "(conversation_id, tenant_id, surface, first_ts, last_ts, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("conv-1", 1, "dock", "2026-05-22T08:00:00Z",
             "2026-05-22T08:00:00Z", "2026-08-20T08:00:00Z"),
        )
        con.commit()
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO agent_conversation_meta "
                "(conversation_id, tenant_id, surface, first_ts, last_ts, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("conv-1", 1, "dock", "2026-05-22T09:00:00Z",
                 "2026-05-22T09:00:00Z", "2026-08-20T09:00:00Z"),
            )
            con.commit()
        con.close()

    def test_defaults_message_count_and_pinned(self, initialized_db):
        con = sqlite3.connect(initialized_db)
        con.execute(
            "INSERT INTO agent_conversation_meta "
            "(conversation_id, tenant_id, surface, first_ts, last_ts, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("conv-2", 1, "symbol_detail", "2026-05-22T08:00:00Z",
             "2026-05-22T08:00:00Z", "2026-08-20T08:00:00Z"),
        )
        con.commit()
        row = con.execute(
            "SELECT message_count, pinned FROM agent_conversation_meta "
            "WHERE conversation_id='conv-2'"
        ).fetchone()
        assert row == (0, 0)
        con.close()


# ---------------------------------------------------------------------------
# Idempotency + non-interference
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_init_db_twice_is_safe(self, tmp_db):
        from db.schema import init_db
        init_db()
        init_db()
        # Tables and indexes still expected
        cols = _get_columns(tmp_db, "agent_messages")
        assert "tenant_id" in cols
        indexes = _get_indexes(tmp_db, "agent_messages")
        assert "idx_agent_messages_tenant_conv_ts" in indexes

    def test_migrate_agent_history_directly_twice(self, initialized_db):
        from db.schema import _migrate_agent_history
        from db.transaction import transaction
        with transaction() as con:
            _migrate_agent_history(con)
        with transaction() as con:
            _migrate_agent_history(con)  # must not raise
        cols = _get_columns(initialized_db, "agent_conversation_meta")
        assert "conversation_id" in cols


class TestNonInterferenceWithAuditTables:
    """H.1 must NOT touch the existing audit + side-effect + quota tables.

    Pre-reg D.1: separation of audit ledger from user-visible history.
    """

    def test_agent_conversations_unchanged(self, initialized_db):
        """The audit ledger keeps its own schema; this migration leaves
        it intact (no extra columns, no extra indexes)."""
        cols = _get_columns(initialized_db, "agent_conversations")
        # Sanity: agent_conversations does NOT acquire columns that
        # belong to the history table (would indicate accidental
        # cross-pollination).
        for forbidden in ("expires_at", "tool_chips_json", "proposals_json"):
            assert forbidden not in cols, (
                f"agent_conversations gained {forbidden}; that column "
                f"belongs to agent_messages only"
            )

    def test_agent_side_effects_unchanged(self, initialized_db):
        cols = _get_columns(initialized_db, "agent_side_effects")
        assert "idempotency_key" in cols  # original schema preserved

    def test_agent_quotas_unchanged(self, initialized_db):
        cols = _get_columns(initialized_db, "agent_quotas")
        assert "daily_usd_cap" in cols  # original schema preserved
