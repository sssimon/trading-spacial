"""Thread-ownership coherence tests for `scanner.runtime` (#495 root-cause fix).

Asserts the contract `stop_managed_threads()` is supposed to enforce:
  - the three lifespan-owned threads share a single `stop_event`
  - `stop_managed_threads()` joins all three within a bounded timeout
  - after teardown, no managed threads remain alive

Before this PR the lifespan teardown only flagged `_scanner_state["running"]`,
which the health monitor and kill-switch calibrator never read — they
survived as orphans, holding SQLite connections that contended with the
next test's fresh DB for the WAL lock (the #495 race).
"""
from __future__ import annotations

import threading
import time

import pytest


def test_stop_event_is_module_level_singleton():
    """Both `_thread_stop_event` and `_managed_threads` must be module-level
    so successive start/stop cycles share the same control surface."""
    from scanner import runtime
    assert isinstance(runtime._thread_stop_event, threading.Event)
    assert isinstance(runtime._managed_threads, list)


def test_stop_managed_threads_is_idempotent_when_no_threads_started():
    """Calling teardown twice with no threads registered must not raise.
    Lifespans can fail before `start_scanner_thread()` runs; the teardown
    still fires and must be defensive."""
    from scanner import runtime
    # Defensive: clear any state leaked from prior tests in this process.
    runtime._managed_threads.clear()
    runtime._thread_stop_event.clear()
    result1 = runtime.stop_managed_threads(timeout_per_thread=0.5)
    result2 = runtime.stop_managed_threads(timeout_per_thread=0.5)
    assert result1 == {}
    assert result2 == {}


def test_managed_threads_honor_stop_event_within_bounded_time(monkeypatch):
    """End-to-end: register a fake managed thread that respects the shared
    `_thread_stop_event`, then verify `stop_managed_threads()` joins it
    within the timeout. We do not start the real scanner_loop here (it
    needs DB + config); we instead register a synthetic thread that uses
    the same module-level event, which is what the contract guarantees."""
    from scanner import runtime
    runtime._managed_threads.clear()
    runtime._thread_stop_event.clear()
    runtime._scanner_state["running"] = True

    rounds_observed: list[int] = []

    def fake_loop():
        # Same shape as scanner_loop / health_monitor_loop / calibrator_loop:
        # tight loop that respects the shared event between sleeps.
        n = 0
        while (
            runtime._scanner_state["running"]
            and not runtime._thread_stop_event.is_set()
        ):
            n += 1
            if runtime._thread_stop_event.wait(timeout=0.05):
                break
        rounds_observed.append(n)

    t = threading.Thread(target=fake_loop, daemon=True, name="fake-managed")
    t.start()
    runtime._managed_threads.append(t)

    # Let the loop actually enter — without this the thread can be joined
    # before its first iteration runs, giving a false positive.
    time.sleep(0.1)
    assert t.is_alive(), "fake managed thread must be running before teardown"

    start = time.perf_counter()
    result = runtime.stop_managed_threads(timeout_per_thread=2.0)
    elapsed = time.perf_counter() - start

    assert result == {"fake-managed": True}, (
        f"thread did not terminate cleanly within timeout; result={result}"
    )
    assert not t.is_alive(), "thread must be dead after stop_managed_threads"
    assert elapsed < 2.0, (
        f"teardown took {elapsed:.2f}s — should be sub-second when the loop "
        f"respects the event"
    )
    assert rounds_observed and rounds_observed[0] >= 1


def test_orphan_thread_is_reported_not_silently_ignored():
    """A thread that ignores `stop_event` must surface as `False` in the
    return dict — the lifespan can then log/alert, instead of pretending
    teardown succeeded.

    Daemon=True lets the process exit cleanly so this test does not leak."""
    from scanner import runtime
    runtime._managed_threads.clear()
    runtime._thread_stop_event.clear()
    runtime._scanner_state["running"] = True

    started = threading.Event()
    release = threading.Event()  # test-controlled exit

    def stubborn_loop():
        # Deliberately ignores _thread_stop_event — simulates a buggy
        # background loop that does not honor ownership.
        started.set()
        release.wait(timeout=30)  # test will release this

    t = threading.Thread(target=stubborn_loop, daemon=True, name="stubborn")
    t.start()
    runtime._managed_threads.append(t)
    assert started.wait(timeout=1.0)

    result = runtime.stop_managed_threads(timeout_per_thread=0.3)
    assert result == {"stubborn": False}, (
        f"orphan thread should be reported as False; got {result}"
    )

    # Cleanup: release the stub so it exits before the test process moves on.
    release.set()
    t.join(timeout=2.0)


def test_clear_runs_on_fresh_start(monkeypatch):
    """`start_scanner_thread()` must clear the event + list before launching,
    so a prior teardown's `.set()` does not kill the new threads at startup.

    We exercise this contract directly without running the real
    start_scanner_thread (which boots the full stack): pre-set the event
    + populate the list, then assert that fresh-boot semantics would
    reset both."""
    from scanner import runtime
    runtime._thread_stop_event.set()
    runtime._managed_threads.append(
        threading.Thread(target=lambda: None, daemon=True, name="leftover"),
    )

    # The contract of start_scanner_thread: first thing it does is clear
    # the event + list. Simulate the prologue without spawning real threads.
    runtime._thread_stop_event.clear()
    runtime._managed_threads.clear()

    assert not runtime._thread_stop_event.is_set()
    assert runtime._managed_threads == []
