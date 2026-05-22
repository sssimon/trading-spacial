"""Tests for #428 H.3 — conversation-history read endpoints.

Happy-path coverage: list / messages / delete / pin against a TestClient
seeded via record_history(). IDOR / cross-tenant tests live in
test_agent_history_idor.py (mirror B.7 #260 pattern).
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path as _Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient with fresh DB + bypass-auth. Mirror of the B.7 fixture
    so the TestClient operates as the synthetic test user (id=0)."""
    import btc_api
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    btc_api.init_db()

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"api_key": "test-key"}))
    monkeypatch.setattr(btc_api, "CONFIG_FILE", str(cfg_path), raising=False)
    import api.config as _ac
    monkeypatch.setattr(_ac, "CONFIG_FILE", str(cfg_path), raising=False)
    monkeypatch.setattr(btc_api, "DEFAULTS_FILE", str(tmp_path / "no_def.json"), raising=False)
    monkeypatch.setattr(_ac, "DEFAULTS_FILE", str(tmp_path / "no_def.json"), raising=False)
    monkeypatch.setattr(btc_api, "SECRETS_FILE", str(tmp_path / "no_sec.json"), raising=False)
    monkeypatch.setattr(_ac, "SECRETS_FILE", str(tmp_path / "no_sec.json"), raising=False)
    return TestClient(btc_api.app)


def _seed(tenant_id: int, conversation_id: str, user_msg: str,
          assistant_text: str = "ok", surface: str = "dock",
          reasoning: str | None = None,
          tool_chips: list[dict] | None = None,
          proposals: list[dict] | None = None) -> None:
    """Seed one turn via the production write path."""
    from api.agent.audit import record_history
    record_history(
        tenant_id=tenant_id, surface=surface, conversation_id=conversation_id,
        user_message=user_msg, assistant_text=assistant_text,
        assistant_reasoning=reasoning,
        tool_chips=tool_chips or [], proposals=proposals or [],
    )


def _expire_meta(conversation_id: str, when_iso: str | None = None) -> None:
    """Set expires_at on a conversation to a past timestamp (test-only
    helper — simulates the retention horizon having elapsed)."""
    from db.connection import get_db
    past = when_iso or "2000-01-01T00:00:00+00:00"
    con = get_db()
    con.execute(
        "UPDATE agent_conversation_meta SET expires_at = ? WHERE conversation_id = ?",
        (past, conversation_id),
    )
    con.execute(
        "UPDATE agent_messages SET expires_at = ? WHERE conversation_id = ?",
        (past, conversation_id),
    )
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# GET /agent/conversations
# ---------------------------------------------------------------------------


class TestListConversations:
    def test_returns_empty_when_no_data(self, client):
        resp = client.get("/agent/conversations")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"conversations": [], "total": 0, "limit": 20, "offset": 0}

    def test_returns_own_conversations_only(self, client):
        _seed(0, "conv-mine", "mi pregunta")
        resp = client.get("/agent/conversations")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["conversations"][0]["conversation_id"] == "conv-mine"
        assert body["conversations"][0]["title"] == "mi pregunta"
        assert body["conversations"][0]["pinned"] is False
        assert body["conversations"][0]["message_count"] == 2

    def test_pinned_floats_to_top(self, client):
        _seed(0, "old", "viejo", surface="dock")
        time.sleep(0.01)
        _seed(0, "new", "nuevo", surface="dock")
        # Pin the OLD one
        from db.connection import get_db
        con = get_db()
        con.execute(
            "UPDATE agent_conversation_meta SET pinned = 1 WHERE conversation_id = 'old'"
        )
        con.commit()
        con.close()

        resp = client.get("/agent/conversations")
        ids = [c["conversation_id"] for c in resp.json()["conversations"]]
        # Pinned 'old' floats above 'new' even though new has later last_ts
        assert ids == ["old", "new"]

    def test_excludes_expired_conversations(self, client):
        _seed(0, "expired", "expirado")
        _seed(0, "fresh", "vigente")
        _expire_meta("expired")

        resp = client.get("/agent/conversations")
        ids = [c["conversation_id"] for c in resp.json()["conversations"]]
        assert ids == ["fresh"]
        assert resp.json()["total"] == 1

    def test_filter_by_surface(self, client):
        _seed(0, "from-dock", "del dock", surface="dock")
        _seed(0, "from-detail", "del symbol detail", surface="symbol_detail")

        resp = client.get("/agent/conversations?surface=symbol_detail")
        ids = [c["conversation_id"] for c in resp.json()["conversations"]]
        assert ids == ["from-detail"]

    def test_search_q_matches_title(self, client):
        _seed(0, "a", "que opinas de PENDLE?")
        _seed(0, "b", "que tal BTC?")

        resp = client.get("/agent/conversations?q=PENDLE")
        ids = [c["conversation_id"] for c in resp.json()["conversations"]]
        assert ids == ["a"]

    def test_search_q_matches_message_content(self, client):
        """The user message includes 'BTC' in its title (and content), but
        the SEARCH key 'ZONA LRC' lives only in the assistant body."""
        _seed(0, "a", "que opinas?", assistant_text="BTC está en ZONA LRC baja")
        _seed(0, "b", "otra cosa", assistant_text="respuesta cualquiera")

        resp = client.get("/agent/conversations?q=ZONA LRC")
        ids = [c["conversation_id"] for c in resp.json()["conversations"]]
        assert ids == ["a"]

    def test_search_escapes_like_wildcards(self, client):
        """A user searching for '%' must match literal '%', not 'anything'."""
        _seed(0, "a", "100% rentable")
        _seed(0, "b", "perdida total")

        resp = client.get("/agent/conversations?q=%25")  # URL-encoded %
        ids = [c["conversation_id"] for c in resp.json()["conversations"]]
        assert ids == ["a"]

    def test_pagination_limit_offset(self, client):
        for i in range(5):
            _seed(0, f"c{i}", f"msg {i}")
            time.sleep(0.005)
        resp = client.get("/agent/conversations?limit=2&offset=1")
        body = resp.json()
        assert body["limit"] == 2
        assert body["offset"] == 1
        assert len(body["conversations"]) == 2
        assert body["total"] == 5  # total of full set, not page size

    def test_limit_cap_max_100(self, client):
        resp = client.get("/agent/conversations?limit=101")
        assert resp.status_code == 422  # FastAPI validation error


# ---------------------------------------------------------------------------
# GET /agent/conversations/{id}/messages
# ---------------------------------------------------------------------------


class TestGetMessages:
    def test_returns_ordered_transcript(self, client):
        _seed(0, "conv-X", "primera", assistant_text="A1")
        time.sleep(0.01)
        _seed(0, "conv-X", "segunda", assistant_text="A2")

        resp = client.get("/agent/conversations/conv-X/messages")
        assert resp.status_code == 200
        body = resp.json()
        assert body["conversation_id"] == "conv-X"
        assert body["title"] == "primera"
        roles = [m["role"] for m in body["messages"]]
        contents = [m["content"] for m in body["messages"]]
        assert roles == ["user", "assistant", "user", "assistant"]
        assert contents == ["primera", "A1", "segunda", "A2"]

    def test_includes_reasoning_and_chips(self, client):
        _seed(
            0, "conv-Y", "usa tools",
            assistant_text="respuesta",
            reasoning="<think>razonamiento</think>",
            tool_chips=[{"tool": "get_positions", "status": "ok"}],
        )
        resp = client.get("/agent/conversations/conv-Y/messages")
        body = resp.json()
        assistant = next(m for m in body["messages"] if m["role"] == "assistant")
        assert assistant["reasoning"] == "<think>razonamiento</think>"
        assert assistant["tool_chips"] == [{"tool": "get_positions", "status": "ok"}]

    def test_proposal_state_pending_when_never_confirmed(self, client):
        """No matching row in agent_side_effects + future expires_at →
        state is 'pending'."""
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        _seed(
            0, "conv-P1", "cerra mi BTC",
            assistant_text="propongo cerrar",
            proposals=[{
                "proposal_id": "prop-p1",
                "action":      "close_position",
                "args":        {"position_id": 7},
                "expires_at":  future,
                "summary":     "Cerrar #7",
            }],
        )
        resp = client.get("/agent/conversations/conv-P1/messages")
        assistant = next(m for m in resp.json()["messages"] if m["role"] == "assistant")
        assert assistant["proposals"][0]["state"] == "pending"
        # signed_payload never present in the response
        assert "signed_payload" not in assistant["proposals"][0]

    def test_proposal_state_expired_when_ttl_passed(self, client):
        past = "2020-01-01T00:00:00+00:00"
        _seed(
            0, "conv-P2", "cerra ya",
            proposals=[{
                "proposal_id": "prop-p2",
                "action":      "close_position",
                "args":        {"position_id": 8},
                "expires_at":  past,
                "summary":     "Cerrar #8",
            }],
        )
        resp = client.get("/agent/conversations/conv-P2/messages")
        assistant = next(m for m in resp.json()["messages"] if m["role"] == "assistant")
        assert assistant["proposals"][0]["state"] == "expired"

    def test_proposal_state_ok_from_agent_side_effects(self, client):
        """If the user confirmed the proposal, agent_side_effects has the
        terminal result. State derives from there, not from expires_at."""
        from db.connection import get_db
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        _seed(
            0, "conv-P3", "cerra",
            proposals=[{
                "proposal_id": "prop-p3",
                "action":      "close_position",
                "args":        {"position_id": 9},
                "expires_at":  future,
                "summary":     "Cerrar #9",
            }],
        )
        # Pretend the confirm fired and recorded an 'ok'
        con = get_db()
        con.execute(
            """INSERT INTO agent_side_effects
               (tenant_id, conversation_id, ts, action, args_json,
                idempotency_key, result, http_status, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (0, "conv-P3", "2026-05-22T10:00:00Z", "close_position",
             "{}", "prop-p3", "ok", 200, future),
        )
        con.commit()
        con.close()

        resp = client.get("/agent/conversations/conv-P3/messages")
        assistant = next(m for m in resp.json()["messages"] if m["role"] == "assistant")
        assert assistant["proposals"][0]["state"] == "ok"

    def test_404_for_nonexistent_conversation(self, client):
        resp = client.get("/agent/conversations/does-not-exist/messages")
        assert resp.status_code == 404

    def test_404_for_expired_conversation(self, client):
        _seed(0, "conv-expired", "viejo")
        _expire_meta("conv-expired")
        resp = client.get("/agent/conversations/conv-expired/messages")
        assert resp.status_code == 404

    def test_404_for_invalid_conversation_id_chars(self, client):
        """Path pattern blocks special chars."""
        resp = client.get("/agent/conversations/has space/messages")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /agent/conversations/{id} — soft delete
# ---------------------------------------------------------------------------


class TestDeleteConversation:
    def test_soft_delete_removes_from_list_and_messages(self, client):
        _seed(0, "conv-D1", "borrame")
        # Sanity: visible before delete
        assert len(client.get("/agent/conversations").json()["conversations"]) == 1

        resp = client.delete(
            "/agent/conversations/conv-D1",
            headers={"X-API-Key": "test-key"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

        # Hidden after
        assert client.get("/agent/conversations").json()["conversations"] == []
        # GET messages now 404s
        assert client.get("/agent/conversations/conv-D1/messages").status_code == 404

    def test_404_for_nonexistent_conversation(self, client):
        resp = client.delete(
            "/agent/conversations/does-not-exist",
            headers={"X-API-Key": "test-key"},
        )
        assert resp.status_code == 404

    def test_404_for_already_deleted_conversation(self, client):
        _seed(0, "conv-D2", "hola")
        client.delete("/agent/conversations/conv-D2",
                      headers={"X-API-Key": "test-key"})
        # Second delete on the same conversation
        resp = client.delete("/agent/conversations/conv-D2",
                             headers={"X-API-Key": "test-key"})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /agent/conversations/{id}/pin — toggle
# ---------------------------------------------------------------------------


class TestTogglePin:
    def test_pin_then_unpin(self, client):
        _seed(0, "conv-PN1", "fijame")

        # Pin
        resp1 = client.post(
            "/agent/conversations/conv-PN1/pin",
            headers={"X-API-Key": "test-key"},
        )
        assert resp1.status_code == 200
        assert resp1.json() == {"ok": True, "pinned": True}

        # Verify list shows pinned=True
        body = client.get("/agent/conversations").json()
        assert body["conversations"][0]["pinned"] is True

        # Unpin
        resp2 = client.post(
            "/agent/conversations/conv-PN1/pin",
            headers={"X-API-Key": "test-key"},
        )
        assert resp2.status_code == 200
        assert resp2.json() == {"ok": True, "pinned": False}

    def test_404_on_nonexistent(self, client):
        resp = client.post(
            "/agent/conversations/nope/pin",
            headers={"X-API-Key": "test-key"},
        )
        assert resp.status_code == 404

    def test_404_on_expired(self, client):
        _seed(0, "conv-PN2", "viejo")
        _expire_meta("conv-PN2")
        resp = client.post(
            "/agent/conversations/conv-PN2/pin",
            headers={"X-API-Key": "test-key"},
        )
        assert resp.status_code == 404
