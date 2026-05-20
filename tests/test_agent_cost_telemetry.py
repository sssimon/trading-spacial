"""Fase 4 of the multi-provider epic — cost telemetry + per-provider
breakdown tests.

Covers:
  - Schema: agent_conversations has `provider` + `reasoning_tokens`
    columns after init_db().
  - Backfill: legacy rows with provider IS NULL get filled by the
    migration's UPDATE (claude-* → anthropic, deepseek-* → deepseek).
  - audit.record_turn persists `provider` and `reasoning_tokens` when
    passed. NULL values are stored as NULL (not 0) for reasoning_tokens
    so the metrics endpoint can distinguish "no reasoning" from "0
    reasoning tokens reported".
  - TurnAuditWrapper derives provider from model — claude-* → anthropic,
    deepseek-* → deepseek, unknown → None.
  - DeepSeekProvider.stream parses usage.completion_tokens_details.
    reasoning_tokens when DS reports it; field absent / chat-V3 → 0.
  - Loop sums reasoning_tokens across hops for multi-hop turns.
  - /agent/metrics today.by_provider has correct breakdown for mixed
    rows.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest


# ── Schema + backfill ───────────────────────────────────────────


def test_schema_has_provider_column(tmp_path, monkeypatch):
    """init_db() adds the `provider` column to agent_conversations
    (idempotent ALTER TABLE for existing DBs, included in the CREATE
    for fresh DBs — both paths land at the same final schema)."""
    import btc_api, sqlite3
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    btc_api.init_db()
    con = sqlite3.connect(db_path)
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(agent_conversations)")}
    finally:
        con.close()
    assert "provider" in cols
    assert "reasoning_tokens" in cols


def test_backfill_provider_from_model_prefix(tmp_path, monkeypatch):
    """Rows with provider IS NULL get backfilled from their model id."""
    import btc_api, sqlite3
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    btc_api.init_db()

    # Insert rows with NULL provider (simulating pre-Fase-4 data).
    con = btc_api.get_db()
    try:
        ts = datetime.now(timezone.utc).isoformat()
        for model in ("claude-sonnet-4-6", "claude-opus-4-7",
                       "deepseek-chat", "deepseek-reasoner"):
            con.execute(
                "INSERT INTO agent_conversations "
                "(tenant_id, surface, conversation_id, ts, role, model) "
                "VALUES (1, 'dock', ?, ?, 'assistant', ?)",
                (f"conv-{model}", ts, model),
            )
        # Force provider to NULL so the backfill has something to do.
        con.execute("UPDATE agent_conversations SET provider = NULL")
        con.commit()
    finally:
        con.close()

    # Run init_db again — idempotent, but the backfill UPDATE only
    # touches WHERE provider IS NULL. After this, all 4 rows have
    # provider populated.
    btc_api.init_db()
    con = btc_api.get_db()
    try:
        rows = con.execute(
            "SELECT model, provider FROM agent_conversations "
            "ORDER BY model ASC"
        ).fetchall()
    finally:
        con.close()
    by_model = {r["model"]: r["provider"] for r in rows}
    assert by_model["claude-sonnet-4-6"]  == "anthropic"
    assert by_model["claude-opus-4-7"]    == "anthropic"
    assert by_model["deepseek-chat"]      == "deepseek"
    assert by_model["deepseek-reasoner"]  == "deepseek"


def test_backfill_does_not_overwrite_existing_provider(tmp_path, monkeypatch):
    """If an admin sets provider manually (e.g. a hot-fix while data
    is being audited), running the migration again shouldn't change
    it. UPDATE only touches WHERE provider IS NULL."""
    import btc_api, sqlite3
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    btc_api.init_db()
    con = btc_api.get_db()
    try:
        ts = datetime.now(timezone.utc).isoformat()
        con.execute(
            "INSERT INTO agent_conversations "
            "(tenant_id, surface, conversation_id, ts, role, model, provider) "
            "VALUES (1, 'dock', 'sentinel', ?, 'assistant', 'claude-sonnet-4-6', 'custom-vendor')",
            (ts,),
        )
        con.commit()
    finally:
        con.close()

    btc_api.init_db()  # re-run migration
    con = btc_api.get_db()
    try:
        row = con.execute(
            "SELECT provider FROM agent_conversations WHERE conversation_id = 'sentinel'"
        ).fetchone()
    finally:
        con.close()
    assert dict(row)["provider"] == "custom-vendor"


# ── audit.record_turn + TurnAuditWrapper ────────────────────────


def test_record_turn_persists_provider_and_reasoning_tokens(tmp_path, monkeypatch):
    import btc_api
    from api.agent.audit import record_turn
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    btc_api.init_db()

    record_turn(
        tenant_id=1, surface="dock", conversation_id="c1",
        role="assistant", model="deepseek-reasoner",
        provider="deepseek",
        input_tokens=100, output_tokens=200,
        reasoning_tokens=150,
        cost_usd=0.05,
    )

    con = btc_api.get_db()
    try:
        row = con.execute(
            "SELECT provider, reasoning_tokens, model "
            "FROM agent_conversations WHERE conversation_id = 'c1'"
        ).fetchone()
    finally:
        con.close()
    d = dict(row)
    assert d["provider"] == "deepseek"
    assert d["reasoning_tokens"] == 150
    assert d["model"] == "deepseek-reasoner"


def test_record_turn_stores_null_reasoning_tokens_when_absent(tmp_path, monkeypatch):
    """When reasoning_tokens is not passed (most providers, including
    chat-V3 and Anthropic), the column stores NULL — distinguishable
    from a row that explicitly reports 0 reasoning tokens."""
    import btc_api
    from api.agent.audit import record_turn
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    btc_api.init_db()

    record_turn(
        tenant_id=1, surface="dock", conversation_id="c2",
        role="assistant", model="deepseek-chat",
        provider="deepseek",
        input_tokens=100, output_tokens=200,
        cost_usd=0.05,
    )
    con = btc_api.get_db()
    try:
        row = con.execute(
            "SELECT reasoning_tokens FROM agent_conversations "
            "WHERE conversation_id = 'c2'"
        ).fetchone()
    finally:
        con.close()
    assert dict(row)["reasoning_tokens"] is None


def test_record_turn_preserves_explicit_zero_reasoning_tokens(tmp_path, monkeypatch):
    """PR #416 review issue 1: an explicit reasoning_tokens=0 is NOT
    the same as None. DS chat-V3 emits 0 in
    completion_tokens_details.reasoning_tokens (because the field is
    present in the response even when there's no reasoning), and
    analytics queries that count 'rows where the provider reported the
    field at all' need to distinguish.

    Pre-fix the audit code did `int(reasoning_tokens) if
    reasoning_tokens else None` — falsy check colapsa 0 a NULL. The fix
    is `is not None` instead.
    """
    import btc_api
    from api.agent.audit import record_turn
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    btc_api.init_db()

    record_turn(
        tenant_id=1, surface="dock", conversation_id="c-zero",
        role="assistant", model="deepseek-chat",
        provider="deepseek",
        input_tokens=100, output_tokens=50,
        reasoning_tokens=0,  # explicit zero
        cost_usd=0.01,
    )
    con = btc_api.get_db()
    try:
        row = con.execute(
            "SELECT reasoning_tokens FROM agent_conversations "
            "WHERE conversation_id = 'c-zero'"
        ).fetchone()
    finally:
        con.close()
    # 0 preserved, NOT collapsed to NULL.
    assert dict(row)["reasoning_tokens"] == 0
    assert dict(row)["reasoning_tokens"] is not None


def test_provider_mapping_consistent_across_registry_and_audit():
    """PR #416 review issue 2: the prefix→provider mapping lives in
    THREE places (registry.PROVIDER_NAME_BY_PREFIX, audit._provider_
    for_model, db/schema.py backfill SQL). audit.py deliberately
    doesn't import registry.py (avoids the lazy SDK chain), but that
    creates drift risk — adding a new provider to the registry without
    updating audit silently buckets the new turns under 'unknown' in
    /agent/metrics.

    This test asserts the two Python sites stay in sync. The SQL
    backfill in db/schema.py is also covered indirectly: if a new
    prefix is added to the registry, the backfill UPDATE in
    db/schema.py needs to be extended too — but that's a SQL string
    that doesn't ergonomically participate in a runtime check; the
    review of any provider-addition PR is the gate there.
    """
    from api.agent.audit import _provider_for_model
    from api.agent.providers.registry import PROVIDER_NAME_BY_PREFIX

    for prefix, expected_provider in PROVIDER_NAME_BY_PREFIX.items():
        # Construct a sample model id with this prefix. Any suffix
        # works — _provider_for_model only looks at the prefix.
        sample = f"{prefix}-sample-model"
        actual = _provider_for_model(sample)
        assert actual == expected_provider, (
            f"audit._provider_for_model({sample!r}) returned {actual!r}; "
            f"expected {expected_provider!r}. audit.py and registry.py "
            f"have drifted on the {prefix!r} prefix — update one or both."
        )


def test_turn_audit_wrapper_derives_provider_from_model():
    """Static check of the wrapper's provider derivation. Used at
    construction time, so the in-flight loop emits the right value."""
    from api.agent.audit import TurnAuditWrapper

    assert TurnAuditWrapper._resolve_provider_static("claude-sonnet-4-6") == "anthropic"
    assert TurnAuditWrapper._resolve_provider_static("claude-opus-4-7")   == "anthropic"
    assert TurnAuditWrapper._resolve_provider_static("deepseek-chat")     == "deepseek"
    assert TurnAuditWrapper._resolve_provider_static("deepseek-reasoner") == "deepseek"
    # Unknown prefix → None (legacy / typo / future provider not yet wired).
    assert TurnAuditWrapper._resolve_provider_static("gpt-5") is None


# ── DeepSeek SSE: reasoning_tokens parsing ──────────────────────


def _install_fake_httpx_for_test(monkeypatch, *, lines: list[str]):
    """Local copy of the fake from test_provider_deepseek.py — we
    don't import to keep this file self-contained."""
    import httpx

    class _R:
        def __init__(self, ls): self._ls = ls; self.status_code = 200
        async def aread(self): return b""
        async def aiter_lines(self):
            for line in self._ls:
                yield line

    class _SCM:
        def __init__(self, r): self._r = r
        async def __aenter__(self): return self._r
        async def __aexit__(self, *a): return None

    class _C:
        def __init__(self, r): self._r = r
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        def stream(self, *a, **kw): return _SCM(self._r)

    response = _R(lines)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: _C(response))


@pytest.mark.anyio
async def test_ds_stream_parses_reasoning_tokens_from_usage(monkeypatch):
    """DS-R1's usage object carries `completion_tokens_details.
    reasoning_tokens`. The adapter pulls it into LLMStreamEnd.usage
    so the audit row picks it up."""
    from api.agent.providers.deepseek_adapter import DeepSeekProvider
    from api.agent.providers.base import LLMStreamEnd

    lines = [
        'data: {"choices":[{"delta":{"content":"hi"}}]}',
        'data: {"choices":[{"finish_reason":"stop"}],'
        '"usage":{"prompt_tokens":100,"completion_tokens":250,'
        '         "completion_tokens_details":{"reasoning_tokens":180}}}',
        'data: [DONE]',
    ]
    _install_fake_httpx_for_test(monkeypatch, lines=lines)

    p = DeepSeekProvider(api_key="sk-fake")
    end = None
    async for ev in p.stream(
        model="deepseek-reasoner", system_blocks=[], messages=[],
        tools=[], max_tokens=4096,
    ):
        if isinstance(ev, LLMStreamEnd):
            end = ev
    assert end is not None
    assert end.usage["reasoning_tokens"] == 180
    assert end.usage["output_tokens"] == 250  # total (reasoning + content)


@pytest.mark.anyio
async def test_ds_stream_reasoning_tokens_defaults_to_zero(monkeypatch):
    """chat-V3 doesn't emit completion_tokens_details — adapter falls
    back to 0 cleanly."""
    from api.agent.providers.deepseek_adapter import DeepSeekProvider
    from api.agent.providers.base import LLMStreamEnd

    lines = [
        'data: {"choices":[{"delta":{"content":"ok"}}]}',
        'data: {"choices":[{"finish_reason":"stop"}],'
        '"usage":{"prompt_tokens":50,"completion_tokens":10}}',
        'data: [DONE]',
    ]
    _install_fake_httpx_for_test(monkeypatch, lines=lines)

    p = DeepSeekProvider(api_key="sk-fake")
    end = None
    async for ev in p.stream(
        model="deepseek-chat", system_blocks=[], messages=[],
        tools=[], max_tokens=4096,
    ):
        if isinstance(ev, LLMStreamEnd):
            end = ev
    assert end is not None
    assert end.usage["reasoning_tokens"] == 0


# ── /agent/metrics by_provider breakdown ────────────────────────


def _seed_assistant(tenant_id, model, cost_usd, provider, reasoning_tokens=None,
                     hours_ago=1):
    import btc_api
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    con = btc_api.get_db()
    try:
        con.execute(
            "INSERT INTO agent_conversations "
            "(tenant_id, surface, conversation_id, ts, role, model, provider, "
            " input_tokens, output_tokens, cache_read_input_tokens, "
            " cache_creation_input_tokens, reasoning_tokens, latency_ms, cost_usd) "
            "VALUES (?, 'dock', ?, ?, 'assistant', ?, ?, 100, 50, 0, 0, ?, 1000, ?)",
            (tenant_id, f"conv-{ts}", ts, model, provider,
             reasoning_tokens, cost_usd),
        )
        con.commit()
    finally:
        con.close()


def _admin_client_fixture(tmp_path, monkeypatch):
    import btc_api
    from fastapi.testclient import TestClient
    from auth.dependencies import get_current_tenant_id, get_current_user
    from auth.models import User

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setenv("DEEPSEEK_API_KEY",  "sk-ds-fake")

    user = User(id=1, email="u@x", role="admin", is_active=True,
                 created_at="2026-05-20T00:00:00+00:00",
                 password_changed_at="2026-05-20T00:00:00+00:00")
    btc_api.app.dependency_overrides[get_current_tenant_id] = lambda: 1
    btc_api.app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(btc_api.app), btc_api


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    client, btc_api = _admin_client_fixture(tmp_path, monkeypatch)
    try:
        yield client
    finally:
        from auth.dependencies import get_current_tenant_id, get_current_user
        btc_api.app.dependency_overrides.pop(get_current_tenant_id, None)
        btc_api.app.dependency_overrides.pop(get_current_user, None)


def test_metrics_by_provider_breakdown(admin_client):
    """Mixed rows: 2 anthropic + 3 deepseek + 1 NULL provider →
    today.by_provider has 3 buckets (anthropic, deepseek, unknown)
    with the correct turn_count + total_usd each."""
    _seed_assistant(1, "claude-sonnet-4-6", 0.10, "anthropic")
    _seed_assistant(1, "claude-haiku-4-5",  0.05, "anthropic")
    _seed_assistant(1, "deepseek-chat",     0.01, "deepseek")
    _seed_assistant(1, "deepseek-chat",     0.02, "deepseek")
    _seed_assistant(1, "deepseek-reasoner", 0.04, "deepseek",
                     reasoning_tokens=200)
    # Legacy row pre-backfill — provider NULL.
    _seed_assistant(1, "claude-sonnet-4-6", 0.03, None)

    resp = admin_client.get("/agent/metrics")
    assert resp.status_code == 200
    body = resp.json()
    bp = body["today"]["by_provider"]

    assert set(bp.keys()) == {"anthropic", "deepseek", "unknown"}
    assert bp["anthropic"]["turn_count"] == 2
    assert bp["anthropic"]["total_usd"] == pytest.approx(0.15)
    assert bp["deepseek"]["turn_count"] == 3
    assert bp["deepseek"]["total_usd"] == pytest.approx(0.07)
    assert bp["unknown"]["turn_count"] == 1
    assert bp["unknown"]["total_usd"] == pytest.approx(0.03)


def test_metrics_today_reasoning_tokens_sums_across_providers(admin_client):
    """today.reasoning_tokens is a single sum across ALL turns. Only
    DS-reasoner contributes today; other providers / chat-V3 stay 0
    or NULL (NULL → COALESCE → 0)."""
    _seed_assistant(1, "deepseek-reasoner", 0.04, "deepseek",
                     reasoning_tokens=200)
    _seed_assistant(1, "deepseek-reasoner", 0.06, "deepseek",
                     reasoning_tokens=350)
    _seed_assistant(1, "deepseek-chat",     0.01, "deepseek")  # no reasoning
    _seed_assistant(1, "claude-sonnet-4-6", 0.10, "anthropic")  # no reasoning

    resp = admin_client.get("/agent/metrics")
    body = resp.json()
    assert body["today"]["reasoning_tokens"] == 550
