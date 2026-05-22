"""GET /agent/status — no-leak contract + state precedence.

Phase 0 of the production-grade copilot rewrite (epic #400). The status
endpoint is the single source of truth the frontend reads to decide
whether to render the copilot UI. Its body MUST NOT leak env-var names,
.env paths, or any operator-only configuration detail — those strings
would let an unauthenticated visitor map the server's deployment.
"""
from __future__ import annotations

import pytest


# Strings that must NEVER appear in the response body of /agent/status
# or any other agent-related public surface. Lifted verbatim from
# pre-reg §11.7.
_FORBIDDEN_LEAK_STRINGS = (
    "ANTHROPIC_API_KEY",
    ".env",
    "/.env",
    "restart",
    "configure",
    "config.json",
    "Set it in",
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Same DB-isolation pattern as tests/test_health_dashboard.py."""
    import btc_api
    from fastapi.testclient import TestClient
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    return TestClient(btc_api.app)


# ── /agent/status: enabled when ANTHROPIC_API_KEY is set ────────────────


def test_agent_status_enabled_when_api_key_set(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-test-key")
    resp = client.get("/agent/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"enabled": True, "reason": "ok"}


# ── /agent/status: disabled when the default provider's API key is missing ─
#
# Fase 3b of the multi-provider epic: defaults migrated to DeepSeek,
# so the §2.7 status check reads DEEPSEEK_API_KEY (the dock default's
# provider). These tests delete DEEPSEEK_API_KEY instead of the
# Anthropic one. If a future PR flips defaults back, update these.


def test_agent_status_disabled_when_default_provider_key_missing(client, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    resp = client.get("/agent/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"enabled": False, "reason": "agent_disabled"}


def test_agent_status_disabled_when_default_provider_key_empty(client, monkeypatch):
    """Empty string is treated identically to missing — see
    DeepSeekProvider.has_api_key reads via os.environ.get(...).strip()."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    resp = client.get("/agent/status")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False, "reason": "agent_disabled"}


def test_agent_status_disabled_when_default_provider_key_whitespace(client, monkeypatch):
    """Whitespace-only is treated as missing — operator typo guard."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "   ")
    resp = client.get("/agent/status")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False, "reason": "agent_disabled"}


# ── /agent/status: disabled when cfg.agent.enabled is False ─────────────


def test_agent_status_disabled_when_cfg_disabled(client, monkeypatch):
    """Operator-driven disable wins even if ANTHROPIC_API_KEY is set.

    The router resolves load_config via the local reference imported in
    api/agent/config.py (`from api.config import load_config`). That's the
    only patch point that affects the flow; patching btc_api.load_config
    would be a no-op for this code path.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-test-key")
    import api.agent.config as agent_cfg
    monkeypatch.setattr(
        agent_cfg, "load_config",
        lambda: {"agent": {"enabled": False}},
    )
    resp = client.get("/agent/status")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False, "reason": "agent_disabled"}


# ── No-leak contract (the load-bearing test for #381) ───────────────────


def test_agent_status_body_never_leaks_env_var_names_or_paths(client, monkeypatch):
    """The response body MUST NOT contain any of the forbidden leak strings
    regardless of the state. This is the regression test for #381."""
    for key_value in (None, "", "sk-ant-real-key"):
        if key_value is None:
            monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        else:
            monkeypatch.setenv("ANTHROPIC_API_KEY", key_value)
        resp = client.get("/agent/status")
        body_text = resp.text
        for forbidden in _FORBIDDEN_LEAK_STRINGS:
            assert forbidden not in body_text, (
                f"/agent/status leaked forbidden string {forbidden!r} "
                f"with ANTHROPIC_API_KEY={key_value!r}. Body: {body_text!r}"
            )


def test_legacy_agent_chat_endpoint_is_removed(client):
    """Pre-reg §3.3 line 601: the legacy POST /agent/chat endpoint was
    eliminated after epic #400 Phase 2B (SSE streaming via
    /agent/conversations/{id}/turn). This test pins the removal so it
    can't silently come back via a stray import or copy-paste."""
    resp = client.post(
        "/agent/chat",
        json={"system": "test", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 404, (
        f"POST /agent/chat returned {resp.status_code} ({resp.text!r}). "
        f"The legacy endpoint must stay removed — see #381."
    )


# ── Closed-enum invariant ───────────────────────────────────────────────


def test_agent_status_reason_field_is_closed_enum(client, monkeypatch):
    """`reason` must be one of the documented values. Anything else is a
    regression — likely an operator-only string sneaking through."""
    allowed = {"ok", "agent_disabled"}
    for key_value in (None, "", "sk-ant-real-key"):
        if key_value is None:
            monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        else:
            monkeypatch.setenv("ANTHROPIC_API_KEY", key_value)
        resp = client.get("/agent/status")
        reason = resp.json()["reason"]
        assert reason in allowed, (
            f"Unknown agent status reason {reason!r} — closed enum is {allowed}"
        )


# ── Unauthenticated access ──────────────────────────────────────────────


def test_agent_status_does_not_require_auth(client, monkeypatch):
    """The frontend reads /agent/status before login completes in some
    flows; the endpoint must respond without an auth cookie.

    This test DISABLES the conftest test bypass (AUTH_TEST_BYPASS_ROLE)
    so the AuthMiddleware actually runs as it would in production.
    Without this, the test would pass even if /agent/status was NOT in
    the public-path whitelist — the bypass would let any path through.

    Bug history: pre-2026-05-20 fix, /agent/status was NOT in
    _PUBLIC_PATHS_EXACT. Tests passed via the bypass; papá's prod
    smoke during Fase 5 rollout caught the 401 with curl. Test now
    forces the middleware to exercise the whitelist for real.
    """
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds-fake")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    # Kill the conftest bypass so AuthMiddleware enforces the whitelist.
    monkeypatch.delenv("AUTH_TEST_BYPASS_ROLE", raising=False)
    # No login, no cookies — should still succeed via the whitelist.
    resp = client.get("/agent/status")
    assert resp.status_code == 200, (
        f"GET /agent/status returned {resp.status_code} ({resp.text!r}). "
        f"This usually means /agent/status is missing from "
        f"auth/middleware.py:_PUBLIC_PATHS_EXACT. The endpoint MUST be "
        f"public — the frontend hits it before the login flow resolves."
    )


def test_protected_paths_still_require_auth(client, monkeypatch):
    """Negative guard — the whitelist must stay restrictive.

    Defends against the failure mode where someone copy-pastes the
    /agent/status whitelist entry and accidentally adds a path that
    should NOT be public (e.g. /positions, /admin/*, /health/symbols).

    Same bypass-disable pattern as the test above so the middleware
    actually runs. Picks /positions as the canary because it returns
    tenant-scoped data and must never be reachable without a JWT.
    """
    monkeypatch.delenv("AUTH_TEST_BYPASS_ROLE", raising=False)
    resp = client.get("/positions")
    assert resp.status_code == 401, (
        f"GET /positions returned {resp.status_code} ({resp.text!r}) "
        f"without an auth cookie. /positions is tenant-scoped and MUST "
        f"require auth. Did somebody accidentally add it (or a prefix "
        f"that covers it) to auth/middleware.py:_PUBLIC_PATHS_EXACT?"
    )
