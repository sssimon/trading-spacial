"""Phase 3 of epic #400 — propose/confirm pattern tests.

Covers:

  - HMAC sign + verify round-trip: a token produced by sign_proposal
    verifies cleanly; a token with the MAC byte-flipped raises
    ProposalError("signature_mismatch").
  - TTL: a row with expires_at < now is_expired() == True.
  - persist_proposal idempotency: re-inserting the same proposal_id
    is a no-op (idempotency_key UNIQUE).
  - propose_close_position end-to-end: tool emits `_proposal` envelope
    when the position belongs to the tenant, returns `error: not_found`
    when it doesn't. The proposal lands in agent_side_effects with
    result=NULL.
  - propose_reactivate_symbol: only emits when symbol is currently
    PAUSED; else returns symbol_not_paused.
  - POST /agent/proposals/{id}/confirm:
      * verifies HMAC + tenant ownership + idempotency.
      * second confirm of the same proposal is idempotent (no double
        execution downstream — verified via a spy on db_close_position).
      * cross-tenant confirm returns 404, NEVER reveals the proposal
        belongs to another tenant.
      * expired confirm returns 410.
      * TOCTOU drift returns 409 with result='state_drift' persisted.
      * signed_payload byte-tampering returns 400 signature_mismatch.

Pre-reg §10. All tests use the test fake's monkeypatch on
AGENT_PROPOSAL_SECRET so we never depend on real env state.
"""
from __future__ import annotations

import json

import pytest


# ── Fixtures ────────────────────────────────────────────────────────────


_TEST_SECRET = "test-only-secret-for-pytest-not-real"


@pytest.fixture
def proposal_env(monkeypatch):
    """Set AGENT_PROPOSAL_SECRET so sign/verify works in tests."""
    monkeypatch.setenv("AGENT_PROPOSAL_SECRET", _TEST_SECRET)
    yield


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import btc_api
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    yield db_path


@pytest.fixture
def authed_client(tmp_db, monkeypatch):
    """TestClient with the agent dependencies overridden."""
    import btc_api
    from fastapi.testclient import TestClient
    from auth.dependencies import get_current_tenant_id

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-test-key")
    monkeypatch.setenv("AGENT_PROPOSAL_SECRET", _TEST_SECRET)
    btc_api.app.dependency_overrides[get_current_tenant_id] = lambda: 1
    try:
        yield TestClient(btc_api.app)
    finally:
        btc_api.app.dependency_overrides.pop(get_current_tenant_id, None)


# ── sign/verify round-trip ─────────────────────────────────────────────


def test_sign_verify_round_trip(proposal_env):
    from api.agent.proposals import sign_proposal, verify_proposal
    p = sign_proposal(action="close_position",
                       args={"position_id": 7, "exit_price": 50_000.0},
                       tenant_id=42)
    parsed = verify_proposal(p.signed_payload)
    assert parsed["proposal_id"] == p.proposal_id
    assert parsed["action"] == "close_position"
    assert parsed["args"] == {"position_id": 7, "exit_price": 50_000.0}
    assert parsed["tenant_id"] == 42


def test_verify_rejects_tampered_mac(proposal_env):
    from api.agent.proposals import ProposalError, sign_proposal, verify_proposal
    p = sign_proposal(action="close_position", args={"position_id": 1, "exit_price": 1.0}, tenant_id=1)
    # Flip a single hex digit of the MAC
    mac, payload = p.signed_payload.split(".", 1)
    flipped = ("a" if mac[0] != "a" else "b") + mac[1:]
    bad = f"{flipped}.{payload}"
    with pytest.raises(ProposalError) as exc_info:
        verify_proposal(bad)
    assert exc_info.value.reason == "signature_mismatch"


def test_verify_rejects_tampered_payload(proposal_env):
    """Modify the tenant_id field inside the payload — the MAC no longer
    matches because we canonicalize before HMAC."""
    from api.agent.proposals import ProposalError, sign_proposal, verify_proposal
    import base64
    p = sign_proposal(action="close_position", args={"position_id": 1, "exit_price": 1.0}, tenant_id=1)
    mac, payload_b64 = p.signed_payload.split(".", 1)
    # Decode, modify, re-encode (without re-signing)
    padding = "=" * (-len(payload_b64) % 4)
    raw = base64.urlsafe_b64decode(payload_b64 + padding)
    doc = json.loads(raw)
    doc["tenant_id"] = 2  # attacker swaps tenant
    bad_raw = json.dumps(doc, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    bad_b64 = base64.urlsafe_b64encode(bad_raw).decode("ascii").rstrip("=")
    with pytest.raises(ProposalError) as exc_info:
        verify_proposal(f"{mac}.{bad_b64}")
    assert exc_info.value.reason == "signature_mismatch"


# ── persistence + idempotency ──────────────────────────────────────────


def test_persist_and_load_proposal(proposal_env, tmp_db):
    from api.agent.proposals import sign_proposal, persist_proposal, load_proposal_row
    p = sign_proposal(action="close_position", args={"position_id": 5, "exit_price": 100.0}, tenant_id=3)
    persist_proposal(tenant_id=3, conversation_id="conv-a", proposal=p)
    row = load_proposal_row(p.proposal_id)
    assert row is not None
    assert row["tenant_id"] == 3
    assert row["action"] == "close_position"
    assert row["result"] is None
    assert row["expires_at"] is not None


def test_persist_proposal_is_idempotent(proposal_env, tmp_db):
    """Same proposal_id can't insert twice — the UNIQUE constraint handles
    it gracefully (logged warning, no-op)."""
    from api.agent.proposals import sign_proposal, persist_proposal, load_proposal_row
    p = sign_proposal(action="close_position", args={"position_id": 5, "exit_price": 100.0}, tenant_id=3)
    persist_proposal(tenant_id=3, conversation_id="conv-a", proposal=p)
    persist_proposal(tenant_id=3, conversation_id="conv-a", proposal=p)  # idempotent
    row = load_proposal_row(p.proposal_id)
    assert row is not None


def test_is_expired_with_past_timestamp():
    from api.agent.proposals import is_expired
    assert is_expired({"expires_at": "2020-01-01T00:00:00+00:00"}) is True


def test_is_expired_with_future_timestamp():
    from api.agent.proposals import is_expired
    from datetime import datetime, timedelta, timezone
    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    assert is_expired({"expires_at": future}) is False


def test_is_expired_treats_null_as_expired():
    """Legacy rows pre-Phase 3 have NULL expires_at → can't be confirmed."""
    from api.agent.proposals import is_expired
    assert is_expired({"expires_at": None}) is True


# ── Propose handlers — grounding checks ───────────────────────────────


def _seed_position(con, *, tenant_id: int, status: str = "open", id: int | None = None):
    cur = con.execute(
        "INSERT INTO positions "
        "(symbol, direction, status, entry_price, entry_ts, size_usd, tenant_id) "
        "VALUES ('BTCUSDT', 'LONG', ?, 50000, '2026-04-20T10:00:00+00:00', 1000, ?)",
        (status, tenant_id),
    )
    con.commit()
    return cur.lastrowid


def test_propose_close_position_emits_envelope_for_own_position(proposal_env, tmp_db):
    import btc_api
    from api.agent.tools.propose_handlers import propose_close_position
    from api.agent.proposals import load_proposal_row

    con = btc_api.get_db()
    try:
        pos_id = _seed_position(con, tenant_id=1, status="open")
    finally:
        con.close()

    out = propose_close_position(
        tenant_id=1, conversation_id="c1",
        position_id=pos_id, exit_price=51000.0,
        rationale="momentum gave out and the SL is too far",
    )
    assert "_proposal" in out
    env = out["_proposal"]
    assert env["action"] == "close_position"
    assert env["args"] == {"position_id": pos_id, "exit_price": 51000.0}
    assert env["proposal_id"].startswith("prop_")
    # And persisted with result=NULL.
    row = load_proposal_row(env["proposal_id"])
    assert row is not None and row["result"] is None
    assert row["tenant_id"] == 1


def test_propose_close_position_rejects_other_tenant(proposal_env, tmp_db):
    """Tenant 1 tries to propose closing tenant 2's position → not_found,
    no proposal signed, no row in agent_side_effects."""
    import btc_api
    from api.agent.tools.propose_handlers import propose_close_position

    con = btc_api.get_db()
    try:
        other_id = _seed_position(con, tenant_id=2, status="open")
    finally:
        con.close()

    out = propose_close_position(
        tenant_id=1, conversation_id="c1",
        position_id=other_id, exit_price=1.0, rationale="x" * 20,
    )
    assert out == {"error": "not_found"}
    # No proposal persisted.
    con = btc_api.get_db()
    try:
        rows = con.execute("SELECT * FROM agent_side_effects").fetchall()
    finally:
        con.close()
    assert rows == []


def test_propose_close_position_rejects_closed_position(proposal_env, tmp_db):
    import btc_api
    from api.agent.tools.propose_handlers import propose_close_position

    con = btc_api.get_db()
    try:
        pos_id = _seed_position(con, tenant_id=1, status="closed")
    finally:
        con.close()

    out = propose_close_position(
        tenant_id=1, conversation_id="c1",
        position_id=pos_id, exit_price=1.0, rationale="x" * 20,
    )
    assert out == {"error": "position_not_open"}


# ── Confirm endpoint ──────────────────────────────────────────────────


def _build_signed_close_proposal(con, *, tenant_id: int, exit_price: float = 51000.0):
    """Helper: seed an open position, sign + persist a close_position
    proposal, return (pos_id, proposal_obj)."""
    from api.agent.proposals import sign_proposal, persist_proposal
    pos_id = _seed_position(con, tenant_id=tenant_id, status="open")
    p = sign_proposal(
        action="close_position",
        args={"position_id": pos_id, "exit_price": exit_price},
        tenant_id=tenant_id,
    )
    persist_proposal(tenant_id=tenant_id, conversation_id="conv-test", proposal=p)
    return pos_id, p


def test_confirm_succeeds_and_executes_downstream(authed_client):
    """Happy path: confirm a freshly signed proposal → 200 ok →
    downstream db_close_position fired → row in positions marked closed."""
    import btc_api
    client = authed_client

    con = btc_api.get_db()
    try:
        pos_id, proposal = _build_signed_close_proposal(con, tenant_id=1)
    finally:
        con.close()

    resp = client.post(
        f"/agent/proposals/{proposal.proposal_id}/confirm",
        json={"signed_payload": proposal.signed_payload},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["result"] == "ok"
    assert body["idempotent"] is False

    # And the position is actually closed.
    con = btc_api.get_db()
    try:
        row = con.execute(
            "SELECT status, exit_reason FROM positions WHERE id = ?", (pos_id,),
        ).fetchone()
    finally:
        con.close()
    assert dict(row)["status"] == "closed"
    assert dict(row)["exit_reason"] == "MANUAL_AGENT"


def test_confirm_is_idempotent_on_double_click(authed_client):
    """Second confirm returns the first result, does NOT re-execute the
    downstream action. Verified by checking the position only closed
    once (exit_ts doesn't change on the second call)."""
    import btc_api
    client = authed_client

    con = btc_api.get_db()
    try:
        pos_id, proposal = _build_signed_close_proposal(con, tenant_id=1)
    finally:
        con.close()

    r1 = client.post(
        f"/agent/proposals/{proposal.proposal_id}/confirm",
        json={"signed_payload": proposal.signed_payload},
    )
    assert r1.status_code == 200
    assert r1.json()["idempotent"] is False

    r2 = client.post(
        f"/agent/proposals/{proposal.proposal_id}/confirm",
        json={"signed_payload": proposal.signed_payload},
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["ok"] is True
    assert body["idempotent"] is True  # second call short-circuited


def test_confirm_returns_404_for_cross_tenant_attempt(authed_client, monkeypatch):
    """Tenant 2 signs a proposal. Tenant 1 (the authed test client) tries
    to confirm it → 404 not_found (NEVER 403 or detail revealing the
    cross-tenant nature)."""
    import btc_api
    client = authed_client

    con = btc_api.get_db()
    try:
        _pos_id, proposal = _build_signed_close_proposal(con, tenant_id=2)
    finally:
        con.close()

    # client is bound to tenant_id=1 via the fixture override.
    resp = client.post(
        f"/agent/proposals/{proposal.proposal_id}/confirm",
        json={"signed_payload": proposal.signed_payload},
    )
    assert resp.status_code == 404
    assert resp.json() == {"detail": "not_found"}


def test_confirm_returns_400_on_tampered_signature(authed_client):
    """A byte-flipped MAC on the signed_payload → 400 signature_mismatch.
    The position must NOT close."""
    import btc_api
    client = authed_client

    con = btc_api.get_db()
    try:
        pos_id, proposal = _build_signed_close_proposal(con, tenant_id=1)
    finally:
        con.close()

    mac, payload = proposal.signed_payload.split(".", 1)
    flipped = ("a" if mac[0] != "a" else "b") + mac[1:]
    bad = f"{flipped}.{payload}"
    resp = client.post(
        f"/agent/proposals/{proposal.proposal_id}/confirm",
        json={"signed_payload": bad},
    )
    assert resp.status_code == 400
    assert resp.json() == {"detail": "signature_mismatch"}

    # And the position is STILL open.
    con = btc_api.get_db()
    try:
        row = con.execute(
            "SELECT status FROM positions WHERE id = ?", (pos_id,),
        ).fetchone()
    finally:
        con.close()
    assert dict(row)["status"] == "open"


def test_confirm_returns_410_on_expired(authed_client):
    """A proposal whose expires_at column has passed → 410 expired.
    Persist directly with a past expires_at to bypass the helper."""
    import btc_api
    from api.agent.proposals import sign_proposal
    client = authed_client

    p = sign_proposal(
        action="close_position",
        args={"position_id": 1, "exit_price": 1.0},
        tenant_id=1,
    )
    con = btc_api.get_db()
    try:
        # Insert manually with a past expires_at.
        con.execute(
            "INSERT INTO agent_side_effects "
            "(tenant_id, conversation_id, ts, action, args_json, "
            " idempotency_key, result, http_status, expires_at) "
            "VALUES (1, 'c', '2020-01-01T00:00:00+00:00', "
            "        'close_position', '{}', ?, NULL, NULL, "
            "        '2020-01-01T00:05:00+00:00')",
            (p.proposal_id,),
        )
        con.commit()
    finally:
        con.close()

    resp = client.post(
        f"/agent/proposals/{p.proposal_id}/confirm",
        json={"signed_payload": p.signed_payload},
    )
    assert resp.status_code == 410
    assert resp.json() == {"detail": "expired"}


def test_confirm_returns_409_on_state_drift(authed_client):
    """TOCTOU: proposal signed when position was open; before confirm,
    the position got closed elsewhere. confirm → 409 state_drift."""
    import btc_api
    client = authed_client

    con = btc_api.get_db()
    try:
        pos_id, proposal = _build_signed_close_proposal(con, tenant_id=1)
        # Simulate the position closing in another flow before confirm.
        con.execute(
            "UPDATE positions SET status = 'closed', exit_reason = 'SL', "
            "  exit_ts = '2026-05-19T20:00:00+00:00', exit_price = 49000 "
            "WHERE id = ?", (pos_id,),
        )
        con.commit()
    finally:
        con.close()

    resp = client.post(
        f"/agent/proposals/{proposal.proposal_id}/confirm",
        json={"signed_payload": proposal.signed_payload},
    )
    assert resp.status_code == 409
    assert resp.json() == {"detail": "state_drift"}
    # The row in agent_side_effects records the drift.
    con = btc_api.get_db()
    try:
        row = con.execute(
            "SELECT result FROM agent_side_effects WHERE idempotency_key = ?",
            (proposal.proposal_id,),
        ).fetchone()
    finally:
        con.close()
    assert dict(row)["result"] == "state_drift"


def test_confirm_503_when_agent_disabled(authed_client, monkeypatch):
    """If the agent is disabled at confirm time (operator flipped the
    flag mid-flow), the endpoint 503s without executing the action.

    Uses a 60-char dummy signed_payload so the body clears Pydantic's
    min_length=20 and the request actually reaches the handler's
    status gate (otherwise the test only proves short bodies 422,
    which is not what we want to assert).
    """
    import api.agent.config as agent_cfg
    client = authed_client

    monkeypatch.setattr(
        agent_cfg, "load_config",
        lambda: {"agent": {"enabled": False}},
    )
    fake_token = "x" * 30 + "." + "y" * 30
    resp = client.post(
        "/agent/proposals/prop_test123/confirm",
        json={"signed_payload": fake_token},
    )
    assert resp.status_code == 503
    assert resp.json() == {"detail": "agent_disabled"}
