# Selection-world provenance fingerprint — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stamp a `selection_fingerprint` (a digest of the world-state under which a deflated-selection metric is computed) on every evaluation artifact, so the deflation-N pool stays cost-model-homogeneous and the irreversible holdout gate hard-refuses to fire a hypothesis frozen under a different selection world.

**Architecture:** A new leaf module `selection_provenance.py` builds one extensible digest (seeded with cost-model + deflation params; `_DIGEST_VERSION` bumps when the ingredient set grows). `db/trials.py` and `db/hypotheses.py` gain `cost_model` + `selection_fingerprint` columns (idempotent ALTER + v2 backfill), auto-stamp on write, and the holdout gate makes `selection_fingerprint` a FROZEN field (seal + trigger) + guards lock and fire on fingerprint match.

**Tech Stack:** Python 3, SQLite (signals.db, WAL, `db.transaction.transaction()`), pytest, hashlib/json.

**Spec:** `docs/superpowers/specs/2026-06-02-cost-model-provenance-design.md` — read it. The cost-model is the FIRST INGREDIENT of the digest, not the unit. Voronov reframe: the unit is the selection world (multi-coordinate); new coordinates go INSIDE the digest, never as new schema/seal/trigger.

**Branch:** `feat/cost-model-provenance` (spec committed at `8ff2a9b`).

**GUARDRAIL-CRITICAL:** Tasks 6-9 touch `db/hypotheses.py`'s `_FROZEN_FIELDS` + seal + immutability trigger + fire-guard (the holdout bala única). After the last task, an **adversarial audit before push** is MANDATORY ([[adversarial-audit-before-push-pattern]]) — implementer separate from auditors.

**Import-cycle rule (load-bearing):** `selection_provenance.py` is a LEAF — module-level imports only `backtest_costs` + `deflation` (both leaves, no `db` imports). It lazy-imports the A03 constants from `db.trials` INSIDE the digest function. `db/trials.py` and `db/hypotheses.py` import `selection_provenance` at module level. This is acyclic: importing `selection_provenance` never re-enters `db`.

**Verified line numbers** (re-grep if a hunk doesn't match): `db/trials.py` — `_ensure_trials_schema` `:66-91`, `claim_trial` `:119-147`, `A03_DECAY_DATE`/`A03_N_FLOOR` `:182-183`, `selection_population_stats` `:186-205`. `db/hypotheses.py` — `_FROZEN_FIELDS` `:47-53`, `_ensure_schema`+trigger `:81-155`, `claim_hypothesis` `:158-195`, `_compute_seal` `:215-218`, `_deflation_probability` `:240-263`, `lock_hypothesis` `:280-361`, `assert_fireable` `:409-435`. `backtest_costs.py` — `Calibration` dataclass, `load_calibration(path=...)`. `deflation.py` — top-of-module. Test fixtures: `tests/test_trials_registry.py::trials_db` (`:9-17`), `tests/test_hypotheses_gate.py::hyp_db` (`:15-24`).

---

## File Structure

- **Create `selection_provenance.py`** (repo root) — the digest. One responsibility: assemble + hash the selection-world coordinates.
- **Modify `deflation.py`** — add `ALGO_VERSION = 1`.
- **Modify `backtest_costs.py`** — add `calibration_identity_hash(cal)` + `active_cost_model_id()`.
- **Modify `db/trials.py`** — columns + migration/backfill + auto-stamp + fingerprint filter.
- **Modify `db/hypotheses.py`** — columns + migration/trigger + auto-stamp + frozen field + lock 4f + fire check 6.
- **Create `tests/test_selection_provenance.py`** — the digest + accessors.
- **Modify `tests/test_trials_registry.py`** — trials provenance + migration.
- **Modify `tests/test_hypotheses_gate.py`** — hypotheses provenance + guards.

---

### Task 1: `deflation.ALGO_VERSION`

**Files:** Modify `deflation.py` (top of module, after the docstring). Test: `tests/test_deflation.py` (append).

- [ ] **Step 1: Write the failing test** (append to `tests/test_deflation.py`):

```python
def test_algo_version_present():
    import deflation
    assert isinstance(deflation.ALGO_VERSION, int)
    assert deflation.ALGO_VERSION >= 1
```

- [ ] **Step 2: Run, confirm FAIL**: `python -m pytest tests/test_deflation.py::test_algo_version_present -v` (AttributeError).

- [ ] **Step 3: Implement** — add after the module docstring in `deflation.py`, before the first import or right after imports:

```python
# Version of the deflation algorithm. Bumped when the formula changes; an
# ingredient of the selection fingerprint (selection_provenance.py), so a change
# to how the deflated metric is computed re-versions every evaluation artifact.
ALGO_VERSION = 1
```

- [ ] **Step 4: Run, confirm PASS**: same command.

- [ ] **Step 5: Commit**:
```bash
git add deflation.py tests/test_deflation.py
git commit -m "feat(provenance): deflation.ALGO_VERSION (fingerprint ingredient)"
```

---

### Task 2: `calibration_identity_hash` + `active_cost_model_id` (backtest_costs.py)

**Files:** Modify `backtest_costs.py` (add `import hashlib` at top if absent; add the two functions after `load_calibration`). Test: `tests/test_selection_provenance.py` (create).

- [ ] **Step 1: Write the failing test** (create `tests/test_selection_provenance.py`):

```python
"""Selection-world provenance — see docs/superpowers/specs/2026-06-02-cost-model-provenance-design.md."""
import pytest


class TestCalibrationIdentity:
    def test_active_cost_model_id_shape(self):
        from backtest_costs import active_cost_model_id
        active_model, cal_hash = active_cost_model_id()
        assert active_model == "v3"               # main costs_calibration.json
        assert isinstance(cal_hash, str) and len(cal_hash) == 64   # sha256 hex

    def test_identity_hash_ignores_prose_changes(self):
        # The hash covers the SELECTOR numbers, not sources/sensitivity_note.
        from backtest_costs import load_calibration, calibration_identity_hash
        import copy, dataclasses
        cal = load_calibration()
        h1 = calibration_identity_hash(cal)
        # mutate prose-only fields -> hash unchanged
        cal2 = dataclasses.replace(cal, sources={"x": "different prose"},
                                   sensitivity_note="totally different", model="reworded")
        assert calibration_identity_hash(cal2) == h1

    def test_identity_hash_changes_on_selector_change(self):
        from backtest_costs import load_calibration, calibration_identity_hash
        import dataclasses
        cal = load_calibration()
        h1 = calibration_identity_hash(cal)
        # change a global selector number -> hash changes
        cal2 = dataclasses.replace(cal, global_=dataclasses.replace(cal.global_, Y_impact_constant=99.0))
        assert calibration_identity_hash(cal2) != h1

    def test_v2_sibling_hash_differs_from_v3(self):
        from backtest_costs import load_calibration, calibration_identity_hash
        v3 = calibration_identity_hash(load_calibration())
        v2 = calibration_identity_hash(load_calibration(path="costs_calibration.v2.json"))
        assert v2 != v3
```

- [ ] **Step 2: Run, confirm FAIL**: `python -m pytest tests/test_selection_provenance.py::TestCalibrationIdentity -v` (ImportError). Do NOT run the full suite (hangs ~47min on Windows).

- [ ] **Step 3: Implement** — ensure `import hashlib` at the top of `backtest_costs.py`, then add after `load_calibration`:

```python
def calibration_identity_hash(cal: Calibration) -> str:
    """sha256 of the SELECTOR — the numbers that decide which params the bound
    admits (version, active_model, global, per-tier floor+tail / v2 base+size_factor).
    Excludes prose (model/sources/sensitivity_note). Computed on the parsed
    Calibration, so JSON whitespace does not affect it. NaN cross-version fields
    serialize deterministically as 'NaN' — fine for a stable digest."""
    tiers = {
        name: {
            "base_bps": tp.base_bps, "size_factor": tp.size_factor,
            "half_spread_bps": tp.half_spread_bps, "fee_bps_per_side": tp.fee_bps_per_side,
            "funding_rate_bps_per_8h": tp.funding_rate_bps_per_8h,
            "stress_mult": tp.stress_mult, "sigma_daily_bps": tp.sigma_daily_bps,
        }
        for name, tp in cal.tiers.items()
    }
    g = cal.global_
    glob = None if g is None else {
        "Y_impact_constant": g.Y_impact_constant,
        "total_cost_cap_bps": g.total_cost_cap_bps,
        "liquidity_fallback_floor_bps": g.liquidity_fallback_floor_bps,
        "v_daily_minutes_per_day": g.v_daily_minutes_per_day,
    }
    payload = {"version": cal.version, "active_model": cal.active_model,
               "global": glob, "tiers": tiers}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def active_cost_model_id() -> tuple[str, str]:
    """(active_model, calibration_identity_hash) of the ACTIVE calibration."""
    cal = load_calibration()
    return cal.active_model, calibration_identity_hash(cal)
```

- [ ] **Step 4: Run, confirm PASS**: `python -m pytest tests/test_selection_provenance.py::TestCalibrationIdentity -v` (4 passed).

- [ ] **Step 5: Regression** (v3 cost suite uses the same module): `python -m pytest tests/test_backtest_costs_v3.py -q` (must still pass).

- [ ] **Step 6: Commit**:
```bash
git add backtest_costs.py tests/test_selection_provenance.py
git commit -m "feat(provenance): calibration_identity_hash + active_cost_model_id"
```

---

### Task 3: `selection_provenance.py` — the digest

**Files:** Create `selection_provenance.py`. Test: `tests/test_selection_provenance.py` (append).

- [ ] **Step 1: Write the failing test** (append):

```python
class TestSelectionFingerprint:
    def setup_method(self):
        import selection_provenance
        selection_provenance._clear_cache()

    def test_fingerprint_shape_and_components(self):
        import selection_provenance, deflation
        fp, comp = selection_provenance.selection_fingerprint()
        assert isinstance(fp, str) and len(fp) == 64
        assert comp["_digest_version"] == selection_provenance._DIGEST_VERSION
        assert comp["cost_model"]["active_model"] == "v3"
        assert len(comp["cost_model"]["calibration_hash"]) == 64
        assert comp["deflation"]["algo_version"] == deflation.ALGO_VERSION

    def test_memoized(self):
        import selection_provenance
        a = selection_provenance.selection_fingerprint()
        b = selection_provenance.selection_fingerprint()
        assert a is b  # same tuple object -> memoized

    def test_clear_cache_recomputes(self):
        import selection_provenance
        a = selection_provenance.selection_fingerprint()
        selection_provenance._clear_cache()
        b = selection_provenance.selection_fingerprint()
        assert a is not b and a[0] == b[0]  # new object, same digest (world unchanged)

    def test_changing_an_ingredient_changes_the_digest(self, monkeypatch):
        import selection_provenance
        base = selection_provenance.selection_fingerprint()[0]
        selection_provenance._clear_cache()
        # bump the digest version -> different fingerprint
        monkeypatch.setattr(selection_provenance, "_DIGEST_VERSION", 999)
        assert selection_provenance.selection_fingerprint()[0] != base

    def test_v2_sibling_fingerprint_differs(self):
        import selection_provenance
        active = selection_provenance.selection_fingerprint()[0]
        v2 = selection_provenance.fingerprint_for_v2_sibling()
        assert v2 != active  # the v2-era world is a different selector
```

- [ ] **Step 2: Run, confirm FAIL**: `python -m pytest tests/test_selection_provenance.py::TestSelectionFingerprint -v`.

- [ ] **Step 3: Implement** — create `selection_provenance.py`:

```python
"""Selection-world provenance fingerprint.

The unit relative to which a frozen selection claim is frozen: a sha256 over the
COMPLETE world-state under which a deflated-selection metric is computed. Two
artifacts with different fingerprints are NOT comparable. New world-coordinates
are added INSIDE this digest (bump _DIGEST_VERSION) — never as new schema columns,
frozen fields, or trigger clauses (that would be accretion by enumeration).

LEAF MODULE: module-level imports are backtest_costs + deflation only (both leaves).
The A03 constants are lazy-imported from db.trials INSIDE _build to keep this module
acyclic (db.trials / db.hypotheses import this module at their module level).

See docs/superpowers/specs/2026-06-02-cost-model-provenance-design.md.
"""
from __future__ import annotations

import hashlib
import json

from backtest_costs import active_cost_model_id, calibration_identity_hash, load_calibration
import deflation

# Bump when the SET of ingredients changes. Adding a coordinate re-versions every
# fingerprint: previously-conflated worlds become distinguished (honest, auditable).
_DIGEST_VERSION = 1

_cache: "tuple[str, dict] | None" = None


def _clear_cache() -> None:
    """Reset the per-process memo (tests; or if the active calibration is reloaded)."""
    global _cache
    _cache = None


def _build(active_model: str, calibration_hash: str) -> tuple[str, dict]:
    """Assemble + hash the selection world for a given cost-model identity. The
    non-cost-model coordinates (deflation params) come from the CURRENT process —
    correct for stamping the active world and for backfilling the v2 era (the A03
    params and deflation algo did not change across the v2->v3 transition)."""
    from db.trials import A03_DECAY_DATE, A03_N_FLOOR  # lazy: keep this module a leaf
    components = {
        "_digest_version": _DIGEST_VERSION,
        "cost_model": {"active_model": active_model, "calibration_hash": calibration_hash},
        "deflation": {
            "a03_decay_date": A03_DECAY_DATE.isoformat(),
            "a03_n_floor": A03_N_FLOOR,
            "algo_version": deflation.ALGO_VERSION,
        },
    }
    digest = hashlib.sha256(
        json.dumps(components, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return digest, components


def selection_fingerprint() -> tuple[str, dict]:
    """(fingerprint_hash, components) of the ACTIVE selection world. Memoized."""
    global _cache
    if _cache is None:
        active_model, cal_hash = active_cost_model_id()
        _cache = _build(active_model, cal_hash)
    return _cache


def fingerprint_for_v2_sibling() -> str:
    """The selection fingerprint of the v2-era world (cost-model = the frozen
    costs_calibration.v2.json + current deflation params). Used to backfill
    pre-v3 trials/hypotheses. NOT memoized (called once at migration)."""
    cal = load_calibration(path="costs_calibration.v2.json")
    return _build("v2", calibration_identity_hash(cal))[0]
```

- [ ] **Step 4: Run, confirm PASS**: `python -m pytest tests/test_selection_provenance.py -v` (all pass).

- [ ] **Step 5: Confirm no import cycle**: `python -c "import db.trials, db.hypotheses, selection_provenance; print('ok')"` — expect `ok` (no ImportError / circular).

- [ ] **Step 6: Commit**:
```bash
git add selection_provenance.py tests/test_selection_provenance.py
git commit -m "feat(provenance): selection_fingerprint digest (leaf module, extensible)"
```

---

### Task 4: trials — schema columns + idempotent migration + v2 backfill

**Files:** Modify `db/trials.py` (`_ensure_trials_schema`, `:66-91`). Test: `tests/test_trials_registry.py` (append).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_trials_registry.py`):

```python
def test_new_db_has_provenance_columns(trials_db):
    import db.trials
    db.trials._ensure_trials_schema()
    with transaction() as con:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(trials)")}
    assert "cost_model" in cols and "selection_fingerprint" in cols


def test_migration_adds_columns_and_backfills_v2(trials_db, monkeypatch):
    import db.trials, selection_provenance
    selection_provenance._clear_cache()
    # Simulate an OLD-schema DB: create the pre-provenance table + one row.
    with transaction() as con:
        con.execute(
            "CREATE TABLE trials (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "claimed_ts TEXT NOT NULL, finalized_ts TEXT, source TEXT NOT NULL, "
            "study_type TEXT NOT NULL DEFAULT 'exploratory', symbol TEXT, "
            "combo_json TEXT NOT NULL, window_label TEXT, "
            "status TEXT NOT NULL DEFAULT 'pending', sharpe REAL, "
            "metrics_json TEXT, error TEXT)")
        con.execute("INSERT INTO trials (claimed_ts, source, combo_json, status, sharpe) "
                    "VALUES ('2026-05-01T00:00:00+00:00', 'auto_tune', '{\"a\":1}', 'ok', 1.2)")
    monkeypatch.setattr(db.trials, "_schema_ensured", False)
    db.trials._ensure_trials_schema()
    with transaction() as con:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(trials)")}
        row = dict(con.execute("SELECT cost_model, selection_fingerprint FROM trials").fetchone())
    assert "selection_fingerprint" in cols
    assert row["cost_model"] == "v2"
    assert row["selection_fingerprint"] == selection_provenance.fingerprint_for_v2_sibling()


def test_migration_idempotent(trials_db, monkeypatch):
    import db.trials
    db.trials._ensure_trials_schema()
    monkeypatch.setattr(db.trials, "_schema_ensured", False)
    db.trials._ensure_trials_schema()  # second run must not raise / not duplicate columns
    with transaction() as con:
        names = [r["name"] for r in con.execute("PRAGMA table_info(trials)")]
    assert names.count("selection_fingerprint") == 1
```

(Also extend the `trials_db` fixture at `tests/test_trials_registry.py:9-17` to reset the digest cache — add after the `db.trials` reset: `import selection_provenance; monkeypatch.setattr(selection_provenance, "_cache", None)`.)

- [ ] **Step 2: Run, confirm FAIL**: `python -m pytest tests/test_trials_registry.py -k "provenance or migration" -v`.

- [ ] **Step 3: Implement** — in `db/trials.py`, add `cost_model TEXT, selection_fingerprint TEXT` to the `CREATE TABLE trials` body, and in `_ensure_trials_schema` after the CREATE, inside the same `with transaction() as con:` block, add the idempotent migration + backfill:

```python
        # provenance migration (idempotent): add columns if absent, backfill pre-v3 rows
        cols = {r["name"] for r in con.execute("PRAGMA table_info(trials)")}
        if "cost_model" not in cols:
            con.execute("ALTER TABLE trials ADD COLUMN cost_model TEXT")
        if "selection_fingerprint" not in cols:
            con.execute("ALTER TABLE trials ADD COLUMN selection_fingerprint TEXT")
        from selection_provenance import fingerprint_for_v2_sibling  # leaf, no cycle
        con.execute(
            "UPDATE trials SET cost_model='v2', selection_fingerprint=? "
            "WHERE selection_fingerprint IS NULL",
            (fingerprint_for_v2_sibling(),),
        )
```

- [ ] **Step 4: Run, confirm PASS**: `python -m pytest tests/test_trials_registry.py -k "provenance or migration" -v`.

- [ ] **Step 5: Regression**: `python -m pytest tests/test_trials_registry.py -q` (existing trials tests still pass — the new columns are nullable/backfilled, claim still works).

- [ ] **Step 6: Commit**:
```bash
git add db/trials.py tests/test_trials_registry.py
git commit -m "feat(provenance): trials cost_model + selection_fingerprint columns + idempotent v2 backfill"
```

---

### Task 5: trials — auto-stamp claim_trial + filter selection_population_stats

**Files:** Modify `db/trials.py` (`claim_trial` `:119-147`, `selection_population_stats` `:186-205`). Test: `tests/test_trials_registry.py` (append).

- [ ] **Step 1: Write the failing tests** (append):

```python
def test_claim_trial_auto_stamps_fingerprint(trials_db):
    import db.trials, selection_provenance
    selection_provenance._clear_cache()
    db.trials.claim_trial(source="auto_tune", combo={"a": 1}, window_label="w")
    fp, _ = selection_provenance.selection_fingerprint()
    with transaction() as con:
        row = dict(con.execute("SELECT cost_model, selection_fingerprint FROM trials").fetchone())
    assert row["cost_model"] == "v3"
    assert row["selection_fingerprint"] == fp


def test_population_stats_filters_by_fingerprint(trials_db, monkeypatch):
    import db.trials
    from db.trials import claim_trial, finalize_trial, selection_population_stats
    # 3 trials under fingerprint A, 2 under B (distinct configs so dedup keeps them)
    monkeypatch.setattr(db.trials, "_active_fingerprint", lambda: ("A", "v3"), raising=False)
    # Simpler: stamp directly by patching the stamp source. Use two real fingerprints
    # by claiming, then UPDATE the rows' fingerprints to controlled values.
    ids = [claim_trial(source="auto_tune", combo={"a": i}, window_label="w") for i in range(5)]
    for i, tid in enumerate(ids):
        finalize_trial(tid, status="ok", metrics={"sharpe": 1.0 + i})
    with transaction() as con:
        for tid in ids[:3]:
            con.execute("UPDATE trials SET selection_fingerprint='AAA' WHERE id=?", (tid,))
        for tid in ids[3:]:
            con.execute("UPDATE trials SET selection_fingerprint='BBB' WHERE id=?", (tid,))
    a = selection_population_stats(selection_fingerprint="AAA")
    b = selection_population_stats(selection_fingerprint="BBB")
    assert a["n_registered"] == 3
    assert b["n_registered"] == 2


def test_population_stats_none_pools_all_legacy(trials_db):
    from db.trials import claim_trial, finalize_trial, selection_population_stats
    for i in range(4):
        tid = claim_trial(source="auto_tune", combo={"a": i}, window_label="w")
        finalize_trial(tid, status="ok", metrics={"sharpe": 1.0 + i})
    assert selection_population_stats()["n_registered"] == 4  # None = pool all (legacy)
```

- [ ] **Step 2: Run, confirm FAIL**: `python -m pytest tests/test_trials_registry.py -k "auto_stamp or population_stats_filters or pools_all" -v`.

- [ ] **Step 3: Implement**:

In `claim_trial`, stamp at insert. Add at module top: `from selection_provenance import selection_fingerprint`. Inside `claim_trial`'s `_do`, compute `fp, _ = selection_fingerprint()` and `active_model, _ = active_cost_model_id()` (import `active_cost_model_id` from backtest_costs) — or read both from the fingerprint components. Simplest:

```python
    from selection_provenance import selection_fingerprint
    from backtest_costs import active_cost_model_id
    fp, _ = selection_fingerprint()
    active_model, _hash = active_cost_model_id()

    def _do() -> int:
        with transaction() as con:
            cur = con.execute(
                "INSERT INTO trials "
                "(claimed_ts, source, study_type, symbol, combo_json, window_label, "
                " status, cost_model, selection_fingerprint) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                (now, source, study_type, symbol, combo_json, window_label,
                 active_model, fp),
            )
            return int(cur.lastrowid)
```

In `selection_population_stats`, add the param + filter:

```python
def selection_population_stats(*, study_type: str = "exploratory",
                               selection_fingerprint: str | None = None) -> dict:
    """... (existing docstring) ... When selection_fingerprint is given, restricts
    the population to trials of that selection world (homogeneous pool). None pools
    all (legacy)."""
    _ensure_trials_schema()
    sql = ("SELECT AVG(sharpe) AS s FROM trials "
           "WHERE study_type = ? AND sharpe IS NOT NULL ")
    params = [study_type]
    if selection_fingerprint is not None:
        sql += "AND selection_fingerprint = ? "
        params.append(selection_fingerprint)
    sql += "GROUP BY source, combo_json, window_label"
    with transaction() as con:
        rows = con.execute(sql, tuple(params)).fetchall()
    sharpes = [float(r["s"]) for r in rows if r["s"] is not None]
    n = len(sharpes)
    sigma = statistics.pstdev(sharpes) if n >= 2 else None
    return {"n_registered": n, "sigma_sr_trials": sigma}
```

(Delete the bogus `_active_fingerprint` monkeypatch line from the test if you used the UPDATE approach — keep the UPDATE-based control which exercises the real filter. The test above uses UPDATE; the `monkeypatch.setattr(..., raising=False)` line is unused — remove it.)

- [ ] **Step 4: Run, confirm PASS** + regression: `python -m pytest tests/test_trials_registry.py -q`.

- [ ] **Step 5: Commit**:
```bash
git add db/trials.py tests/test_trials_registry.py
git commit -m "feat(provenance): auto-stamp claim_trial + cost-model-homogeneous deflation pool"
```

---

### Task 6: hypotheses — schema columns + migration + trigger recreate (GUARDRAIL)

**Files:** Modify `db/hypotheses.py` (`_ensure_schema` `:81-155`). Test: `tests/test_hypotheses_gate.py` (append).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_hypotheses_gate.py`):

```python
def test_hypotheses_has_provenance_columns(hyp_db):
    import db.hypotheses
    db.hypotheses._ensure_schema()
    with transaction() as con:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(hypotheses)")}
    assert "cost_model" in cols and "selection_fingerprint" in cols


def test_trigger_blocks_selection_fingerprint_mutation_after_lock(hyp_db, monkeypatch):
    # A locked row's selection_fingerprint is immutable (trigger raises IntegrityError).
    import db.hypotheses
    db.hypotheses._ensure_schema()
    with transaction() as con:
        con.execute(
            "INSERT INTO hypotheses (created_ts, status, strategy_config_json, "
            "config_hash, window_label, selection_fingerprint, seal) "
            "VALUES ('t','locked','{}','h','w','FP_OLD','seal')")
    with pytest.raises(sqlite3.IntegrityError):
        with transaction() as con:
            con.execute("UPDATE hypotheses SET selection_fingerprint='FP_NEW' WHERE id=1")


def test_hyp_migration_idempotent(hyp_db, monkeypatch):
    import db.hypotheses
    db.hypotheses._ensure_schema()
    monkeypatch.setattr(db.hypotheses, "_schema_ensured", False)
    db.hypotheses._ensure_schema()
    with transaction() as con:
        names = [r["name"] for r in con.execute("PRAGMA table_info(hypotheses)")]
    assert names.count("selection_fingerprint") == 1
```

(Extend the `hyp_db` fixture at `:15-24` to also reset the digest cache: `import selection_provenance; monkeypatch.setattr(selection_provenance, "_cache", None)`.)

- [ ] **Step 2: Run, confirm FAIL**: `python -m pytest tests/test_hypotheses_gate.py -k "provenance_columns or trigger_blocks_selection or hyp_migration" -v`.

- [ ] **Step 3: Implement** — in `db/hypotheses.py` `_ensure_schema`:
  (a) add `cost_model TEXT, selection_fingerprint TEXT` to the `CREATE TABLE hypotheses` body;
  (b) after the CREATE TABLE (still inside the `with transaction()`), add idempotent ALTER (PRAGMA-guarded) for both columns;
  (c) change the trigger from `CREATE TRIGGER IF NOT EXISTS` to `DROP TRIGGER IF EXISTS hypotheses_frozen_after_lock;` then `CREATE TRIGGER hypotheses_frozen_after_lock ...` with one extra line in the `WHEN` clause: `OR NEW.selection_fingerprint IS NOT OLD.selection_fingerprint`.

```python
        cols = {r["name"] for r in con.execute("PRAGMA table_info(hypotheses)")}
        if "cost_model" not in cols:
            con.execute("ALTER TABLE hypotheses ADD COLUMN cost_model TEXT")
        if "selection_fingerprint" not in cols:
            con.execute("ALTER TABLE hypotheses ADD COLUMN selection_fingerprint TEXT")
        con.execute("DROP TRIGGER IF EXISTS hypotheses_frozen_after_lock")
        con.execute(
            """
            CREATE TRIGGER hypotheses_frozen_after_lock
            BEFORE UPDATE ON hypotheses
            FOR EACH ROW
            WHEN OLD.status IN ('locked', 'fired', 'refuted', 'not_refuted')
              AND (
                NEW.strategy_config_json IS NOT OLD.strategy_config_json
                OR NEW.config_hash         IS NOT OLD.config_hash
                OR NEW.symbols_json        IS NOT OLD.symbols_json
                OR NEW.window_label        IS NOT OLD.window_label
                OR NEW.metric              IS NOT OLD.metric
                OR NEW.threshold           IS NOT OLD.threshold
                OR NEW.direction           IS NOT OLD.direction
                OR NEW.deflated_metric     IS NOT OLD.deflated_metric
                OR NEW.deflated_threshold  IS NOT OLD.deflated_threshold
                OR NEW.n_at_lock           IS NOT OLD.n_at_lock
                OR NEW.cand_sharpe         IS NOT OLD.cand_sharpe
                OR NEW.cand_n_returns      IS NOT OLD.cand_n_returns
                OR NEW.cand_skew           IS NOT OLD.cand_skew
                OR NEW.cand_kurt_raw       IS NOT OLD.cand_kurt_raw
                OR NEW.preholdout_trial_ids_json IS NOT OLD.preholdout_trial_ids_json
                OR NEW.walkforward_ref     IS NOT OLD.walkforward_ref
                OR NEW.drift_check_ref     IS NOT OLD.drift_check_ref
                OR NEW.selection_fingerprint IS NOT OLD.selection_fingerprint
                OR NEW.seal                IS NOT OLD.seal
              )
            BEGIN
                SELECT RAISE(ABORT, 'hypothesis frozen fields are immutable after lock');
            END
            """
        )
```

(NOTE: changing the trigger from `IF NOT EXISTS` to DROP+CREATE means it is recreated every process start — idempotent and correct.)

- [ ] **Step 4: Run, confirm PASS**: `python -m pytest tests/test_hypotheses_gate.py -k "provenance_columns or trigger_blocks_selection or hyp_migration" -v`.

- [ ] **Step 5: Regression**: `python -m pytest tests/test_hypotheses_gate.py -q` (existing gate tests still pass — but note: the seal now covers selection_fingerprint, so any test that locks a hypothesis will get a fingerprint stamped in Task 7; until Task 7, claim does NOT set selection_fingerprint, so a locked row's seal payload includes selection_fingerprint=None — consistent within Task 6 since both lock-seal and assert_fireable-seal compute over the same None. Existing lock/fire tests should still pass. If any fail on the seal, it is the Task 7 dependency — proceed to Task 7.)

- [ ] **Step 6: Commit**:
```bash
git add db/hypotheses.py tests/test_hypotheses_gate.py
git commit -m "feat(provenance): hypotheses provenance columns + trigger guards selection_fingerprint"
```

---

### Task 7: hypotheses — auto-stamp claim + selection_fingerprint as FROZEN field (GUARDRAIL)

**Files:** Modify `db/hypotheses.py` (`_FROZEN_FIELDS` `:47-53`, `claim_hypothesis` `:158-195`). Test: `tests/test_hypotheses_gate.py` (append).

- [ ] **Step 1: Write the failing tests** (append):

```python
def test_claim_hypothesis_auto_stamps_fingerprint(hyp_db):
    import selection_provenance
    from db.hypotheses import claim_hypothesis
    selection_provenance._clear_cache()
    fp, _ = selection_provenance.selection_fingerprint()
    hid = claim_hypothesis(
        strategy_config={"atr_sl_mult": 1.0}, symbols=["BTCUSDT"],
        window_label="w", metric="net_pnl", threshold=0.0, direction=">",
        deflated_metric="sharpe_deflated", deflated_threshold=0.95,
        cand_sharpe=1.4, cand_n_returns=120, cand_skew=0.1, cand_kurt_raw=3.5)
    with transaction() as con:
        row = dict(con.execute(
            "SELECT cost_model, selection_fingerprint FROM hypotheses WHERE id=?", (hid,)).fetchone())
    assert row["cost_model"] == "v3"
    assert row["selection_fingerprint"] == fp


def test_seal_covers_selection_fingerprint(hyp_db):
    # _compute_seal must change if selection_fingerprint changes.
    import db.hypotheses as H
    base = {f: None for f in H._FROZEN_FIELDS}
    s1 = H._compute_seal({**base, "selection_fingerprint": "FP_A"})
    s2 = H._compute_seal({**base, "selection_fingerprint": "FP_B"})
    assert "selection_fingerprint" in H._FROZEN_FIELDS
    assert s1 != s2
```

- [ ] **Step 2: Run, confirm FAIL**: `python -m pytest tests/test_hypotheses_gate.py -k "auto_stamps_fingerprint or seal_covers_selection" -v`.

- [ ] **Step 3: Implement**:
  (a) Add `"selection_fingerprint"` to the `_FROZEN_FIELDS` tuple (append after `"drift_check_ref"`).
  (b) In `claim_hypothesis`, stamp `cost_model` + `selection_fingerprint` at insert. Add at module top: `from selection_provenance import selection_fingerprint` and use `active_cost_model_id` (already-needed import from backtest_costs). In `claim_hypothesis` before `_do`:

```python
    from selection_provenance import selection_fingerprint as _sel_fp
    from backtest_costs import active_cost_model_id
    fp, _ = _sel_fp()
    active_model, _h = active_cost_model_id()
```

  and extend the INSERT column list + values to include `cost_model` and `selection_fingerprint`:

```python
            cur = con.execute(
                "INSERT INTO hypotheses "
                "(created_ts, status, strategy_config_json, config_hash, symbols_json, "
                " window_label, metric, threshold, direction, deflated_metric, "
                " deflated_threshold, cand_sharpe, cand_n_returns, cand_skew, "
                " cand_kurt_raw, source_note, cost_model, selection_fingerprint) "
                "VALUES (?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (now, cfg_json, config_hash, json.dumps(symbols), window_label,
                 metric, threshold, direction, deflated_metric, deflated_threshold,
                 cand_sharpe, cand_n_returns, cand_skew, cand_kurt_raw, source_note,
                 active_model, fp),
            )
```

- [ ] **Step 4: Run, confirm PASS**: targeted `-k "auto_stamps_fingerprint or seal_covers_selection"`.

- [ ] **Step 5: Regression**: `python -m pytest tests/test_hypotheses_gate.py -q`. The existing lock/fire tests now stamp a real fingerprint at claim, seal it at lock, and check it at fire — they must still pass end-to-end (Tasks 8-9 add the cross-world guards; within a single test the claim/lock/fire all run under the same active fingerprint, so the guards pass).

- [ ] **Step 6: Commit**:
```bash
git add db/hypotheses.py tests/test_hypotheses_gate.py
git commit -m "feat(provenance): auto-stamp claim_hypothesis + selection_fingerprint is a FROZEN field"
```

---

### Task 8: hypotheses — lock criterion 4f + deflation pools by fingerprint (GUARDRAIL)

**Files:** Modify `db/hypotheses.py` (`_deflation_probability` `:240-263`, `lock_hypothesis` `:280-361`). Test: `tests/test_hypotheses_gate.py` (append).

- [ ] **Step 1: Write the failing tests** (append). Use the existing lockable-hypothesis helpers in this file (`_lockable`, `_register_matching_ok_trial`, etc. — reuse them; read the file to find their exact names/signatures):

```python
def test_lock_refuses_on_cost_model_drift(hyp_db, monkeypatch):
    # Claim under fingerprint A, then the active world drifts to B before lock -> refuse.
    import selection_provenance
    from db.hypotheses import lock_hypothesis, HypothesisLockError
    selection_provenance._clear_cache()
    hid = _lockable(hyp_db)   # helper that claims + registers provenance + attests refs
    # drift the active fingerprint AFTER claim/setup, BEFORE lock
    selection_provenance._clear_cache()
    monkeypatch.setattr(selection_provenance, "_DIGEST_VERSION", 999)  # changes active fp
    with pytest.raises(HypothesisLockError, match="cost-model|selection world|fingerprint"):
        lock_hypothesis(hid, today=_T())
```

(If `_lockable` doesn't exist, build the lockable hypothesis inline the way the existing `test_lock_*` tests do — read them. The point: a hypothesis whose frozen fingerprint != the active fingerprint at lock must raise.)

- [ ] **Step 2: Run, confirm FAIL**.

- [ ] **Step 3: Implement**:
  (a) `_deflation_probability(row, *, today)` — pass the hypothesis's fingerprint to the pool:

```python
    stats = selection_population_stats(study_type="exploratory",
                                       selection_fingerprint=row["selection_fingerprint"])
```

  (b) `lock_hypothesis` — add criterion 4f in the pre-read phase (after 4e complete-claim, before/with 4a provenance). After importing `from selection_provenance import selection_fingerprint`:

```python
    # 4f: cost-model / selection-world consistency. The candidate was selected
    # under the world stamped at claim; locking under a drifted world would freeze
    # a deflation computed against a population that no longer matches. Refuse.
    active_fp, _ = selection_fingerprint()
    if row["selection_fingerprint"] != active_fp:
        raise HypothesisLockError(
            f"selection-world drift: hypothesis frozen under "
            f"{row['selection_fingerprint']!r} but the active selection world is "
            f"{active_fp!r} — re-claim under the active cost-model before locking")
```

- [ ] **Step 4: Run, confirm PASS** + regression `python -m pytest tests/test_hypotheses_gate.py -q`.

- [ ] **Step 5: Commit**:
```bash
git add db/hypotheses.py tests/test_hypotheses_gate.py
git commit -m "feat(provenance): lock 4f cost-model consistency + deflation pools by fingerprint"
```

---

### Task 9: hypotheses — assert_fireable check 6 (hard refuse on fire) (GUARDRAIL)

**Files:** Modify `db/hypotheses.py` (`assert_fireable` `:409-435`). Test: `tests/test_hypotheses_gate.py` (append).

- [ ] **Step 1: Write the failing test** (append). Build a locked+authorized hypothesis (reuse the existing `_locked_and_authorized` helper if present), then drift the active fingerprint and assert fire is refused with the holdout untouched:

```python
def test_fire_refused_on_cost_model_mismatch(hyp_db, monkeypatch):
    import selection_provenance
    from db.hypotheses import assert_fireable
    from data.holdout_access import HoldoutFalsificationError
    selection_provenance._clear_cache()
    hid = _locked_and_authorized(hyp_db)   # helper: claim->lock->cooldown->authorize
    # the world drifts after authorization, before fire
    selection_provenance._clear_cache()
    monkeypatch.setattr(selection_provenance, "_DIGEST_VERSION", 999)
    with pytest.raises(HoldoutFalsificationError, match="selection world|cost-model|fingerprint"):
        assert_fireable(hid)
```

- [ ] **Step 2: Run, confirm FAIL**.

- [ ] **Step 3: Implement** — add check 6 to `assert_fireable`, after the seal check (check 4) and before/with the budget check (check 5), inside the `with transaction() as con:` block:

```python
        # 6: selection-world match. Firing under a different world than the one the
        # hypothesis was frozen under is re-selection, not falsification — and the
        # bala unica is irreversible. (Local import: keep db a leaf re: provenance.)
        from selection_provenance import selection_fingerprint
        active_fp, _ = selection_fingerprint()
        if row["selection_fingerprint"] != active_fp:
            raise HoldoutFalsificationError(
                f"hypothesis {hid} frozen under selection world "
                f"{row['selection_fingerprint']!r} but the active world is {active_fp!r} "
                "— firing now would be re-selection, not falsification; re-claim/re-lock")
```

- [ ] **Step 4: Run, confirm PASS** + full gate regression `python -m pytest tests/test_hypotheses_gate.py -q`.

- [ ] **Step 5: Commit**:
```bash
git add db/hypotheses.py tests/test_hypotheses_gate.py
git commit -m "feat(provenance): assert_fireable hard-refuses on selection-world mismatch (bala unica)"
```

---

### Task 10: scaffold growth + grep for un-stamped callers

**Files:** Modify `.mex/patterns/firing-the-holdout.md` + `.mex/patterns/registering-a-trial.md` (add provenance gotchas). Grep for direct trials/hypotheses INSERTs that bypass the auto-stamp.

- [ ] **Step 1: Grep for bypass risk**: `grep -rn "INSERT INTO trials\|INSERT INTO hypotheses" --include=*.py .` — confirm the ONLY production INSERTs are in `db/trials.py::claim_trial` and `db/hypotheses.py::claim_hypothesis` (which now auto-stamp). Any other prod INSERT path would write a NULL fingerprint → report it. (Test INSERTs are fine.)

- [ ] **Step 2: Add gotchas** to both patterns: trials/hypotheses now carry a `selection_fingerprint` (auto-stamped); the deflation N pools by it; the holdout gate hard-refuses to lock/fire across a selection-world drift; new world-coordinates go INSIDE `selection_provenance._build`, never as new columns. Point to the spec.

- [ ] **Step 3: Commit**:
```bash
git add .mex/patterns/firing-the-holdout.md .mex/patterns/registering-a-trial.md
git commit -m "docs(mex): provenance gotchas in holdout + trial patterns"
```

- [ ] **Step 4: mex log**:
```bash
mex log "selection-world provenance fingerprint implemented on feat/cost-model-provenance: trials+hypotheses stamp selection_fingerprint (cost-model+deflation digest), deflation N pools by it, holdout lock/fire hard-refuse cross-world drift. Voronov reframe: fingerprint not cost-model-column. Guardrail (holdout seal/trigger) — adversarial audit pending before push."
```

---

## MANDATORY before push — adversarial audit (guardrail)

This branch modifies the holdout gate's `_FROZEN_FIELDS` + seal + immutability trigger + fire-guard (the bala única). Per [[adversarial-audit-before-push-pattern]]: after Task 10, dispatch an independent adversarial audit (implementer separate from auditors, default-to-suspicion) covering: the seal genuinely covers `selection_fingerprint`; the recreated trigger blocks its mutation; lock 4f and fire check 6 cannot be bypassed; the v2 backfill hash is correct; no prod INSERT path writes a NULL fingerprint; the import structure is acyclic. Fix findings, THEN push.

---

## Self-Review

**Spec coverage:** §1 digest → Tasks 1-3. §2 trials (columns/auto-stamp/pool/backfill) → Tasks 4-5. §3 hypotheses (columns/frozen/seal/trigger/lock-4f/fire-6/deflation-pool) → Tasks 6-9. §4 invariant (one world per lifecycle) → Tasks 8-9. §5 migration (idempotent ALTER+backfill, draft-only for hyp) → Tasks 4, 6. §5 auto-stamp assumption → documented in Task 3 module docstring. §6 bala-única residual (code-SHA/OHLCV deferred) → spec §6/§9, the `_DIGEST_VERSION` extension point (Task 3). §7 guardrail/NN#3 → the MANDATORY-audit section + no holdout-access change. §8 testing → each task's tests. §9 scope-out (code-SHA/OHLCV/holdout-window as future digest ingredients) → Task 3 docstring + spec.

**Placeholder scan:** the Task 5 test note flags an unused `monkeypatch.setattr(..., raising=False)` line — the UPDATE-based control is the real one; the implementer removes the unused line (instruction is explicit, not a placeholder). Task 8/9 reuse existing lockable-helpers — instruction says read the file for exact names (the helpers exist per the v3 work); not a placeholder, a grounding instruction.

**Type consistency:** `selection_fingerprint() -> (str, dict)`, `_build(active_model, calibration_hash) -> (str, dict)`, `fingerprint_for_v2_sibling() -> str`, `active_cost_model_id() -> (str, str)`, `calibration_identity_hash(cal) -> str`, `selection_population_stats(*, study_type=, selection_fingerprint=)`, `_DIGEST_VERSION`, `_clear_cache()`, `deflation.ALGO_VERSION` — consistent across all tasks. Column names `cost_model` + `selection_fingerprint` consistent in both tables.
