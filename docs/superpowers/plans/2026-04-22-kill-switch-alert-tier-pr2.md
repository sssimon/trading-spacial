# Kill switch Alert tier (#138 PR 2 of 4) implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the ALERT tier into the already-shipped Foundation (#138 PR 1). Two integration points: (1) prepend `⚠️ ALERT` to the signal Telegram message for any symbol whose current state is `ALERT`; (2) fire a one-shot `notify(HealthEvent)` when a symbol transitions *to* `ALERT`.

**Architecture:** Extend `SignalEvent` with an optional `health_state` field rendered by `signal.telegram.j2`. Teach the `push_telegram_direct` shim (in `btc_api.py`) to look up `get_symbol_state(symbol)` and stamp it into the `SignalEvent`. Extend `health.evaluate_and_record` to emit `notify(HealthEvent)` on any transition that ends in `ALERT`. No change to trading behavior — the symbol is still operated on normally; we just add a visibility prefix and a one-shot warning.

**Tech Stack:** Python 3.12, notifier module (PR #164), health module (PR #165), pytest.

---

## File structure

```
notifier/events.py                               (modified: +1 field on SignalEvent)
notifier/templates/signal.telegram.j2            (modified: conditional prefix)
tests/test_notifier_events.py                    (modified: +1 test for field)
tests/test_notifier_templates.py                 (modified: +1 test for prefix)
tests/test_notifier_signal_parity.py             (modified: update expected string)

btc_api.py                                       (modified: push_telegram_direct shim)
tests/test_health_shim_integration.py            (new: shim + get_symbol_state wiring)

health.py                                        (modified: notify on ALERT transitions)
tests/test_health_alert_notify.py                (new: notify fires on transition only)
```

Task count: 4 tasks — small, tightly scoped.

---

## Task 1: SignalEvent health_state field + template prefix

**Files:**
- Modify: `notifier/events.py`
- Modify: `notifier/templates/signal.telegram.j2`
- Modify: `tests/test_notifier_events.py`
- Modify: `tests/test_notifier_templates.py`
- Modify: `tests/test_notifier_signal_parity.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_notifier_events.py`:
```python
def test_signal_event_health_state_default():
    """Default health_state is 'NORMAL' so existing callers stay backward-compat."""
    from notifier import SignalEvent
    ev = SignalEvent(symbol="BTC", score=5, direction="LONG",
                     entry=1.0, sl=1.0, tp=1.0)
    assert ev.health_state == "NORMAL"


def test_signal_event_health_state_set_to_alert():
    from notifier import SignalEvent
    ev = SignalEvent(symbol="BTC", score=5, direction="LONG",
                     entry=1.0, sl=1.0, tp=1.0, health_state="ALERT")
    assert ev.health_state == "ALERT"
```

Append to `tests/test_notifier_templates.py`:
```python
def test_signal_telegram_prepends_alert_warning():
    """ALERT symbols get a '⚠️ ALERT' prefix on the first line."""
    from notifier._templates import render
    from notifier import SignalEvent
    ev = SignalEvent(symbol="BTCUSDT", score=6, direction="LONG",
                     entry=50_000.0, sl=49_000.0, tp=55_000.0,
                     health_state="ALERT")
    msg = render(ev, channel="telegram")
    assert msg.startswith("⚠️ *ALERT* "), f"unexpected prefix: {msg!r}"
    assert "BTCUSDT" in msg


def test_signal_telegram_no_prefix_for_normal():
    """NORMAL symbols render identically to pre-PR — no prefix."""
    from notifier._templates import render
    from notifier import SignalEvent
    ev = SignalEvent(symbol="BTCUSDT", score=6, direction="LONG",
                     entry=50_000.0, sl=49_000.0, tp=55_000.0)
    msg = render(ev, channel="telegram")
    assert not msg.startswith("⚠️")
```

Update `tests/test_notifier_signal_parity.py::test_signal_template_stable_for_fixed_input` expected value. The NORMAL-case output should be UNCHANGED — so the template must only emit the prefix when `health_state != "NORMAL"`.

- [ ] **Step 2: Run to verify failures**

```bash
python -m pytest tests/test_notifier_events.py tests/test_notifier_templates.py tests/test_notifier_signal_parity.py -v
```
Expected: 2 new events tests FAIL (unexpected kwarg `health_state`), 2 new template tests FAIL (no prefix), parity test still passes for now (template unchanged).

- [ ] **Step 3: Add the field to SignalEvent**

In `notifier/events.py`, modify `SignalEvent` to add the field. Find:
```python
@dataclass
class SignalEvent(_BaseEvent):
    symbol: str = ""
    score: int = 0
    direction: str = "LONG"
    entry: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
```

Replace with:
```python
@dataclass
class SignalEvent(_BaseEvent):
    symbol: str = ""
    score: int = 0
    direction: str = "LONG"
    entry: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    # Kill-switch context (#138): "NORMAL" | "ALERT" | "REDUCED" | "PAUSED".
    # Determines whether the template prepends a warning prefix.
    health_state: str = "NORMAL"
```

- [ ] **Step 4: Update signal.telegram.j2**

Replace the contents of `notifier/templates/signal.telegram.j2` with:
```jinja
{%- if health_state and health_state != "NORMAL" -%}
⚠️ *{{ health_state }}* 
{% endif -%}
*Signal* `{{ symbol }}`
Score: *{{ score }}* ({{ direction }})
Entry: `{{ "%.2f"|format(entry) }}` | SL: `{{ "%.2f"|format(sl) }}` | TP: `{{ "%.2f"|format(tp) }}`
```

Note: the trailing space after `{{ health_state }}` is intentional (so the prefix reads `⚠️ *ALERT* *Signal*` on one line in the final rendered message). Test the parity case to confirm NORMAL output is unchanged.

- [ ] **Step 5: Run events + template tests**

```bash
python -m pytest tests/test_notifier_events.py tests/test_notifier_templates.py -v
```
Expected: all PASS.

- [ ] **Step 6: Run + fix the parity test**

```bash
python -m pytest tests/test_notifier_signal_parity.py -v
```
If the NORMAL case now renders with a leading newline from the `{%- if ... -%}` block, adjust the template's whitespace control. The goal is: **for NORMAL, the output string is IDENTICAL to pre-PR** — no new newline at the start.

The template above uses `{%- if health_state and health_state != "NORMAL" -%}` (strips whitespace before the if) and `{% endif -%}` (strips after endif) so the NORMAL branch produces nothing at all — the `*Signal*` line stays the first byte.

- [ ] **Step 7: Commit**

```bash
git add notifier/events.py notifier/templates/signal.telegram.j2 \
        tests/test_notifier_events.py tests/test_notifier_templates.py \
        tests/test_notifier_signal_parity.py
git commit -m "feat(notifier): SignalEvent.health_state + ALERT prefix in template (#138)"
```

---

## Task 2: push_telegram_direct shim looks up symbol health state

**Files:**
- Modify: `btc_api.py` (push_telegram_direct shim body)
- Create: `tests/test_health_shim_integration.py`

- [ ] **Step 1: Write failing integration test**

Create `tests/test_health_shim_integration.py`:
```python
"""push_telegram_direct (the notifier shim) must stamp the symbol's current
health_state into the SignalEvent so ALERT symbols get the warning prefix."""
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import btc_api
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    # Reset ratelimit between tests
    from notifier import ratelimit
    ratelimit.reset_all_for_tests()
    yield db_path


def _cfg():
    return {
        "notifier": {"enabled": True, "test_mode": False},
        "telegram_bot_token": "t", "telegram_chat_id": "1",
    }


def test_shim_sends_signal_with_health_state_from_db(tmp_db):
    """If get_symbol_state(sym) == 'ALERT', the SignalEvent carries health_state='ALERT'."""
    from health import apply_transition
    import btc_api

    # Seed BTC in ALERT
    apply_transition(
        "BTC", new_state="ALERT", reason="wr_below_threshold",
        metrics={"trades_count_total": 50, "win_rate_20_trades": 0.1,
                 "pnl_30d": 0.0, "pnl_by_month": {}, "months_negative_consecutive": 0},
        from_state="NORMAL",
    )

    rep = {"symbol": "BTC", "score": 6, "direction": "LONG",
           "price": 50_000.0, "sizing_1h": {"sl_precio": 49_000.0, "tp_precio": 55_000.0}}

    fake_resp = MagicMock()
    fake_resp.ok = True
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"ok": True}

    with patch("notifier.channels.telegram.requests.post", return_value=fake_resp) as mock_post:
        result = btc_api.push_telegram_direct(rep, _cfg())

    assert result is True
    assert mock_post.call_count == 1
    # The rendered text sent to Telegram must contain the ALERT prefix.
    sent_text = mock_post.call_args.kwargs["json"]["text"]
    assert sent_text.startswith("⚠️ *ALERT*"), f"no prefix on shim-routed signal: {sent_text!r}"
    assert "BTC" in sent_text


def test_shim_unknown_symbol_defaults_to_normal(tmp_db):
    """If no row exists in symbol_health, health_state defaults to NORMAL → no prefix."""
    import btc_api

    rep = {"symbol": "UNSEEN", "score": 3, "direction": "LONG",
           "price": 1.0, "sizing_1h": {"sl_precio": 0.9, "tp_precio": 1.2}}

    fake_resp = MagicMock()
    fake_resp.ok = True

    with patch("notifier.channels.telegram.requests.post", return_value=fake_resp) as mock_post:
        btc_api.push_telegram_direct(rep, _cfg())

    sent_text = mock_post.call_args.kwargs["json"]["text"]
    assert not sent_text.startswith("⚠️"), f"unexpected prefix: {sent_text!r}"
```

- [ ] **Step 2: Run to verify failures**

```bash
python -m pytest tests/test_health_shim_integration.py -v
```
Expected: 2 FAIL — shim doesn't look up health state yet.

- [ ] **Step 3: Update push_telegram_direct shim**

Locate `def push_telegram_direct(rep: dict, cfg: dict, max_retries: int = 3):` in `btc_api.py` (around line 1111). Modify the SignalEvent construction to include `health_state`:

Find the `notify(SignalEvent(...))` call inside the function body. Currently:
```python
    receipts = notify(
        SignalEvent(
            symbol=rep.get("symbol", ""),
            score=int(rep.get("score", 0) or 0),
            direction=rep.get("direction", "LONG"),
            entry=float(rep.get("price") or 0.0),
            sl=float((rep.get("sizing_1h") or {}).get("sl_precio") or 0.0),
            tp=float((rep.get("sizing_1h") or {}).get("tp_precio") or 0.0),
        ),
        cfg=cfg,
    )
    return bool(receipts and receipts[0].status == "ok")
```

Replace with:
```python
    # Kill switch #138 PR 2: stamp symbol health state so ALERT symbols get
    # a warning prefix in the Telegram message.
    symbol = rep.get("symbol", "")
    try:
        from health import get_symbol_state
        health_state = get_symbol_state(symbol) if symbol else "NORMAL"
    except Exception as e:
        log.warning("push_telegram_direct: health lookup failed for %s: %s", symbol, e)
        health_state = "NORMAL"

    receipts = notify(
        SignalEvent(
            symbol=symbol,
            score=int(rep.get("score", 0) or 0),
            direction=rep.get("direction", "LONG"),
            entry=float(rep.get("price") or 0.0),
            sl=float((rep.get("sizing_1h") or {}).get("sl_precio") or 0.0),
            tp=float((rep.get("sizing_1h") or {}).get("tp_precio") or 0.0),
            health_state=health_state,
        ),
        cfg=cfg,
    )
    return bool(receipts and receipts[0].status == "ok")
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_health_shim_integration.py -v
```
Expected: 2 PASS.

- [ ] **Step 5: Full regression**

```bash
python -m pytest tests/ -q -m "not network"
```
Expected: all PASS (no regressions on existing test_notifier_signal_parity — the NORMAL case must be byte-identical to pre-PR).

- [ ] **Step 6: Commit**

```bash
git add btc_api.py tests/test_health_shim_integration.py
git commit -m "feat(health): push_telegram_direct stamps health_state on SignalEvent (#138)"
```

---

## Task 3: evaluate_and_record emits notify(HealthEvent) on ALERT transitions

**Files:**
- Modify: `health.py`
- Create: `tests/test_health_alert_notify.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_health_alert_notify.py`:
```python
"""evaluate_and_record must fire notify(HealthEvent) once when a symbol
transitions to ALERT, and must NOT re-fire on subsequent evaluations where
the state stays ALERT."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import btc_api
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    from notifier import ratelimit
    ratelimit.reset_all_for_tests()
    yield db_path


def _insert_closed(conn, symbol, pnl, exit_ts):
    conn.execute(
        """INSERT INTO positions
           (symbol, direction, status, entry_price, entry_ts,
            exit_price, exit_ts, exit_reason, pnl_usd, pnl_pct)
           VALUES (?, 'LONG', 'closed', 100.0, ?, 101.0, ?, 'TP', ?, ?)""",
        (symbol, exit_ts, exit_ts, pnl, pnl / 100.0),
    )
    conn.commit()


CFG = {"kill_switch": {
    "enabled": True, "min_trades_for_eval": 20,
    "alert_win_rate_threshold": 0.15,
    "reduce_pnl_window_days": 30, "reduce_size_factor": 0.5,
    "pause_months_consecutive": 3, "auto_recovery_enabled": True,
}}
NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


def _seed_for_alert(conn):
    """25 trades: 1 win, 24 losses (positive pnl, so no REDUCED) — wr=0.04 → ALERT."""
    for i in range(25):
        pnl = 10.0 if i == 0 else 5.0  # all winners by value, but…
        # Actually: we need the WIN RATE low but the aggregate P&L POSITIVE (else
        # REDUCED/PAUSED would fire first). Trick: small wins, but count few of them.
        pass
    # Correct scenario: 25 closed trades, last 20 have 1 winner (wr=0.05),
    # but the pnl sum over 30d is positive.
    for i in range(25):
        pnl = 100.0 if i == 24 else -1.0  # one big winner beats 24 tiny losers → agg positive
        _insert_closed(conn, "BTC", pnl, (NOW - timedelta(days=25 - i)).isoformat())


def test_transition_to_alert_fires_notify(tmp_db):
    from health import evaluate_and_record
    import btc_api

    conn = btc_api.get_db()
    try:
        _seed_for_alert(conn)
    finally:
        conn.close()

    with patch("health.notify") as mock_notify:
        state = evaluate_and_record("BTC", CFG, now=NOW)

    assert state == "ALERT"
    assert mock_notify.call_count == 1
    event_arg = mock_notify.call_args.args[0]
    # Arg is a HealthEvent — check its fields
    assert event_arg.symbol == "BTC"
    assert event_arg.to_state == "ALERT"
    assert event_arg.from_state == "NORMAL"


def test_alert_no_renotify_when_state_unchanged(tmp_db):
    """After the first ALERT transition, a second evaluate_and_record with the
    same data must NOT fire notify again (state stays ALERT)."""
    from health import evaluate_and_record
    import btc_api

    conn = btc_api.get_db()
    try:
        _seed_for_alert(conn)
    finally:
        conn.close()

    with patch("health.notify") as mock_notify:
        evaluate_and_record("BTC", CFG, now=NOW)
        evaluate_and_record("BTC", CFG, now=NOW)

    assert mock_notify.call_count == 1  # not 2


def test_non_alert_transitions_do_not_fire_in_pr2(tmp_db):
    """PR 2 only emits for ALERT. REDUCED/PAUSED transitions stay silent (for now)."""
    from health import evaluate_and_record
    import btc_api

    # 25 trades all negative → REDUCED (not ALERT)
    conn = btc_api.get_db()
    try:
        for i in range(25):
            _insert_closed(conn, "DOGE", -100.0, (NOW - timedelta(days=25 - i)).isoformat())
    finally:
        conn.close()

    with patch("health.notify") as mock_notify:
        state = evaluate_and_record("DOGE", CFG, now=NOW)

    assert state == "REDUCED"
    assert mock_notify.call_count == 0
```

- [ ] **Step 2: Run to verify failures**

```bash
python -m pytest tests/test_health_alert_notify.py -v
```
Expected: 3 FAIL — `health.notify` doesn't exist yet (health.py doesn't import notifier).

- [ ] **Step 3: Modify health.evaluate_and_record**

In `health.py`, add the import at the top (after the existing `from typing import Any`):
```python
# Lazy re-export so tests can patch health.notify without reaching into notifier.
try:
    from notifier import notify, HealthEvent  # noqa: F401
except ImportError:
    notify = None  # type: ignore
    HealthEvent = None  # type: ignore
```

Then locate `evaluate_and_record` and modify the transition path. Current tail:
```python
    if new_state != current:
        apply_transition(symbol, new_state=new_state, reason=reason,
                         metrics=metrics, from_state=current)
    else:
        _record_evaluation(symbol, metrics, new_state)
    return new_state
```

Replace with:
```python
    if new_state != current:
        apply_transition(symbol, new_state=new_state, reason=reason,
                         metrics=metrics, from_state=current)
        # PR 2 (#138): one-shot notify only on transitions into ALERT.
        # PRs 3/4 will extend this to REDUCED and PAUSED.
        if new_state == "ALERT" and notify is not None and HealthEvent is not None:
            try:
                # Config for notifier is the same dict passed in (contains telegram_bot_token etc.)
                notify(
                    HealthEvent(symbol=symbol, from_state=current,
                                to_state=new_state, reason=reason, metrics=metrics),
                    cfg=cfg,
                )
            except Exception as e:  # noqa: BLE001
                log.warning("health: ALERT notify failed for %s: %s", symbol, e)
    else:
        _record_evaluation(symbol, metrics, new_state)
    return new_state
```

Note: the test patches `health.notify`, which works because of the `from notifier import notify` re-export at the top of `health.py`.

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_health_alert_notify.py -v
```
Expected: 3 PASS.

- [ ] **Step 5: Full regression**

```bash
python -m pytest tests/ -q -m "not network"
```
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add health.py tests/test_health_alert_notify.py
git commit -m "feat(health): one-shot notify(HealthEvent) on ALERT transitions (#138)"
```

---

## Task 4: Full regression + push + PR

- [ ] **Step 1: Full test suite**

```bash
python -m pytest tests/ -q -m "not network"
```
Expected: all PASS. Prior baseline was 547 (end of PR #165); PR 2 adds ~7 tests (2 events + 2 template + 2 shim + 3 alert-notify − 1 parity update = 7 net). Target ≈ **554 passed**, 0 failed.

- [ ] **Step 2: Push branch**

```bash
git push -u origin feat/kill-switch-alert
```

- [ ] **Step 3: Open PR**

```bash
gh pr create --base main --head feat/kill-switch-alert \
  --title "feat(health): kill switch Alert tier (#138 PR 2 of 4)" \
  --body "$(cat <<'BODY'
## Summary
Second PR in the #138 series. Wires the ALERT tier into the Foundation from PR #165:

1. `SignalEvent` gets an optional `health_state` field (default `NORMAL`).
   `signal.telegram.j2` prepends `⚠️ *ALERT*` when `health_state != NORMAL`.
2. `push_telegram_direct` shim in `btc_api.py` looks up `get_symbol_state(symbol)`
   before building the `SignalEvent`, so every outbound signal Telegram message
   carries the correct state.
3. `health.evaluate_and_record` emits a one-shot `notify(HealthEvent)` when a
   symbol transitions to `ALERT` (any state → ALERT). Subsequent evaluations
   while the symbol stays in ALERT do NOT re-fire.

REDUCED / PAUSED transitions remain silent — PRs 3 and 4 extend the notify logic.

## Does NOT change
- Trading behavior: ALERT symbols are still operated on normally. This PR only
  adds a visual prefix + a single warning notification on the state change.
- The byte-identical NORMAL case — `test_notifier_signal_parity` still passes.

## Tests
- [x] ~7 new tests across 5 files
- [x] Full suite: ~554 passed, 0 failed

Closes partial: #138 (PR 2 of 4).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
BODY
)"
```

- [ ] **Step 4: Watch CI**

```bash
sleep 12 && gh pr checks --watch --interval 15
```
Expected: backend-tests + frontend-typecheck PASS.

---

## Self-review

**Spec coverage** (against `docs/superpowers/specs/es/2026-04-21-kill-switch-design.md` §9 PR 2):
- scan/scanner → prefix `⚠️ ALERT` when state == ALERT: Task 1+2 (field + template + shim wiring). ✓
- one-shot notify(HealthEvent) on NORMAL→ALERT: Task 3. Note: also covers REDUCED→ALERT and PAUSED→ALERT (possible via auto-recovery paths), which is a reasonable superset. ✓
- NOT firing on subsequent same-state evaluations: Task 3 test `test_alert_no_renotify_when_state_unchanged`. ✓

**Placeholder scan:** no TBDs. Template whitespace handling is explicit. The `health.notify` re-export pattern is shown with its rationale (tests can patch `health.notify`).

**Type consistency:**
- `SignalEvent.health_state: str = "NORMAL"` — Task 1 defines; Task 2 shim sets it.
- `get_symbol_state(symbol) -> str` — already exists in health.py (PR #165); Task 2 imports lazily.
- `notify(event, cfg)` — already exists in notifier (PR #164); Task 3 imports via health re-export.

All consistent.

**Scope:** PR 2 of 4 in #138 series. Does not include REDUCED/PAUSED tier actions (PRs 3/4).
