# A.4-1 Phase 3 + Phase 4 — Pre-holdout ATR Re-tune Execution Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute the pre-holdout re-tune of the 30 ATR multipliers (10 symbols × {atr_sl_mult, atr_tp_mult, atr_be_mult}) on PR #287 branch, produce a byte-deterministic artefact under `data/retune/2026-05-04-pre-holdout/`, commit on the existing draft PR, and shepherd the PR through the multi-agent R1 + R2 review loop until criticals = 0 and Sam authorizes merge.

**Architecture:** Two-phase. Phase 3 runs `tools/retune_pre_holdout.py --max-date 2025-04-30` against the production `data/ohlcv.db` (NOT the holdout dataset — verified: harness is whitelisted for the `-pre-holdout` token in its output dir, but reads no holdout data). Wrapper invokes `auto_tune.optimize_symbol(..., cutoff=cutoff)` per symbol via a `ProcessPoolExecutor`, with `auto_tune._slice_below_cutoff` enforcing strict `<` slicing on every OHLCV / F&G / funding frame. Three artefacts emitted: `params.json` (sort_keys-stable, the only byte-gated artefact), `manifest.json` (run-time + hashes + per-(symbol, tf) MIN/MAX timestamp ranges as no-leakage proof), `report.md` (current vs re-tuned table, grid-edge convergence, runtime). Phase 4 runs the multi-agent review loop already proven across PR1/PR2/PR3 of epic #294.

**Tech Stack:** Python 3.x, pandas, sqlite3, `auto_tune.py` (existing grid + walk-forward), `tools/retune_pre_holdout.py` (Phase 2 wrapper), `pytest`, `gh` CLI, `git` (rebase + commit). No new dependencies. No code on the holdout reading path.

---

## Pre-execution Decision Point — Sam authorization required

**Branch is 12 commits behind main**, including #296 (per-symbol `time_limit_hours`), #297 (per-symbol `max_participation_rate`), #299 (per-symbol `cooldown_hours`). Post-rebase, `config.defaults.json` carries **6 keys per symbol**, but `tools/retune_pre_holdout.py:_build_params_block` only emits the 3 `atr_*` keys (verified at `tools/retune_pre_holdout.py:178-209`). This means the resulting `params.json` is **NOT a complete drop-in** for `symbol_overrides` — promoting it (Phase 5) would silently strip the 30 locked TL/cap/cooldown values committed ex-ante in D9 §4.1.

**Two options:**

- **Option A (recommended) — Pass-through fix in Phase 3.** Modify `_build_params_block` so KEEP/CHANGE paths preserve every key from the current override, only overwriting the 3 `atr_*` keys when CHANGE. Adds 2 unit tests. Net effect: `params.json` becomes genuinely drop-in safe; the 30 locked values pass through verbatim; **no new tuning DOFs**. Aligns with kickoff "Solo los 30 ATR multipliers son re-tuned. Nada más." (the values being passed through are not being re-tuned, they're being preserved).
- **Option B — Defer schema fix to Phase 5 promotion PR.** Document `params.json` as an "atr-only sub-block" in the wrapper docstring + `report.md`. Phase 5 promotion procedure manually merges atr_* keys into the live `symbol_overrides` instead of `dict.update`. Smaller Phase 3 diff, but pushes a load-bearing risk to Phase 5.

**Plan body assumes Option A.** Tasks 4-5 implement it; if Sam picks Option B, drop tasks 4-5 and replace with a docstring update + a `report.md` callout.

**Secondary doc-coordination note:** `docs/superpowers/specs/es/2026-05-03-asunciones-tecnicas-pre-holdout.md` §2.9 says *"NO re-tunear (#272 deferred per Sam, A.4-1 retune harness #287 stays open draft)"*. The kickoff supersedes that — caveat #1 in CLAUDE.md ("Re-tune required") is the authoritative path. The §2.9 wording will need a follow-up edit acknowledging the re-tune ran, but **that edit is OUT OF SCOPE for this PR** (it's a chore-spec PR, not a Phase 3 commit). Flag for reviewer to track post-merge.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `tools/retune_pre_holdout.py` | Modify (Option A only) | `_build_params_block` preserves all keys, only overwriting `atr_*` on CHANGE |
| `tests/test_auto_tune_max_date.py` | Modify (Option A only) | +2 tests covering pass-through of TL/cap/cooldown keys |
| `data/retune/2026-05-04-pre-holdout/params.json` | Create | 30 ATR values (+ pass-through locked values under Option A); `sort_keys=True`, atomic write |
| `data/retune/2026-05-04-pre-holdout/manifest.json` | Create | Cutoff, code commit SHA, ohlcv.db sha256, seed, per-(symbol, tf) MIN/MAX timestamp ranges, leakage check = PASS |
| `data/retune/2026-05-04-pre-holdout/report.md` | Create | Side-by-side current vs re-tuned, grid-edge convergence, JUP warmup caveat, data ranges table |
| `data/retune/2026-05-01-pre-holdout/` | Delete | Stale Run-1 artefact (PRE-#299, descartá per kickoff) |
| `data/retune/2026-05-04-pre-holdout/params.json` (re-run) | Verify byte-identical | Reproducibility gate; diff must be empty |
| `git commit` on `feat/methodology-a4-1-retune-pre-holdout` | Create | Single Phase 3 commit; PR #287 stays DRAFT until R1+R2 → Sam |

**No changes to** `auto_tune.py`, `btc_scanner.py`, `backtest.py`, `config.json`, `config.defaults.json`, `tests/test_holdout_isolation.py` (Guard B already whitelists `tools/retune_pre_holdout.py`), or anything reading `data/holdout/`.

---

## Phase 3 — Tune execution

### Task 1: Rebase branch onto origin/main

**Files:**
- Modify (rebase resolution likely required): `tools/retune_pre_holdout.py`, `tests/test_auto_tune_max_date.py`, `tests/test_holdout_isolation.py`, `auto_tune.py`, `config.defaults.json`, `tools/__init__.py`

Branch is 12 commits behind main. 6 conflict-candidate files (touched by both branch and incoming main commits). The branch's `tools/retune_pre_holdout.py` and `tests/test_auto_tune_max_date.py` are net-new on the branch (no conflicts expected on the file content itself, but their presence on top of main's diff in the same files needs verification). `tools/__init__.py` is empty on both sides — trivial. `auto_tune.py` and `config.defaults.json` and `tests/test_holdout_isolation.py` are the substantive overlap.

- [ ] **Step 1: Confirm clean working tree**

```bash
git status
```

Expected: only untracked `.claude/` and `data/retune/` (stale Run-1). No staged or unstaged modifications.

- [ ] **Step 2: Snapshot current branch HEAD (rollback insurance)**

```bash
git rev-parse HEAD
git tag retune-phase3-pre-rebase-snapshot
```

Expected: prints SHA `4c5a50d…`, creates a local tag.

- [ ] **Step 3: Rebase onto origin/main**

```bash
git rebase origin/main
```

Expected outcomes:
- **Best case:** Clean rebase, no conflicts. Proceed to Step 5.
- **Likely case:** Conflicts in 1+ of the 6 conflict-candidate files. Stop rebase, do NOT auto-resolve, escalate to reviewer for inspection.

- [ ] **Step 4: If conflicts arise, document and pause**

If `git status` after Step 3 shows `Unmerged paths`:

```bash
git status
git diff --name-only --diff-filter=U
```

For each unmerged file, run `git diff <file>` and capture the conflict markers. Do NOT resolve unilaterally — surface to reviewer with:
- File + line numbers of conflict
- Branch's intent vs main's intent
- Proposed resolution

Common expected resolutions:
- `tests/test_holdout_isolation.py`: branch added the `tools/retune_pre_holdout.py` line to `HOLDOUT_LEGITIMATE_MODULES`. Main may have also touched the whitelist (#296/#297 added other entries? — verify via `git log origin/main -- tests/test_holdout_isolation.py`). Resolution: union of both whitelist additions; preserve docstring comments on each entry.
- `auto_tune.py`: branch added `--max-date` flag, `_slice_below_cutoff`, `cutoff=` propagation, `initialize_seed` config knob. Main may have touched `optimize_symbol` for cost-aware metrics (#289). Resolution: keep all branch additions; integrate with main's signature changes if any.
- `config.defaults.json`: branch added `auto_tune.seed` knob. Main added `time_limit_hours` / `max_participation_rate` / `cooldown_hours` per symbol. These additions are at different JSON paths — should merge cleanly without semantic conflict.

If a non-trivial conflict arises, abort the rebase (`git rebase --abort`) and STOP. Report to reviewer.

- [ ] **Step 5: Push rebased branch (force-with-lease)**

```bash
git push --force-with-lease origin feat/methodology-a4-1-retune-pre-holdout
```

`--force-with-lease` (NOT `--force`) refuses to overwrite remote if it has commits we don't have locally. Safe variant.

**This step requires Sam authorization.** Do NOT execute Step 5 without an explicit "dale" from Sam — force push is a shared-state action even on a draft PR branch.

- [ ] **Step 6: Commit (NO commit — rebase is not a commit)**

Skipped. The rebase produces no new commit; only re-applies existing branch commits onto main.

---

### Task 2: Verify Phase 2 tests + holdout guards still pass post-rebase

**Files:**
- Test: `tests/test_auto_tune_max_date.py`, `tests/test_holdout_isolation.py`

- [ ] **Step 1: Run Phase 2 unit + E2E tests**

```bash
pytest tests/test_auto_tune_max_date.py -v
```

Expected: all tests pass. The E2E test `TestEndToEndWithRealOhlcv::test_optimize_symbol_with_cutoff_does_not_leak` will run if `data/ohlcv.db` exists (skip otherwise). Look for any post-rebase signature drift — e.g., if `optimize_symbol` gained a new keyword arg in main, the test stub may need updating.

If any test fails, STOP. Report to reviewer with full failure output. Do NOT proceed to tune execution.

- [ ] **Step 2: Run Guard B AST scanner**

```bash
pytest tests/test_holdout_isolation.py -v
```

Expected: all tests pass, including `test_no_holdout_references_in_non_whitelisted_modules`. If any new file added by main commits (#296/#297/#299 etc.) introduces a holdout reference outside the whitelist, this fails. STOP and report.

- [ ] **Step 3: Run full test suite (sanity)**

```bash
pytest tests/ -v --ignore=tests/test_backtest_smoke_cooldown.py --ignore=tests/test_backtest_smoke_sizing_cap.py --ignore=tests/test_backtest_smoke_time_limit.py
```

Expected: 1254+ tests pass (post-#299 baseline per kickoff). Smokes are excluded here because they each take 6-7 min — they run in Task 7 separately. **This is the only place where `--ignore` is permitted, and only for the three smoke files explicitly listed.**

If any non-smoke test fails, STOP and triage. Likely causes: post-rebase signature drift in `auto_tune.py`, schema drift in `config.defaults.json` test fixtures, or a flaky test surfaced by the rebase.

- [ ] **Step 4: Commit**

No commit — these are verification-only steps, no file modifications.

---

### Task 3: Delete stale Run-1 artefact

**Files:**
- Delete: `data/retune/2026-05-01-pre-holdout/`

The kickoff explicitly notes this directory is PRE-#299 (it predates the cooldown auto-enforce merge) and must be discarded.

- [ ] **Step 1: Verify the directory contents before deleting (defensive)**

```bash
ls -la data/retune/2026-05-01-pre-holdout/
```

Expected: 3 files (`params.json`, `manifest.json`, `report.md`). If unexpected contents (e.g., a draft file Sam was inspecting), stop and ask before deleting.

- [ ] **Step 2: Delete**

```bash
rm -rf data/retune/2026-05-01-pre-holdout/
```

- [ ] **Step 3: Verify deletion**

```bash
ls data/retune/ 2>&1 || true
```

Expected: empty (or "no such file or directory" if the parent is now empty).

- [ ] **Step 4: Commit**

No commit at this step — the parent `data/retune/` was already untracked, so the deletion has no git effect. The new artefact (Task 6) will be the first thing committed under `data/retune/`.

---

### Task 4 (Option A only): Fix `_build_params_block` to pass through locked keys

**Files:**
- Modify: `tools/retune_pre_holdout.py:165-210` (function `_build_params_block`)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_auto_tune_max_date.py` inside `class TestBuildParamsBlock`:

```python
    def test_passes_through_time_limit_and_cap_and_cooldown_on_change(self):
        from tools.retune_pre_holdout import _build_params_block

        results = [self._result(
            "BTC", "CHANGE",
            {"atr_sl_mult": 1.5, "atr_tp_mult": 5.0, "atr_be_mult": 2.0},
        )]
        current = {"BTC": {
            "atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.5,
            "time_limit_hours": 14,
            "max_participation_rate": 0.010,
            "cooldown_hours": 14,
        }}
        out = _build_params_block(results, current)
        assert out == {"BTC": {
            "atr_sl_mult": 1.5, "atr_tp_mult": 5.0, "atr_be_mult": 2.0,
            "time_limit_hours": 14,
            "max_participation_rate": 0.010,
            "cooldown_hours": 14,
        }}, "CHANGE path must overwrite atr_* and pass-through locked keys"

    def test_passes_through_locked_keys_on_keep(self):
        from tools.retune_pre_holdout import _build_params_block

        results = [self._result("BTC", "KEEP")]
        current = {"BTC": {
            "atr_sl_mult": 1.0, "atr_tp_mult": 4.0, "atr_be_mult": 1.5,
            "time_limit_hours": 14,
            "max_participation_rate": 0.010,
            "cooldown_hours": 14,
        }}
        out = _build_params_block(results, current)
        assert out == current["BTC"] | {}, "KEEP path must preserve every key verbatim"
        # Defensive: identity of nested dict must NOT be the same object
        # (avoid leaking a reference that downstream callers could mutate).
        assert out["BTC"] is not current["BTC"]
```

- [ ] **Step 2: Run the new tests to verify they fail**

```bash
pytest tests/test_auto_tune_max_date.py::TestBuildParamsBlock::test_passes_through_time_limit_and_cap_and_cooldown_on_change tests/test_auto_tune_max_date.py::TestBuildParamsBlock::test_passes_through_locked_keys_on_keep -v
```

Expected: FAIL — the current implementation only emits 3 atr_* keys; assertion on TL/cap/cooldown presence fails.

- [ ] **Step 3: Patch `_build_params_block`**

Replace the body of `_build_params_block` in `tools/retune_pre_holdout.py:165-210` with:

```python
def _build_params_block(results: list, current_overrides: dict) -> dict:
    """Build the new ``symbol_overrides`` block from re-tune results.

    Output preserves every key of the current override verbatim; only the
    three ``atr_*`` keys are overwritten when the optimizer recommended
    CHANGE. Locked structural-fix values (``time_limit_hours``,
    ``max_participation_rate``, ``cooldown_hours``) pre-registered in
    D9 §4.1 pass through unchanged so that ``params.json`` remains a
    complete drop-in for ``config["symbol_overrides"]``.

    KEEP / NO_DATA / ERROR → preserve current overrides verbatim (deep copy).
    CHANGE without ``proposed_params`` → treated as KEEP (defensive).
    """
    out: dict = {}
    for r in results:
        sym = r["symbol"]
        cur = current_overrides.get(sym)

        if cur is False:
            out[sym] = False
            continue
        if not isinstance(cur, dict):
            raise ValueError(
                f"_build_params_block: symbol {sym!r} is in the active portfolio "
                f"(recommendation={r.get('recommendation')!r}) but has no flat "
                f"override entry in current config (got {cur!r}). Refusing to "
                f"emit a partial params.json with None values — fix the input "
                f"config or extend this helper to handle non-flat shapes."
            )
        missing = [k for k in ("atr_sl_mult", "atr_tp_mult", "atr_be_mult") if k not in cur]
        if missing:
            raise ValueError(
                f"_build_params_block: symbol {sym!r} current override is "
                f"missing required keys {missing}. params.json must be a "
                f"complete drop-in; refusing to emit None placeholders."
            )

        merged = copy.deepcopy(cur)
        if r.get("recommendation") == "CHANGE" and r.get("proposed_params"):
            pp = r["proposed_params"]
            merged["atr_sl_mult"] = pp["atr_sl_mult"]
            merged["atr_tp_mult"] = pp["atr_tp_mult"]
            merged["atr_be_mult"] = pp["atr_be_mult"]
        out[sym] = merged
    return out
```

The `copy.deepcopy(cur)` ensures the output dict has no shared mutable references with the input `current_overrides` (prevents downstream mutation surprises, hence the second test). `copy` is already imported at `tools/retune_pre_holdout.py:34`.

- [ ] **Step 4: Run the failing tests to verify they now pass**

```bash
pytest tests/test_auto_tune_max_date.py::TestBuildParamsBlock -v
```

Expected: PASS for all 7 tests in `TestBuildParamsBlock` (5 pre-existing + 2 new).

- [ ] **Step 5: Run the full test file to verify no regression in adjacent tests**

```bash
pytest tests/test_auto_tune_max_date.py -v
```

Expected: every test passes. If any pre-existing test now fails, the deepcopy or merge logic has a bug — investigate before proceeding.

- [ ] **Step 6: Commit**

```bash
git add tools/retune_pre_holdout.py tests/test_auto_tune_max_date.py
git commit -m "$(cat <<'EOF'
fix(methodology): A.4-1 _build_params_block preserves locked TL/cap/cooldown keys

post-#296/#297/#299, current symbol_overrides carry six keys per symbol
(atr_sl_mult/tp/be + time_limit_hours + max_participation_rate +
cooldown_hours). The previous KEEP/CHANGE paths emitted only the three
atr_* keys, so promoting params.json to config.json would silently
strip the 30 locked structural-fix values pre-registered in D9 §4.1.

CHANGE path now overwrites atr_* and deepcopies the rest from the
current override; KEEP path is a deepcopy. Output is a complete drop-in
for symbol_overrides without re-tuning anything beyond the 30 atr_*
values (locked values pass through unchanged, so no new DOFs).

+2 tests in TestBuildParamsBlock covering CHANGE pass-through and KEEP
deepcopy identity.

EOF
)"
```

---

### Task 5 (Option A only): Run full test suite + holdout guards on the patched harness

**Files:**
- Test: all of `tests/`

- [ ] **Step 1: Re-run full suite excluding smokes**

```bash
pytest tests/ -v --ignore=tests/test_backtest_smoke_cooldown.py --ignore=tests/test_backtest_smoke_sizing_cap.py --ignore=tests/test_backtest_smoke_time_limit.py
```

Expected: 1256+ tests pass (1254 baseline + 2 new in Task 4).

- [ ] **Step 2: Re-run Guard B**

```bash
pytest tests/test_holdout_isolation.py -v
```

Expected: pass. The Task 4 patch did not introduce any holdout reference; this is a defensive double-check.

- [ ] **Step 3: Commit**

No commit — verification only.

---

### Task 6: Execute the tune (Run 1)

**Files:**
- Create (via the wrapper): `data/retune/2026-05-04-pre-holdout/{params.json,manifest.json,report.md}`

**This step requires Sam's "dale" before execution.** Reviewer surfaces the plan + the Option A vs B decision; on Sam's authorization for the tune, proceed.

Cutoff is `2025-04-30T00:00:00 UTC` (strict `<` slicing — drops the cutoff bar itself; verified at `auto_tune.py:168`).

- [ ] **Step 1: Confirm working tree clean and on branch HEAD**

```bash
git status
git rev-parse HEAD
git rev-parse origin/feat/methodology-a4-1-retune-pre-holdout
```

Expected: clean tree (no unstaged changes; the `data/retune/` dir is empty after Task 3); local HEAD == remote HEAD post-rebase + Task 4 commit + push.

- [ ] **Step 2: Confirm `data/ohlcv.db` exists and is the production cache**

```bash
ls -la data/ohlcv.db
sha256sum data/ohlcv.db
```

Expected: file exists, ~few hundred MB. Capture the sha256 — it must match what the manifest records in Step 4. If `sha256sum` is not available on macOS, use `shasum -a 256 data/ohlcv.db`.

- [ ] **Step 3: Run the wrapper (Run 1)**

```bash
python -m tools.retune_pre_holdout --max-date 2025-04-30 2>&1 | tee /tmp/retune_run1.log
```

Expected: ~tens of minutes runtime depending on `os.cpu_count()` and dataset size (the wrapper parallelizes across symbols). Log lines per symbol show recommendation (CHANGE / KEEP / NO_DATA / ERROR). Final lines: `Leakage check: PASS`, then `Artefacts written to data/retune/2026-05-04-pre-holdout/`.

If the wrapper exits non-zero, STOP. Capture the full stderr; do NOT iterate or rerun blindly. Report to reviewer.

If `Leakage check: FAIL` (any symbol/tf has `max_ts_ms >= cutoff_ms`), STOP — this is a BLOCKER that means the slicing logic or the OHLCV DB has post-cutoff bars that escaped the strict `<` filter. Report immediately.

- [ ] **Step 4: Inspect the artefact directory**

```bash
ls -la data/retune/2026-05-04-pre-holdout/
```

Expected: `params.json`, `manifest.json`, `report.md` (3 files).

- [ ] **Step 5: Commit**

No commit yet — Run 1 alone is not the final artefact. Run 2 (Task 7) is the byte-identical reproducibility proof; commit happens in Task 9 after both runs and the manifest gate pass.

---

### Task 7: Execute the tune again (Run 2 — reproducibility check)

**Files:**
- Create (in temp): `/tmp/retune-run2/{params.json,manifest.json,report.md}` (NOT under `data/retune/`)

The reproducibility gate is: `params.json` byte-identical across two runs with same cutoff + same code commit + same OHLCV DB. `manifest.json` is NOT byte-identical by design (`ran_at_iso`, `runtime_seconds` differ).

- [ ] **Step 1: Run the wrapper into a temp directory**

```bash
python -m tools.retune_pre_holdout --max-date 2025-04-30 --out-dir /tmp/retune-run2 2>&1 | tee /tmp/retune_run2.log
```

Expected: completes successfully, same `Leakage check: PASS`, same per-symbol recommendations.

- [ ] **Step 2: Diff `params.json` byte-for-byte**

```bash
diff data/retune/2026-05-04-pre-holdout/params.json /tmp/retune-run2/params.json
```

Expected: empty output (exit code 0). If diff is non-empty, STOP. The wrapper has a non-determinism source (likely an unseeded RNG entering the grid search — but Phase 2 already guards against that). Report immediately.

- [ ] **Step 3: Diff `manifest.json` for expected-only differences**

```bash
diff <(python3 -c "import json; m=json.load(open('data/retune/2026-05-04-pre-holdout/manifest.json')); m.pop('ran_at_iso',None); m.pop('runtime_seconds',None); print(json.dumps(m, sort_keys=True, indent=2))") \
     <(python3 -c "import json; m=json.load(open('/tmp/retune-run2/manifest.json')); m.pop('ran_at_iso',None); m.pop('runtime_seconds',None); print(json.dumps(m, sort_keys=True, indent=2))")
```

Expected: empty output. Strips `ran_at_iso` and `runtime_seconds` (which differ by design) and verifies everything else (cutoff, code commit, ohlcv sha256, seed, workers, leakage_check, symbols, per_symbol_data_ranges, scope_notes) is identical.

If any other field differs (e.g., `code_commit` — would mean someone amended/committed between runs), STOP. The reproducibility proof requires identical code state.

- [ ] **Step 4: Cleanup the temp dir**

```bash
rm -rf /tmp/retune-run2
```

- [ ] **Step 5: Commit**

No commit — verification only.

---

### Task 8: Manifest gate — verify no-leakage proof externally

**Files:**
- Read-only: `data/retune/2026-05-04-pre-holdout/manifest.json`, `data/ohlcv.db`

The wrapper's internal `_verify_no_leakage` already checks every per-(symbol, tf) MAX timestamp is `< cutoff_ms`. This task does an **independent** SQL query against `data/ohlcv.db` to triangulate.

- [ ] **Step 1: Run the cross-check SQL**

```bash
python3 <<'EOF'
import json, sqlite3, sys
from datetime import datetime, timezone

CUTOFF_ISO = "2025-04-30T00:00:00+00:00"
cutoff_ms = int(datetime.fromisoformat(CUTOFF_ISO).timestamp() * 1000)

with open("data/retune/2026-05-04-pre-holdout/manifest.json") as f:
    manifest = json.load(f)

con = sqlite3.connect("data/ohlcv.db")
problems = []
for sym, tfs in manifest["per_symbol_data_ranges"].items():
    for tf, span in tfs.items():
        # Independent query — bars at-or-after cutoff in production DB
        row = con.execute(
            "SELECT COUNT(*), MIN(open_time), MAX(open_time) "
            "FROM ohlcv WHERE symbol=? AND timeframe=? AND open_time>=?",
            (sym, tf, cutoff_ms),
        ).fetchone()
        n_post, min_post, max_post = row
        # Manifest-declared MAX must be strictly < cutoff
        if span["max_ts_ms"] is not None and span["max_ts_ms"] >= cutoff_ms:
            problems.append(f"{sym} {tf}: manifest max_ts_ms {span['max_ts_ms']} >= cutoff {cutoff_ms}")
        # Sanity: if production DB has post-cutoff bars, the wrapper saw them and excluded them — that's correct.
        # If production DB has zero post-cutoff bars (e.g., dataset hasn't been refreshed past holdout start),
        # the test is weaker but not wrong. Print a warning in that case.
        if n_post == 0:
            print(f"INFO  {sym} {tf}: production DB has no bars >= cutoff (weaker leakage test)", file=sys.stderr)

if problems:
    print("LEAKAGE GATE FAILED:", file=sys.stderr)
    for p in problems:
        print(f"  - {p}", file=sys.stderr)
    sys.exit(1)
print("LEAKAGE GATE PASS — manifest MAX timestamps strictly below cutoff for all (symbol, tf).")
EOF
```

Expected: prints `LEAKAGE GATE PASS`. INFO lines are advisory (not failures). If `LEAKAGE GATE FAILED`, STOP and report.

- [ ] **Step 2: Verify count of atr params in `params.json`**

```bash
python3 <<'EOF'
import json
with open("data/retune/2026-05-04-pre-holdout/params.json") as f:
    p = json.load(f)
ov = p["symbol_overrides"]
atr_count = 0
locked_count = 0
for sym, v in ov.items():
    if v is False:
        continue
    for k in ("atr_sl_mult", "atr_tp_mult", "atr_be_mult"):
        assert k in v, f"missing {k} in {sym}"
        atr_count += 1
    # Option A only: pass-through locked keys
    for k in ("time_limit_hours", "max_participation_rate", "cooldown_hours"):
        if k in v:
            locked_count += 1
print(f"atr params: {atr_count}  (expected 30)")
print(f"locked passthroughs: {locked_count}  (expected 30 under Option A, 0 under Option B)")
assert atr_count == 30, f"expected 30 atr params, got {atr_count}"
EOF
```

Expected: `atr params: 30`, `locked passthroughs: 30` (Option A) or `0` (Option B).

- [ ] **Step 3: Commit**

No commit — verification only.

---

### Task 9: Run long-form smokes

**Files:**
- Test: `tests/test_backtest_smoke_cooldown.py`, `tests/test_backtest_smoke_sizing_cap.py`, `tests/test_backtest_smoke_time_limit.py`

Each smoke runs ~6-7 min. Run all three sequentially. Per kickoff: NO `--ignore` blanket — these MUST pass before commit.

- [ ] **Step 1: Run smokes**

```bash
pytest tests/test_backtest_smoke_cooldown.py tests/test_backtest_smoke_sizing_cap.py tests/test_backtest_smoke_time_limit.py -v
```

Expected: all pass. Total runtime ~20 min. If any fails, STOP and triage. None of these tests touch `tools/retune_pre_holdout.py` or the artefact, so failure here would point to a rebase regression, not a Phase 3 bug.

- [ ] **Step 2: Commit**

No commit — verification only.

---

### Task 10: Commit Phase 3 artefact and push

**Files:**
- Add: `data/retune/2026-05-04-pre-holdout/params.json`, `data/retune/2026-05-04-pre-holdout/manifest.json`, `data/retune/2026-05-04-pre-holdout/report.md`

**This step requires Sam authorization** before push (force-with-lease was already done in Task 1; this is a normal push, but a draft-PR commit is still observable).

- [ ] **Step 1: Stage the artefact files explicitly (no `git add -A`)**

```bash
git add data/retune/2026-05-04-pre-holdout/params.json
git add data/retune/2026-05-04-pre-holdout/manifest.json
git add data/retune/2026-05-04-pre-holdout/report.md
git status
```

Expected: 3 new files staged, nothing else. Confirm `data/retune/2026-05-01-pre-holdout/` is gone (Task 3) and not staged.

- [ ] **Step 2: Compose the commit message body**

Pull the manifest summary and report performance summary into the commit body. Example skeleton (fill in actual numbers from the artefact):

```
feat(methodology): A.4-1 Phase 3 — re-tune ATR multipliers pre-holdout

Cutoff (--max-date): 2025-04-30T00:00:00+00:00 UTC
OHLCV DB sha256: <from manifest>
Code commit (in manifest): <SHA from manifest>
Seed: 42
Runtime: ~<seconds>s, workers=<n>
Leakage check: PASS (independent SQL cross-check confirmed)

params.json byte-hash (sha256): <compute>
- 30 atr_* values (10 symbols × {sl, tp, be})
- 30 locked pass-through values (10 symbols × {time_limit_hours, max_participation_rate, cooldown_hours})  [Option A]

Per-(symbol, tf) data ranges:
- 1d: <min> → <max>  (<rows> bars)
- 1h: <min> → <max>  (<rows> bars)
- 4h: <min> → <max>  (<rows> bars)
- 5m: <min> → <max>  (<rows> bars)
All MAX timestamps strictly < 2025-04-30T00:00:00 UTC.

Performance (NOT gated):
- CHANGE recommendations: <list of symbols>
- KEEP recommendations: <list>
- NO_DATA / ERROR: <list, if any>
- Notable val P&L deltas: <top movers>
- Grid-edge convergence: <symbols at boundary, if any>

Reproducibility verified: Run-2 to /tmp/retune-run2 produced byte-identical
params.json (diff empty). Manifest fields outside ran_at_iso/runtime_seconds
also identical.

References: #250 (A.4 epic), #287 (this PR — stays draft until R1+R2 pass)
```

- [ ] **Step 3: Commit with HEREDOC**

```bash
git commit -m "$(cat <<'EOF'
<paste filled-in body from Step 2>
EOF
)"
```

- [ ] **Step 4: Push**

```bash
git push origin feat/methodology-a4-1-retune-pre-holdout
```

Expected: push succeeds. `gh pr view 287 --json commits` should now show 4 commits on the branch (3 prior + this one).

- [ ] **Step 5: Update PR #287 body with Phase 3 summary**

```bash
gh pr edit 287 --body "$(cat <<'EOF'
<existing body content from Phase 2 PR description>

---

## Phase 3 update (2026-05-04)

Phase 3 commits the actual re-tune artefact under `data/retune/2026-05-04-pre-holdout/`.
Reproducibility, leakage, and test gates all green.

**Verification gate (all green):**
- Byte-identical `params.json` across two consecutive runs (diff empty)
- Manifest MAX(timestamp) strictly < `2025-04-30T00:00:00 UTC` for every (symbol, tf)
- Independent SQL cross-check confirmed no-leakage
- Full test suite passing (1256+ tests post-#299; 2 new in TestBuildParamsBlock under Option A)
- Long-form smokes passing (cooldown / sizing-cap / time-limit, ~20 min total)
- params.json covers 30 atr_* values exactly (10 symbols × {sl, tp, be})
- params.json passes through 30 locked structural-fix values (TL/cap/cooldown) [Option A]
- Performance reported in report.md (NOT gated)

**Out of scope (deferred):**
- Promotion to `config.json` — separate PR after A.4 holdout pass
- Per-direction tuning — A.4-1 option (b), separate ticket if/when needed
- D9 §2.9 doc update acknowledging the re-tune ran — separate chore-spec PR

## References
- #250 (A.4 epic)
- #246, #247 (holdout dataset lock)
EOF
)"
```

**Note:** PR body uses `## References` (NOT `Closes #N`) per kickoff. Closing the issue is Sam's call after Phase 4 + holdout evaluation.

- [ ] **Step 6: Verify PR shows expected file changes**

```bash
gh pr view 287 --json files,additions,deletions
```

Expected: `data/retune/2026-05-04-pre-holdout/{params,manifest,report}` files appear; total additions delta ≈ harness diff (Phase 2) + 3-key-passthrough patch (Task 4 if Option A) + the artefact JSON.

---

## Phase 4 — Multi-agent review iteration

The kickoff documents the R1 → fix → R2 → fix → loop pattern proven across PR1/PR2/PR3 of epic #294. **Reviewer drives this phase**, not the Dev agent. The Dev agent's role is: address criticals as the reviewer triages them, with verification (grep + cheap checks), no rubber-stamp self-claims.

### Task 11: Reviewer launches R1 (parallel multi-agent review)

**Operator:** Reviewer agent (separate from this Dev agent).

Reviewer dispatches in parallel via a single message containing 4 Agent tool calls:
- `code-reviewer` (general code-review)
- `silent-failure-hunter` (try/except / fallback / suppression patterns)
- `comment-analyzer` (any added docstrings / inline comments — `report.md` content too)
- `type-design-analyzer` (params.json shape, manifest fields, the patched `_build_params_block` if Option A)

Each briefing must be self-contained (subagents have zero conversation history). Briefings should reference:
- Kickoff invariants (N=3 PRIMARY, basket=4, locked TL/cap/cooldown values)
- File paths + line numbers under review
- The Option A vs B decision (so reviewers don't flag the schema fix as unrelated scope)
- The "no `Closes #N` in PR body" convention

### Task 12: Dev agent addresses R1 criticals

**Operator:** Dev agent (this plan's executor).

For each critical surfaced in R1:

- [ ] Read the full critical (don't just skim). Identify the file:line and the claim.
- [ ] Verify the claim independently — grep, run a focused test, read adjacent context. Refuse to apply a "fix" for a claim that doesn't reproduce.
- [ ] If the critical is valid: write a failing test, fix, run all related tests, commit.
- [ ] If the critical is invalid (mistake by reviewer agent): document why in a reply to the reviewer. Do NOT silently ignore.
- [ ] After all criticals processed, re-run full suite + smokes (only if any code changed).
- [ ] Push.

Each fix is its own commit on the PR branch. No squashing.

### Task 13: Reviewer launches R2

**Operator:** Reviewer agent.

R2 ALWAYS finds 1-3 NEW criticals introduced by R1 fixes (pattern observed across PR1/PR2/PR3 of epic #294). Skipping R2 is the most expensive shortcut available. Same 4-agent parallel dispatch as R1.

### Task 14: Dev agent addresses R2 criticals

Same protocol as Task 12.

### Task 15: Reviewer presents to Sam for merge

When criticals = 0:

- [ ] Reviewer summarizes the verification gate (kickoff Phase 3 list).
- [ ] Reviewer surfaces the Option A vs B path that landed and why.
- [ ] Reviewer asks Sam: "¿Dale al merge?"
- [ ] On Sam's "dale", reviewer (or Dev agent on instruction) marks PR ready-for-review (`gh pr ready 287`), confirms CI green, then merges.

**Merge protocol:**
- NO `gh pr merge --auto` (kickoff: branch protection doesn't enforce required checks).
- If CI green at the time of "dale", merge directly: `gh pr merge 287 --squash` or `--merge` per repo convention (verify last 5 PR merges to detect the convention: `gh pr list --state merged --limit 5 --json mergeCommit,title`).
- Do NOT use `--no-verify` or `--no-gpg-sign`.
- Do NOT close any other issue from this PR — `## References` only.

### Task 16: Post-merge — open follow-up tickets

After merge:

- [ ] **D9 §2.9 doc edit ticket** — open issue acknowledging the re-tune ran, cross-link the artefact path. Title: `chore(specs): D9 §2.9 — acknowledge A.4-1 retune ran`. Body should be ~5 lines.
- [ ] **A.4-2 walk-forward harness ticket** (if not already open under #250). The retune harness is the input to A.4-2; A.4-2 is the input to A.4-3 holdout evaluation.
- [ ] **#302 basket governance** stays blocked by #250 closure — no action.
- [ ] **#287** can be closed by Sam once #250 (A.4 epic) is satisfied; `## References` does not auto-close.

---

## Verification gate (Phase 3 done = ALL of these)

Single-line checklist for the reviewer's pre-merge summary:

- [ ] Byte-identical `params.json` across Run 1 (`data/retune/2026-05-04-pre-holdout/`) and Run 2 (`/tmp/retune-run2/`). Diff empty.
- [ ] Manifest MAX(timestamp) strictly `<` `2025-04-30T00:00:00 UTC` for every (symbol, tf). Independent SQL cross-check confirms.
- [ ] Full test suite passing (1254+ tests post-#299, +2 new under Option A).
- [ ] Long-form smokes passing (cooldown / sizing-cap / time-limit; ~20 min total).
- [ ] `params.json["symbol_overrides"]` covers 30 atr_* values exactly (10 symbols × 3 keys).
- [ ] `params.json["symbol_overrides"]` passes through 30 locked structural-fix values (Option A) OR `report.md` documents atr-only sub-block semantics (Option B).
- [ ] Performance reported in `report.md` (NOT gated).
- [ ] Guard B AST scanner (`tests/test_holdout_isolation.py`) passes — wrapper module still whitelisted, no new holdout references.
- [ ] No `data/holdout/` paths read by the wrapper (verified by Guard B + the manifest pointing to `data/ohlcv.db` for OHLCV ranges).

---

## Hard constraints (kickoff — non-negotiable)

- **NO read of `data/holdout/`** by any code path on this PR. Wrapper consumes `data/ohlcv.db`; manifest cross-checks `data/ohlcv.db`.
- **NO chmod of holdout** files or directory.
- **NO monkey-patch of Guard A** (`data/holdout_access.py`).
- **NO suppression of Guard B** AST scanner.
- **NO addition of the wrapper to a wider whitelist** beyond what Phase 2 already did.
- **NO promotion of `params.json` → `config.json`** in this PR (Phase 5 separate PR).
- **NO `--no-verify`, `--no-gpg-sign`, `push --force` (use `--force-with-lease`)**.
- **NO `gh pr merge --auto`** — manual merge after CI confirmed green.
- **NO closing of issues / PRs** in PR body (`## References` only, never `Closes #N`).
- **NO ajuste post-holdout to N or to locked TL/cap/cooldown values** — would disqualify validation primaria.
- **Re-tune scope is exactly the 30 atr_* values.** Pass-through preservation of locked keys (Option A) is not re-tuning; it's a faithful artefact shape.
