"""IDOR suite for #428 H.3 conversation-history endpoints.

Mirrors the B.7 #260 pattern. TestClient operates as synthetic user
(id=99 post-#446 Task 6; was id=0 pre-fix). Cross-tenant data is
seeded for OTHER_USER_ID via the
production write path. Every endpoint must:

- Hide other users' conversations from the list.
- Return 404 (not 403) when probing other users' conversation_ids —
  no existence leak across tenants.
- Refuse to mutate other users' state (delete / pin).
- Drop query/header tenant tampering.

Threat model: docs/superpowers/specs/es/2026-05-22-conversation-history-pre-reg.md §7
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


OTHER_USER_ID = 999  # synthetic "other tenant" — distinct from TestClient (id=99)


@pytest.fixture
def client(tmp_path, monkeypatch):
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


def _seed(tenant_id: int, conversation_id: str, user_msg: str = "msg",
          assistant_text: str = "ok", surface: str = "dock") -> None:
    from api.agent.audit import record_history
    record_history(
        tenant_id=tenant_id, surface=surface,
        conversation_id=conversation_id,
        user_message=user_msg, assistant_text=assistant_text,
        assistant_reasoning=None, tool_chips=[], proposals=[],
    )


# ---------------------------------------------------------------------------
# GET /agent/conversations — list
# ---------------------------------------------------------------------------


class TestListIDOR:
    def test_list_excludes_other_tenants(self, client):
        _seed(OTHER_USER_ID, "victim-conv", "secreto de la victima")
        _seed(99, "my-conv", "mi pregunta")

        body = client.get("/agent/conversations").json()
        ids = [c["conversation_id"] for c in body["conversations"]]
        assert ids == ["my-conv"]
        assert body["total"] == 1

    def test_list_empty_when_only_other_tenant_has_conversations(self, client):
        _seed(OTHER_USER_ID, "v1", "..")
        _seed(OTHER_USER_ID, "v2", "..")

        body = client.get("/agent/conversations").json()
        assert body["conversations"] == []
        assert body["total"] == 0

    def test_search_q_does_not_leak_other_tenant_content(self, client):
        """An attacker can't fish for content via q-search across tenants.
        Even if the victim's messages contain the search keyword, the
        attacker's list must not surface them."""
        _seed(OTHER_USER_ID, "v-conv",
              user_msg="contraseña super secreta xyz",
              assistant_text="otro secreto")
        _seed(99, "my-conv", user_msg="hola", assistant_text="qué tal")

        body = client.get("/agent/conversations?q=secreta").json()
        ids = [c["conversation_id"] for c in body["conversations"]]
        assert ids == []  # no leak even though the keyword exists across tenant boundary
        assert body["total"] == 0


# ---------------------------------------------------------------------------
# GET /agent/conversations/{id}/messages — transcript read
# ---------------------------------------------------------------------------


class TestGetMessagesIDOR:
    def test_404_when_probing_other_tenants_conversation(self, client):
        _seed(OTHER_USER_ID, "victim-id", "secret")
        resp = client.get("/agent/conversations/victim-id/messages")
        assert resp.status_code == 404
        # Same response as nonexistent (no existence leak)
        nonexistent = client.get("/agent/conversations/never-existed/messages")
        assert resp.json() == nonexistent.json()

    def test_own_conversation_visible_other_invisible(self, client):
        _seed(OTHER_USER_ID, "victim-id", "secret")
        _seed(99, "mine", "hola")

        ok = client.get("/agent/conversations/mine/messages")
        assert ok.status_code == 200
        assert ok.json()["conversation_id"] == "mine"

        forbidden = client.get("/agent/conversations/victim-id/messages")
        assert forbidden.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /agent/conversations/{id} — soft delete
# ---------------------------------------------------------------------------


class TestDeleteIDOR:
    def test_404_when_deleting_other_tenants_conversation(self, client):
        from db.transaction import transaction
        _seed(OTHER_USER_ID, "victim-id", "secret")

        resp = client.delete(
            "/agent/conversations/victim-id",
            headers={"X-API-Key": "test-key"},
        )
        assert resp.status_code == 404

        # Victim's row untouched (expires_at still in the future)
        with transaction() as con:
            row = con.execute(
                "SELECT expires_at, tenant_id FROM agent_conversation_meta "
                "WHERE conversation_id = 'victim-id'"
            ).fetchone()
        from datetime import datetime, timezone
        assert datetime.fromisoformat(row[0]) > datetime.now(timezone.utc)
        assert row[1] == OTHER_USER_ID

    def test_delete_does_not_affect_other_tenants_messages(self, client):
        """Even with the IDOR-blocked DELETE returning 404, the attacker
        could in theory have soft-deleted the victim's agent_messages
        rows. Verify they're untouched."""
        from db.transaction import transaction
        _seed(OTHER_USER_ID, "victim-id", "secret")

        client.delete(
            "/agent/conversations/victim-id",
            headers={"X-API-Key": "test-key"},
        )

        with transaction() as con:
            rows = con.execute(
                "SELECT COUNT(*) FROM agent_messages "
                "WHERE conversation_id = 'victim-id' AND tenant_id = ?",
                (OTHER_USER_ID,),
            ).fetchone()
        assert rows[0] == 2  # user + assistant both still present


# ---------------------------------------------------------------------------
# POST /agent/conversations/{id}/pin — toggle
# ---------------------------------------------------------------------------


class TestPinIDOR:
    def test_404_when_pinning_other_tenants_conversation(self, client):
        _seed(OTHER_USER_ID, "victim-id", "secret")
        resp = client.post(
            "/agent/conversations/victim-id/pin",
            headers={"X-API-Key": "test-key"},
        )
        assert resp.status_code == 404

    def test_pin_does_not_mutate_other_tenants_row(self, client):
        from db.transaction import transaction
        _seed(OTHER_USER_ID, "victim-id", "secret")

        client.post(
            "/agent/conversations/victim-id/pin",
            headers={"X-API-Key": "test-key"},
        )

        with transaction() as con:
            row = con.execute(
                "SELECT pinned FROM agent_conversation_meta "
                "WHERE conversation_id = 'victim-id'"
            ).fetchone()
        assert row[0] == 0  # untouched


# ---------------------------------------------------------------------------
# Tampering — query / body / header
# ---------------------------------------------------------------------------


class TestTamperingIgnored:
    def test_query_tenant_id_ignored_in_list(self, client):
        """`?tenant_id=999` must NOT override JWT-derived id."""
        _seed(OTHER_USER_ID, "v1", "..")

        body = client.get(f"/agent/conversations?tenant_id={OTHER_USER_ID}").json()
        assert body["conversations"] == []
        assert body["total"] == 0

    def test_header_x_tenant_id_ignored_in_list(self, client):
        _seed(OTHER_USER_ID, "v1", "..")
        body = client.get(
            "/agent/conversations",
            headers={"X-Tenant-Id": str(OTHER_USER_ID)},
        ).json()
        assert body["conversations"] == []

    def test_query_tenant_id_ignored_in_messages_get(self, client):
        _seed(OTHER_USER_ID, "v-id", "..")
        resp = client.get(
            f"/agent/conversations/v-id/messages?tenant_id={OTHER_USER_ID}"
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Same conversation_id across tenants (write-side collision was fixed in
# PR #433 review pickup; H.3 reads MUST also segregate)
# ---------------------------------------------------------------------------


class TestSameConversationIdAcrossTenants:
    """The H.2 write path refuses cross-tenant writes via record_history's
    pre-check. But what if pre-existing data (from before the security
    fix) had orphan agent_messages rows under a victim's conversation_id
    with a different tenant_id? The H.3 read path MUST still filter
    those out so the attacker can't see victim's transcript even if
    they know the conversation_id.
    """

    def test_messages_get_filters_by_tenant_id_even_with_orphans(self, client):
        from db.transaction import transaction
        # Victim creates the conversation legitimately
        _seed(OTHER_USER_ID, "shared-id", "secreto de la victima")

        # Simulate pre-fix orphan rows: attacker's tenant_id=99 rows
        # exist under the same conversation_id.
        with transaction() as con:
            con.execute(
                """INSERT INTO agent_messages
                   (tenant_id, conversation_id, ts, role, content, expires_at)
                   VALUES (?, ?, ?, 'user', ?, ?)""",
                (99, "shared-id", "2026-05-22T08:00:00Z",
                 "attacker injected", "2099-01-01T00:00:00Z"),
            )

        # Attacker (id=99) probing the conversation: meta row is owned by
        # OTHER_USER_ID, so the 404 fires before any message read can
        # happen.
        resp = client.get("/agent/conversations/shared-id/messages")
        assert resp.status_code == 404

    def test_list_does_not_surface_orphan_meta_for_other_tenant(self, client):
        """Victim's meta row + attacker's orphan agent_messages rows
        under the same conversation_id. Attacker's GET /conversations
        must NOT return the conversation even if their orphan messages
        match a q-filter."""
        from db.transaction import transaction
        _seed(OTHER_USER_ID, "shared-id", "asunto victima")
        with transaction() as con:
            con.execute(
                """INSERT INTO agent_messages
                   (tenant_id, conversation_id, ts, role, content, expires_at)
                   VALUES (?, ?, ?, 'user', ?, ?)""",
                (99, "shared-id", "2026-05-22T08:00:00Z",
                 "match-this-keyword", "2099-01-01T00:00:00Z"),
            )

        body = client.get("/agent/conversations?q=match-this-keyword").json()
        ids = [c["conversation_id"] for c in body["conversations"]]
        assert ids == []  # No row, even though attacker has a matching message
        assert body["total"] == 0
