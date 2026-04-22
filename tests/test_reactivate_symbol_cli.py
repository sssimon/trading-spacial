"""CLI script scripts/reactivate_symbol.py: end-to-end round-trip."""
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "reactivate_symbol.py"


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Point DB_FILE at a tmp path AND propagate via env var so the subprocess sees it.

    btc_api.DB_FILE is set at module load from SCRIPT_DIR — since the CLI is a
    subprocess, we monkeypatch via a custom path imported by btc_api. For this
    test we just run the CLI in a subprocess with cwd=ROOT and let it use a
    tmp-path signals.db via a PYTHONPATH trick: we seed the tmp DB via direct
    API calls first, then invoke the CLI."""
    import btc_api
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    yield db_path


def test_cli_imports_and_runs(tmp_db):
    """Direct-import invocation (no subprocess) — validates happy-path flow."""
    import importlib
    from health import apply_transition, get_symbol_state

    metrics = {"trades_count_total": 50, "win_rate_20_trades": 0.5,
                "pnl_30d": 0.0, "pnl_by_month": {},
                "months_negative_consecutive": 3}
    apply_transition("BTC", "PAUSED", "3mo_consec_neg", metrics, "NORMAL")
    assert get_symbol_state("BTC") == "PAUSED"

    # Simulate argv and invoke main()
    mod_name = "reactivate_symbol"
    spec = importlib.util.spec_from_file_location(mod_name, SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.argv = ["reactivate_symbol.py", "BTC", "--reason", "backtest_validated"]
    spec.loader.exec_module(mod)
    rc = mod.main()

    assert rc == 0
    assert get_symbol_state("BTC") == "NORMAL"


def test_cli_accepts_lowercase_symbol(tmp_db):
    """Symbol is normalized to uppercase by the CLI."""
    import importlib
    from health import apply_transition, get_symbol_state

    metrics = {"trades_count_total": 50, "win_rate_20_trades": 0.5,
                "pnl_30d": 0.0, "pnl_by_month": {},
                "months_negative_consecutive": 3}
    apply_transition("JUP", "PAUSED", "3mo_consec_neg", metrics, "REDUCED")

    spec = importlib.util.spec_from_file_location("reactivate_symbol_2", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.argv = ["reactivate_symbol.py", "jup"]  # lowercase!
    spec.loader.exec_module(mod)
    rc = mod.main()

    assert rc == 0
    assert get_symbol_state("JUP") == "NORMAL"
