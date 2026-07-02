import threading
import time
import scanner.runtime as rt


def test_baseline_loop_ticks_and_persists(tmp_path, monkeypatch):
    # universo pequeño + barras fake => sin red
    uni = [f"S{i}" for i in range(40)]
    bars = {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}
    monkeypatch.setattr(rt, "_baseline_universe", lambda: uni)
    monkeypatch.setattr(rt, "_baseline_bar", lambda sym: dict(bars))
    monkeypatch.setattr(rt, "_baseline_today", lambda: "2026-07-02")
    path = str(tmp_path / "state.json")
    monkeypatch.setattr(rt, "_BASELINE_PATH", path)

    ev = threading.Event()
    t = threading.Thread(target=rt.baseline_loop, kwargs={"stop_event": ev}, daemon=True)
    t.start()
    time.sleep(0.5)   # deja correr un ciclo
    ev.set()
    t.join(timeout=3)

    from scanner.baseline.store import load
    ensemble, gen = load(path=path)
    assert ensemble is not None and ensemble.last_date == "2026-07-02"
    assert gen is not None  # generated_at presente => la frescura será 'fresco'


def test_baseline_thread_registered_in_managed():
    # start_scanner_thread debe registrar el baseline_thread para el teardown (#8)
    import inspect
    src = inspect.getsource(rt.start_scanner_thread)
    assert "baseline_loop" in src
    assert "baseline_thread" in src
