"""Defense-in-depth: the lifespan refuses to boot if pytest leaks into
a production runtime (RUN_AS_SERVICE=1 + 'pytest' in sys.modules).

This complements the auth middleware's bypass triple-guard
(auth/middleware.py:_bypass_role_or_none). The middleware gates the
*request* path; this gate stops the *service* from coming up at all.

Filed after the #428 H.5 smoke surfaced that the bypass is only
defended by env-var + middleware check, with no startup assertion.
"""
from __future__ import annotations

import sys

import pytest
from fastapi.testclient import TestClient


def test_lifespan_refuses_to_boot_when_pytest_leaks(monkeypatch):
    """Both signals coincide → boot crashes. The `with TestClient(app)`
    block drives the lifespan; without the env var the same test would
    pass (covered by test_setup.py)."""
    monkeypatch.setenv("RUN_AS_SERVICE", "1")
    assert "pytest" in sys.modules  # sanity: we ARE under pytest

    import btc_api
    with pytest.raises(RuntimeError, match="pytest leaked"):
        with TestClient(btc_api.app):
            pass  # never reaches here — lifespan raised before yield


def test_lifespan_does_not_crash_without_run_as_service_env(monkeypatch):
    """Negative control: the gate is silent when RUN_AS_SERVICE is unset.
    This is the path test_setup.py + every other `with TestClient`
    test relies on — confirms we didn't break them."""
    monkeypatch.delenv("RUN_AS_SERVICE", raising=False)
    assert "pytest" in sys.modules

    import btc_api
    # Smoke: lifespan should enter and exit cleanly. test_setup.py
    # covers the full lifespan happy path; here we just confirm the
    # new gate doesn't fire.
    with TestClient(btc_api.app):
        pass  # if this raises, the gate is over-eager — must NOT happen


def test_main_entry_sets_run_as_service():
    """`if __name__ == '__main__'` block contains
    `os.environ.setdefault("RUN_AS_SERVICE", "1")` before the uvicorn.run
    call. We can't easily exec the __main__ block (it would launch
    uvicorn and block); instead, assert the source-level invariant
    by reading the file and pattern-matching. Brittle but pins the
    guarantee that direct `python btc_api.py` invocations arm the gate.
    """
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "btc_api.py"
    text = src.read_text(encoding="utf-8")
    assert 'os.environ.setdefault("RUN_AS_SERVICE", "1")' in text, (
        "btc_api.py's __main__ block must set RUN_AS_SERVICE=1 so direct "
        "`python btc_api.py` invocations arm the lifespan gate."
    )
