# Read-Only Connection Enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close issues #456 + #462 (Voronov's Cluster B — read-only ontológico). Add `PRAGMA query_only=1` to `read_only_connection()` so write attempts raise `OperationalError` at runtime. Migrate `update_positions_json` (the canonical SELECT-inside-`transaction()` call site) to use `read_only_connection()`.

**Architecture:** Single invariant tightened — `db.transaction.read_only_connection()` enforces its contract (no INSERT/UPDATE/DELETE) at runtime via SQLite's `PRAGMA query_only=1`, not by docstring trust. One migration follows (`update_positions_json`) as proof that the primitive is consumable in real code. No new files; no operator changes; no decisions of product.

**Tech Stack:** Python 3, `sqlite3` stdlib, `pytest`.

---

## Context (read before starting)

Voronov pivoted away from Cluster A (observability of partial failure) toward Cluster B (read-only ontológico) with this reasoning:

> Cluster A produce dashboards. Cluster B produce un sistema que sabe lo que es. La observabilidad de un objeto mal-tipado produce métricas mal-tipadas. No se observa antes de ontologizar. Se ontologiza, y entonces la observabilidad es derivable.

Per Serrano's triage: Cluster B is **inseparable by evidence** — #462 says "migrate `update_positions_json` to `read_only_connection()` once #456 makes it safe". They are the same invariant: declaring at runtime that reads do not mutate.

The work is small, mechanical, and gates no product decision.

---

## Scope Check

One subsystem (`db.transaction` access primitives + one caller migration). Already a single cohesive plan.

---

## File Structure

### Modified files

- `db/transaction.py` — add `PRAGMA query_only=1` inside `read_only_connection()`. The yielded connection raises `OperationalError` on any write statement until exit.
- `tests/db/test_transaction.py` — add 3 invariant tests covering the new contract: rejects INSERT/UPDATE/DELETE; preserves SELECT; resets cleanly between calls (no leak across the connection pool).
- `api/positions.py` — `update_positions_json()` (lines 76-100) switches from `with transaction() as con:` to `with read_only_connection() as con:`. The only DB operation inside is `db_get_positions(con)` (pure SELECT). File I/O (`os.replace` of the JSON snapshot) stays where it is.
- `CLAUDE.md` — update "Database access" §4 to state that the read-only contract is now enforced at runtime, not just declared in the docstring.

### New files

None.

### Deleted files

None.

---

## Locked API Design

`read_only_connection()` final shape in `db/transaction.py`:

```python
@contextmanager
def read_only_connection() -> Iterator[sqlite3.Connection]:
    """Open a configured connection for read-only work outside any transaction.

    Use when an operator needs pre-validation reads (ownership check,
    existence check) that must NOT hold a writer lock. The connection
    closes on exit; no BEGIN/COMMIT is issued.

    Caller contract — ENFORCED AT RUNTIME via PRAGMA query_only=1:
    - MAY use con.execute for SELECT.
    - INSERT/UPDATE/DELETE raise sqlite3.OperationalError.
    - MUST NOT escape the connection past the `with` block (lifecycle).
    """
    con = _open_configured_connection()
    try:
        con.execute("PRAGMA query_only = 1")
        yield con
    finally:
        con.close()
```

The PRAGMA scope: SQLite's `query_only` is per-connection. Setting it after `_open_configured_connection()` and before yielding ensures every statement the caller issues on this connection is checked. The PRAGMA is reset implicitly when the connection closes — no global state pollution.

---

## Tasks

### Task 1: Verify branch and baseline

**Files:** none modified.

- [ ] **Step 1: Confirm branch and HEAD**

Run: `git rev-parse --abbrev-ref HEAD && git rev-parse HEAD`
Expected: `feat/read-only-connection-enforcement-456-462` and a commit at or descending from `353cfe4` (the #452 merge into main).

- [ ] **Step 2: Confirm clean working tree**

Run: `git status --short`
Expected: empty (the plan file is committed in the next step before Task 2 runs).

- [ ] **Step 3: Baseline test count**

Run: `pytest --collect-only -q 2>&1 | tail -3`
Expected: ~2502 tests collected. Note the exact number.

- [ ] **Step 4: Smoke check current `read_only_connection` behavior**

Run:
```bash
python -c "
from db.transaction import read_only_connection
with read_only_connection() as con:
    con.execute('SELECT 1')
print('SELECT ok (expected)')
"
```
Expected: `SELECT ok (expected)`.

Then verify the (currently un-enforced) write contract:
```bash
python -c "
import sqlite3
from db.transaction import read_only_connection
with read_only_connection() as con:
    con.execute('CREATE TABLE IF NOT EXISTS scratch (x INT)')
    con.execute('INSERT INTO scratch (x) VALUES (1)')
print('INSERT did not raise (this is the bug — Task 3 fixes it)')
"
```
Expected today: `INSERT did not raise (this is the bug — Task 3 fixes it)`. (Note the bug shape so you can confirm the fix in Task 3.)

If for some reason the INSERT does raise pre-Task-3, stop and report — the bug premise of #456 is wrong.

---

### Task 2: TDD — write failing tests for read-only enforcement

**Files:**
- Modify: `tests/db/test_transaction.py`

Append three new test functions to the existing `tests/db/test_transaction.py`. These tests will fail until Task 3 lands the PRAGMA.

- [ ] **Step 1: Read the existing test file**

Run: `head -20 tests/db/test_transaction.py`

Note the existing `fresh_db` fixture (it points `btc_api.DB_FILE` at a `tmp_path` DB and calls `init_db()`). The new tests reuse it via parametrization or per-test instantiation as appropriate. Confirm the fixture name and what it returns.

- [ ] **Step 2: Append the three new tests to `tests/db/test_transaction.py`**

Append (do NOT replace the existing 9 contract tests — these are 3 additional invariants):

```python
# ---- read_only_connection enforcement (issue #456) ----

def test_read_only_connection_rejects_insert(fresh_db):
    """PRAGMA query_only=1 must make INSERT raise OperationalError."""
    from db.transaction import read_only_connection, transaction

    # Setup: create a table via a normal write transaction.
    with transaction() as con:
        con.execute("CREATE TABLE ro_test (x INTEGER)")

    # The read-only connection must reject the INSERT.
    with pytest.raises(sqlite3.OperationalError, match="(?i)read-only|query_only|attempt to write"):
        with read_only_connection() as con:
            con.execute("INSERT INTO ro_test (x) VALUES (1)")


def test_read_only_connection_rejects_update(fresh_db):
    """PRAGMA query_only=1 must make UPDATE raise OperationalError."""
    from db.transaction import read_only_connection, transaction

    with transaction() as con:
        con.execute("CREATE TABLE ro_test (x INTEGER)")
        con.execute("INSERT INTO ro_test (x) VALUES (1)")

    with pytest.raises(sqlite3.OperationalError, match="(?i)read-only|query_only|attempt to write"):
        with read_only_connection() as con:
            con.execute("UPDATE ro_test SET x = 2 WHERE x = 1")


def test_read_only_connection_allows_select_and_does_not_leak_query_only(fresh_db):
    """SELECT must succeed under read_only_connection. After exit, a fresh
    write connection via transaction() must NOT inherit query_only — the
    PRAGMA is per-connection and the connection closes on exit."""
    from db.transaction import read_only_connection, transaction

    with transaction() as con:
        con.execute("CREATE TABLE ro_test (x INTEGER)")
        con.execute("INSERT INTO ro_test (x) VALUES (42)")

    # SELECT works through read_only_connection.
    with read_only_connection() as con:
        row = con.execute("SELECT x FROM ro_test").fetchone()
    assert row["x"] == 42

    # After the read-only block exits, a fresh transaction() must accept writes
    # (proves PRAGMA query_only does NOT leak across connections).
    with transaction() as con:
        con.execute("INSERT INTO ro_test (x) VALUES (99)")
    with transaction() as con:
        rows = con.execute("SELECT x FROM ro_test ORDER BY x").fetchall()
    assert [r["x"] for r in rows] == [42, 99]
```

- [ ] **Step 3: Verify the 3 new tests fail**

Run: `pytest tests/db/test_transaction.py::test_read_only_connection_rejects_insert tests/db/test_transaction.py::test_read_only_connection_rejects_update -v 2>&1 | tail -15`

Expected: both fail with `DID NOT RAISE <class 'sqlite3.OperationalError'>` (because the PRAGMA does not exist yet, so the writes silently succeed).

Run: `pytest tests/db/test_transaction.py::test_read_only_connection_allows_select_and_does_not_leak_query_only -v 2>&1 | tail -10`

Expected: passes today (no enforcement, no leak to test against yet) — that is acceptable. It will continue to pass after Task 3, which is the invariant.

- [ ] **Step 4: Confirm the existing 9 contract tests still pass**

Run: `pytest tests/db/test_transaction.py -v 2>&1 | tail -20`
Expected: 9 pass (the original contract tests) + 1 pass (the leak test) + 2 fail (the two write-rejection tests).

- [ ] **Step 5: Commit**

```bash
git add tests/db/test_transaction.py
git commit -m "test(db): add 3 failing invariant tests for read_only_connection enforcement (#456)

INSERT and UPDATE must raise sqlite3.OperationalError when called inside
read_only_connection(). SELECT must continue to work. PRAGMA query_only
must NOT leak across connections (per-connection scope verified)."
```

---

### Task 3: Implement — add PRAGMA query_only=1 to read_only_connection()

**Files:**
- Modify: `db/transaction.py`

- [ ] **Step 1: Read the current `read_only_connection`**

Run: `sed -n '85,105p' db/transaction.py` (line range may shift; locate `def read_only_connection`)

Confirm the current body is:

```python
@contextmanager
def read_only_connection() -> Iterator[sqlite3.Connection]:
    """..."""
    con = _open_configured_connection()
    try:
        yield con
    finally:
        con.close()
```

- [ ] **Step 2: Add the PRAGMA + update the docstring**

Replace the function body with:

```python
@contextmanager
def read_only_connection() -> Iterator[sqlite3.Connection]:
    """Open a configured connection for read-only work outside any transaction.

    Use when an operator needs pre-validation reads (ownership check,
    existence check) that must NOT hold a writer lock. The connection
    closes on exit; no BEGIN/COMMIT is issued.

    Caller contract — ENFORCED AT RUNTIME via PRAGMA query_only=1:
    - MAY use con.execute for SELECT.
    - INSERT/UPDATE/DELETE raise sqlite3.OperationalError.
    - MUST NOT escape the connection past the `with` block (lifecycle).
    """
    con = _open_configured_connection()
    try:
        con.execute("PRAGMA query_only = 1")
        yield con
    finally:
        con.close()
```

- [ ] **Step 3: Run the 3 new tests**

Run: `pytest tests/db/test_transaction.py::test_read_only_connection_rejects_insert tests/db/test_transaction.py::test_read_only_connection_rejects_update tests/db/test_transaction.py::test_read_only_connection_allows_select_and_does_not_leak_query_only -v 2>&1 | tail -15`

Expected: 3/3 pass.

If a test fails with a different error message than the regex `(?i)read-only|query_only|attempt to write`, inspect the actual SQLite error string and adjust the regex in the test. SQLite's error wording can vary across versions; widen the regex to match.

- [ ] **Step 4: Run all 12 contract tests for `transaction.py`**

Run: `pytest tests/db/test_transaction.py -v 2>&1 | tail -15`
Expected: 12/12 pass (9 original + 3 new).

- [ ] **Step 5: Run all PositionClosure invariant tests (sanity — operator uses `read_only_connection` in `__enter__`)**

Run: `pytest tests/operators/test_position_closure.py -v 2>&1 | tail -15`
Expected: 11/11 pass. The operator's `__enter__` only does a SELECT inside `read_only_connection()`; the PRAGMA does not break it.

- [ ] **Step 6: Commit**

```bash
git add db/transaction.py
git commit -m "feat(db): enforce read_only_connection contract at runtime via PRAGMA query_only=1 (closes #456)

INSERT/UPDATE/DELETE inside read_only_connection() now raise
sqlite3.OperationalError instead of silently mutating. Same class of
ambiguity as _tx_or_use (closed in #452), at smaller scale, now closed
structurally."
```

---

### Task 4: Migrate update_positions_json to read_only_connection

**Files:**
- Modify: `api/positions.py`

- [ ] **Step 1: Read the current `update_positions_json`**

Run: `sed -n '70,105p' api/positions.py` (line range may shift; locate `def update_positions_json`)

Confirm the current body uses `with transaction() as con:` and the only DB operation inside is `db_get_positions(con)` — a pure SELECT helper. If the function does anything else inside the transaction (any INSERT/UPDATE, any helper that mutates), STOP and report DONE_WITH_CONCERNS — the migration premise of #462 is wrong.

- [ ] **Step 2: Switch the context manager**

Find the import line at the top of `api/positions.py`:
```python
from db.transaction import transaction
```

Add `read_only_connection`:
```python
from db.transaction import transaction, read_only_connection
```

Then in `update_positions_json`, replace:
```python
with transaction() as con:
    all_pos = db_get_positions(con)
```

with:
```python
with read_only_connection() as con:
    all_pos = db_get_positions(con)
```

Leave all file I/O (`os.replace`, JSON serialization, the existing `try/except`) untouched. That logic stays outside the DB scope and was never the point of #462.

- [ ] **Step 3: Smoke-import**

Run: `python -c "from api.positions import update_positions_json; print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Run the positions test suite**

Run: `pytest tests/api/ tests/operators/ -v --tb=short 2>&1 | tail -20`
Expected: no regressions. `update_positions_json` is called from `PositionClosure.__exit__` after every successful close — the invariant tests #6 (each side-effect fires once) and #8 (no writer lock during side-effects) exercise this call path. Both must still pass.

- [ ] **Step 5: Verify no `transaction()` calls remain inside `update_positions_json`**

Run: `awk '/^def update_positions_json/,/^def [a-zA-Z]/' api/positions.py | grep -n "transaction\|read_only_connection"`
Expected: one occurrence of `read_only_connection` (the migration), zero of `transaction` inside the function body.

- [ ] **Step 6: Commit**

```bash
git add api/positions.py
git commit -m "refactor(api): migrate update_positions_json to read_only_connection (closes #462)

Previously the snapshot read held a writer lock via BEGIN IMMEDIATE
for a pure SELECT. Under bursts (multiple concurrent closes) this
multiplied lock acquisitions unnecessarily. Now uses read_only_connection
which acquires no writer lock and rejects writes at runtime (#456)."
```

---

### Task 5: Update CLAUDE.md §4 to reflect runtime enforcement

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Find the §4 section**

Run: `grep -n "Read-only pre-validation" CLAUDE.md`

This should locate the heading `### 4. Read-only pre-validation outside any transaction (\`read_only_connection()\`)`.

- [ ] **Step 2: Update the prose**

Read 6 lines after that heading. The current text says something like "Only used by operators today" without claiming runtime enforcement. Replace the section body with:

```markdown
### 4. Read-only pre-validation outside any transaction (`read_only_connection()`)

When an operator needs to read state BEFORE deciding whether to open a write transaction (e.g., ownership check that should not acquire a writer lock), use `read_only_connection()` from `db.transaction`:

```python
from db.transaction import read_only_connection

with read_only_connection() as con:
    row = db_get_position_by_id(con, pos_id)
# no transaction was opened; no lock held.
```

The contract is **enforced at runtime** via `PRAGMA query_only=1`: any INSERT/UPDATE/DELETE inside `read_only_connection()` raises `sqlite3.OperationalError`. Pure SQL helpers receive `con` from their caller; they never call `read_only_connection` themselves.

Used by `PositionClosure.__enter__` (pre-validation read) and by `update_positions_json` (snapshot generation). New call sites: prefer `read_only_connection` over `transaction` whenever the unit-of-work contains zero writes.
```

(Use the four-backtick wrapper in the actual edit only if your CLAUDE.md style already uses it; otherwise stay consistent with the file's existing fenced-code convention.)

- [ ] **Step 3: Verify the change reads cleanly**

Run: `sed -n '/### 4. Read-only/,/^###/p' CLAUDE.md | head -25`
Expected: section reads cleanly, contract clause explicitly says "enforced at runtime".

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md §4 declares read_only_connection contract is runtime-enforced (#456 #462)"
```

---

### Task 6: Final verification

**Files:** none modified (unless smoke surfaces a fix).

- [ ] **Step 1: All transaction contract tests**

Run: `pytest tests/db/test_transaction.py -v 2>&1 | tail -15`
Expected: 12/12 pass.

- [ ] **Step 2: All PositionClosure invariant tests**

Run: `pytest tests/operators/test_position_closure.py -v 2>&1 | tail -15`
Expected: 11/11 pass.

- [ ] **Step 3: Atomicity regression test**

Run: `pytest tests/api/test_check_position_stops_atomicity.py -v 2>&1 | tail -10`
Expected: PASS.

- [ ] **Step 4: Full suite**

Run: `pytest tests/ --tb=no -q -p no:cacheprovider 2>/dev/null | tail -5`
Expected: baseline (~2489 passed, 22 skipped) plus 3 new tests = ~2492 passed. The known pre-existing flake in `tests/test_setup.py` may or may not fail in this run; it is not caused by this PR.

- [ ] **Step 5: Grep verification — no surviving SELECT-in-transaction in `update_positions_json`**

Run: `grep -B2 -A8 "def update_positions_json" api/positions.py`
Expected: function body uses `read_only_connection`, not `transaction`.

- [ ] **Step 6: Smoke import all touched modules**

Run:
```bash
python -c "
import importlib
for m in ('db.transaction', 'api.positions', 'operators.position_closure'):
    importlib.import_module(m)
print('all 3 modules import cleanly')
"
```
Expected: `all 3 modules import cleanly`.

---

### Task 7: Push, open PR, close #456 and #462 (REQUIRES USER CONFIRMATION)

**Files:** none modified.

This step is externally visible to `sssimon/trading-spacial`. Confirm before executing in an autonomous session.

- [ ] **Step 1: Push the branch**

Run: `git push -u origin feat/read-only-connection-enforcement-456-462`
Expected: branch published; URL printed.

- [ ] **Step 2: Open the PR**

Run:
```bash
gh pr create --repo sssimon/trading-spacial \
  --title "feat(db): enforce read_only_connection contract at runtime + migrate update_positions_json (closes #456 #462)" \
  --body "$(cat <<'EOF'
## Summary

Voronov's Cluster B (read-only ontológico). Two issues inseparable by evidence; closed together.

- **#456**: \`read_only_connection()\` now enforces its contract at runtime via \`PRAGMA query_only=1\`. INSERT/UPDATE/DELETE raise \`sqlite3.OperationalError\` instead of silently mutating. Closes the same class of ambiguity that \`_tx_or_use\` had (closed in #452), at smaller scale.

- **#462**: \`update_positions_json\` migrated from \`transaction()\` to \`read_only_connection()\`. The snapshot read was a pure SELECT but held \`BEGIN IMMEDIATE\` unnecessarily; under bursts (concurrent \`PositionClosure.__exit__\` snapshots) this multiplied writer-lock acquisitions for read-only work.

## Why this PR instead of Cluster A (observability)

Per Voronov's reframe (analysis in commit log of branch): \"La observabilidad de un objeto mal-tipado produce métricas mal-tipadas. No se observa antes de ontologizar.\" Closing this invariant first makes the future Cluster A (tick observability) work against a system that knows what each access pattern is, rather than building dashboards over an ambiguous abstraction.

## Resolves

- #456 — read_only_connection runtime enforcement
- #462 — update_positions_json migration

## Test plan

- [x] 3 new invariant tests in \`tests/db/test_transaction.py\`:
  - \`test_read_only_connection_rejects_insert\`
  - \`test_read_only_connection_rejects_update\`
  - \`test_read_only_connection_allows_select_and_does_not_leak_query_only\` (PRAGMA is per-connection)
- [x] 9 existing transaction contract tests still pass.
- [x] 11 PositionClosure invariant tests still pass (operator's \`__enter__\` uses \`read_only_connection\`).
- [x] Full suite: baseline + 3 new tests, no regressions.

## Architecture note

CLAUDE.md §4 updated to declare the contract is runtime-enforced, not docstring-only. New DB call sites should prefer \`read_only_connection\` over \`transaction\` whenever the unit-of-work contains zero writes.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
Expected: PR URL printed.

- [ ] **Step 3: Close #456 and #462 with cross-link**

Run:
```bash
NEW_PR=$(gh pr view --json number --jq .number)
gh issue comment 456 --repo sssimon/trading-spacial --body "Closed by #$NEW_PR: read_only_connection now sets PRAGMA query_only=1; runtime rejection of writes verified by 2 new invariant tests."
gh issue close 456 --repo sssimon/trading-spacial

gh issue comment 462 --repo sssimon/trading-spacial --body "Closed by #$NEW_PR: update_positions_json migrated to read_only_connection. Pure SELECT no longer holds BEGIN IMMEDIATE."
gh issue close 462 --repo sssimon/trading-spacial
```

- [ ] **Step 4: Done**

The plan is fully executed when this step completes.

---

## Self-Review

**Spec coverage:**
- #456 (PRAGMA query_only runtime enforcement): Task 2 writes failing tests; Task 3 implements; Task 6 verifies.
- #462 (update_positions_json migration): Task 4 migrates; Task 6 verifies via grep + position invariant tests #6/#8.
- Voronov's "define before measure": this PR closes a definition before any future observability PR builds on it.

**Placeholder scan:** None of the disallowed patterns present. All code blocks complete. Migration target is a single function with a single SELECT inside; no ambiguity.

**Type consistency:**
- `read_only_connection() -> Iterator[sqlite3.Connection]` signature unchanged (only body changes).
- `update_positions_json()` signature unchanged.
- Test fixture `fresh_db` reused from existing test file (no new fixtures introduced).

**Caveats:**
- SQLite's error message wording for `query_only` rejection varies across versions (commonly: `"attempt to write a readonly database"` or `"cannot modify <table> -- it is read-only"`). The test regex `(?i)read-only|query_only|attempt to write` covers the common variants. If a CI environment uses a version whose message escapes the regex, widen at that point — do not silently relax to no regex.
- `update_positions_json` has its own internal `try/except Exception: log.warning` (acknowledged in #462's parent issue #455 as F-NEW-10 — that other ticket addresses the swallowed-error observability gap separately). This PR does NOT touch that internal try/except; it only switches the connection primitive.
