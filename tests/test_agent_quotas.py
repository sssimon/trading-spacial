"""Phase 5 of epic #400 — per-tenant spend quotas.

Covers:
  - First-tenant seed (no row → INSERT defaults on read)
  - Daily window reset on a fresh day (zero counter, advance window_start)
  - Monthly window reset on a fresh month (independent of daily)
  - check_quota_pretrun raises QuotaExceeded at the threshold
  - record_spend increments daily + monthly
  - record_spend swallows DB errors (audit-style fail-quiet)
  - Zero / negative cost_usd is a no-op (no row write, no exception)
  - get_snapshot does NOT raise on exceeded (admin can read past breach)
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone
from db.transaction import transaction


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import btc_api
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    yield db_path


# ── First-tenant seed ──────────────────────────────────────────────


def test_check_quota_pretrun_seeds_a_new_tenant(tmp_db):
    """No row in agent_quotas → INSERT with default cap, daily_used=0."""
    from api.agent.quotas import check_quota_pretrun, DEFAULT_DAILY_USD_CAP

    snap = check_quota_pretrun(tenant_id=42)
    assert snap.tenant_id == 42
    assert snap.daily_usd_used == 0.0
    assert snap.daily_usd_cap == DEFAULT_DAILY_USD_CAP
    assert snap.monthly_usd_used == 0.0


def test_check_quota_pretrun_idempotent_on_same_day(tmp_db):
    """Calling twice in a row reads the same row — does NOT re-seed."""
    from api.agent.quotas import check_quota_pretrun

    s1 = check_quota_pretrun(tenant_id=7)
    s2 = check_quota_pretrun(tenant_id=7)
    assert s1.daily_window_start == s2.daily_window_start


# ── Window resets ──────────────────────────────────────────────────


def test_daily_window_reset_on_a_new_day(tmp_db, monkeypatch):
    """Seed a tenant yesterday with usage > 0 → next day's check sees
    daily_usd_used=0 and the window_start advances to today."""
    import btc_api
    from api.agent import quotas

    # Seed manually with a yesterday window_start.
    yesterday = "2026-05-17"
    today = "2026-05-19"
    monkeypatch.setattr(quotas, "_now_utc",
                         lambda: datetime(2026, 5, 19, 12, tzinfo=timezone.utc))
    with transaction() as con:
        con.execute(
            """INSERT INTO agent_quotas
               (tenant_id, daily_usd_used, daily_usd_cap, daily_window_start,
                monthly_usd_used, monthly_window_start)
               VALUES (1, 0.75, 1.0, ?, 5.0, '2026-05-01')""",
            (yesterday,),
        )

    snap = quotas.check_quota_pretrun(tenant_id=1)
    assert snap.daily_usd_used == 0.0
    assert snap.daily_window_start == today
    # Monthly is untouched — same month.
    assert snap.monthly_usd_used == 5.0


def test_monthly_window_reset_on_a_new_month(tmp_db, monkeypatch):
    """First check in a new month → monthly_usd_used resets, daily too
    (since the day also changed)."""
    import btc_api
    from api.agent import quotas

    monkeypatch.setattr(quotas, "_now_utc",
                         lambda: datetime(2026, 6, 1, 8, tzinfo=timezone.utc))
    with transaction() as con:
        con.execute(
            """INSERT INTO agent_quotas
               (tenant_id, daily_usd_used, daily_usd_cap, daily_window_start,
                monthly_usd_used, monthly_window_start)
               VALUES (2, 0.90, 1.0, '2026-05-31', 19.50, '2026-05-01')""",
        )

    snap = quotas.check_quota_pretrun(tenant_id=2)
    assert snap.daily_usd_used == 0.0
    assert snap.monthly_usd_used == 0.0
    assert snap.monthly_window_start == "2026-06-01"


# ── Threshold ──────────────────────────────────────────────────────


def test_check_quota_pretrun_raises_when_daily_at_cap(tmp_db):
    """daily_usd_used >= daily_usd_cap → QuotaExceeded with exact figures
    on the exception."""
    import btc_api
    from api.agent.quotas import QuotaExceeded, check_quota_pretrun
    from api.agent import quotas as _quotas

    today = _quotas._today_iso()
    month = _quotas._this_month_iso()
    with transaction() as con:
        con.execute(
            """INSERT INTO agent_quotas
               (tenant_id, daily_usd_used, daily_usd_cap, daily_window_start,
                monthly_usd_used, monthly_window_start)
               VALUES (9, 1.00, 1.00, ?, 1.00, ?)""",
            (today, month),
        )

    with pytest.raises(QuotaExceeded) as exc:
        check_quota_pretrun(tenant_id=9)
    assert exc.value.daily_used == 1.00
    assert exc.value.daily_cap == 1.00


def test_check_quota_pretrun_does_not_raise_when_below_cap(tmp_db):
    """daily_usd_used < daily_usd_cap → returns snapshot normally."""
    import btc_api
    from api.agent.quotas import check_quota_pretrun
    from api.agent import quotas as _quotas

    today = _quotas._today_iso()
    month = _quotas._this_month_iso()
    with transaction() as con:
        con.execute(
            """INSERT INTO agent_quotas
               (tenant_id, daily_usd_used, daily_usd_cap, daily_window_start,
                monthly_usd_used, monthly_window_start)
               VALUES (3, 0.99, 1.00, ?, 0.99, ?)""",
            (today, month),
        )

    snap = check_quota_pretrun(tenant_id=3)
    assert snap.daily_usd_used == 0.99


# ── record_spend ──────────────────────────────────────────────────


def test_record_spend_increments_both_counters(tmp_db):
    from api.agent.quotas import record_spend, get_snapshot

    # Seed tenant via pretrun.
    from api.agent.quotas import check_quota_pretrun
    check_quota_pretrun(tenant_id=5)

    record_spend(tenant_id=5, cost_usd=0.12)
    record_spend(tenant_id=5, cost_usd=0.08)

    snap = get_snapshot(tenant_id=5)
    assert abs(snap.daily_usd_used - 0.20) < 1e-9
    assert abs(snap.monthly_usd_used - 0.20) < 1e-9


def test_record_spend_ignores_zero_and_negative(tmp_db):
    """Defensive: cost_usd=0 (fake/cheap-model paths) and negative
    (impossible but guard against) must not write a row."""
    from api.agent.quotas import record_spend, get_snapshot, check_quota_pretrun

    check_quota_pretrun(tenant_id=6)
    record_spend(tenant_id=6, cost_usd=0.0)
    record_spend(tenant_id=6, cost_usd=-1.0)

    snap = get_snapshot(tenant_id=6)
    assert snap.daily_usd_used == 0.0


def test_record_spend_swallows_db_errors(tmp_db, monkeypatch):
    """A DB-layer failure during charge must NOT raise — audit miss is
    annoying, but a raised exception turns a successful turn into a 500."""
    from api.agent import quotas

    def _boom(*a, **kw):
        raise RuntimeError("simulated db hiccup")

    monkeypatch.setattr(quotas, "_apply_spend", _boom)
    # Must not raise.
    quotas.record_spend(tenant_id=99, cost_usd=0.01)


def test_record_spend_for_unseen_tenant_inserts_row(tmp_db):
    """If record_spend fires before check_quota_pretrun ever ran for a
    tenant (rare: the pre-flight failed open on a DB hiccup but the turn
    completed), the INSERT path seeds + charges in one statement."""
    from api.agent.quotas import record_spend, get_snapshot

    record_spend(tenant_id=77, cost_usd=0.05)
    snap = get_snapshot(tenant_id=77)
    assert snap.tenant_id == 77
    assert abs(snap.daily_usd_used - 0.05) < 1e-9
    assert abs(snap.monthly_usd_used - 0.05) < 1e-9


# ── get_snapshot vs check_quota_pretrun ────────────────────────────


def test_get_snapshot_does_not_raise_on_exceeded(tmp_db):
    """The metrics endpoint reads via get_snapshot; it must see the
    breach state instead of being blocked by QuotaExceeded."""
    import btc_api
    from api.agent.quotas import get_snapshot
    from api.agent import quotas as _quotas

    today = _quotas._today_iso()
    month = _quotas._this_month_iso()
    with transaction() as con:
        con.execute(
            """INSERT INTO agent_quotas
               (tenant_id, daily_usd_used, daily_usd_cap, daily_window_start,
                monthly_usd_used, monthly_window_start)
               VALUES (50, 2.00, 1.00, ?, 2.00, ?)""",
            (today, month),
        )

    snap = get_snapshot(tenant_id=50)  # must NOT raise
    assert snap.daily_usd_used == 2.00
    assert snap.daily_usd_cap == 1.00  # capped at, breach shown
