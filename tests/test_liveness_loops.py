"""Tests de los loops de liveness (screener/sync). Spec §1. Los ciclos son
testeables; el loop respeta stop_event (no spin infinito)."""
import threading
from unittest.mock import patch

from scanner.runtime import _screener_cycle, _sync_cycle, screener_loop


def test_screener_cycle_invoca_regenerate():
    with patch("scanner.runtime._regenerate_screener") as gen:
        _screener_cycle()
    assert gen.call_count == 1


def test_screener_cycle_fail_soft():
    with patch("scanner.runtime._regenerate_screener", side_effect=RuntimeError("boom")):
        _screener_cycle()   # no debe lanzar


def test_sync_cycle_itera_tenants_active():
    with patch("scanner.runtime._active_tenants", return_value=[2, 4]), \
         patch("scanner.runtime._sync_one") as sync_one:
        _sync_cycle(threading.Event())
    assert {c.args[0] for c in sync_one.call_args_list} == {2, 4}


def test_sync_cycle_un_tenant_falla_no_tumba_el_resto():
    def _one(tid):
        if tid == 2:
            raise RuntimeError("rate ban")
    with patch("scanner.runtime._active_tenants", return_value=[2, 4]), \
         patch("scanner.runtime._sync_one", side_effect=_one) as sync_one:
        _sync_cycle(threading.Event())   # no lanza
    assert sync_one.call_count == 2


def test_loop_respeta_stop_event():
    ev = threading.Event(); ev.set()
    with patch("scanner.runtime._screener_cycle") as cyc:
        screener_loop(stop_event=ev)
    assert cyc.call_count == 0


def test_sync_loop_respeta_stop_event():
    from scanner.runtime import sync_loop
    ev = threading.Event(); ev.set()
    with patch("scanner.runtime._sync_cycle") as cyc:
        sync_loop(stop_event=ev)
    assert cyc.call_count == 0
