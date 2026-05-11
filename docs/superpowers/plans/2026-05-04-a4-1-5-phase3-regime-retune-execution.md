# A.4-1.5 Phase 3 — Pre-holdout Regime Threshold Re-tune Execution Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the merged regime threshold re-tune harness (`tools/regime_retune_pre_holdout.py`, PR #306 / commit `f38d94d`) over the pre-holdout window `[earliest, 2025-04-30T00:00:00Z)`, produce byte-deterministic artefacts under `data/retune/2026-05-04-pre-holdout/`, evaluate the four pre-registered decision flags from spec D9 §2.10, and ship the artefact as a single commit on a fresh branch + draft PR. Phase 4 (multi-agent review) and Phase 5 (promotion) are out of scope.

**Architecture:** Two consecutive harness invocations. Run 1 writes canonical artefacts to the default out-dir (`data/retune/2026-05-04-pre-holdout/`); Run 2 writes to `/tmp` for reproducibility comparison. The harness already implements: locked grid (60_40, 70_30, 80_20, no_detector), strict `<` slicing in `_slice_below_cutoff`, decision flag aggregation (`change_detection`, `sanity_check`, `stability_check`, `degenerate_zero_pnl`), atomic JSON+text writers, stale-artefact cleanup, and emergency fallbacks for rc=3/4/5/6 halt branches. This plan does not modify the harness — only invokes it, verifies the outputs against the manifest contract, runs an independent SQL leakage cross-check, and commits the artefacts.

**Tech Stack:** Python 3.x, `tools.regime_retune_pre_holdout` (merged), `sqlite3`, `pandas`, `pytest`, `gh` CLI, `git`. No new dependencies. No reads from `data/holdout/`.

---

## Pre-execution constraints (kickoff — non-negotiable)

- **NO read of `data/holdout/`** by anything in this PR. Harness reads `data/ohlcv.db` only.
- **NO chmod / monkey-patch / Guard B suppression** at any point.
- **NO promotion** of `regime_params.json` → `strategy/regime.py` + `backtest.py` in this PR (Phase 5 separate).
- **NO `--no-verify`, `--no-gpg-sign`, `push --force`** (use `--force-with-lease` if ever needed; not needed on this fresh branch).
- **NO `gh pr merge --auto`**.
- **NO `Closes #N` / `Fixes #N` / `Resolves #N`** in commit body or PR body — `## References` only.
- **NO touching `data/retune/2026-05-01-pre-holdout/`** — that is A.4-1's stale artefact, owned by a different plan (`docs/superpowers/plans/2026-05-04-a4-1-phase3-retune-execution.md`).
- **Locked-by-historical-record (do not adjust):** grid = 4 configs from `bf581f1`; cutoff = `2025-04-30T00:00:00 UTC`; objective = sum of `net_pnl` across the 10 portfolio symbols; basket = `BTCUSDT, ETHUSDT, ADAUSDT, AVAXUSDT, DOGEUSDT, UNIUSDT, XLMUSDT, PENDLEUSDT, JUPUSDT, RUNEUSDT`. Any post-hoc adjustment disqualifies as primary validation.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `data/retune/2026-05-04-pre-holdout/regime_params.json` | Create (via harness) | Winner config — either `{"regime_thresholds": {...}}` or `{"regime_disabled": true}`; byte-deterministic durability marker |
| `data/retune/2026-05-04-pre-holdout/regime_manifest.json` | Create (via harness) | Cutoff, ohlcv.db sha256, code commit, symbol_overrides sha256, per-(symbol, tf) MIN/MAX timestamps, leakage_check, decision_flags, per_config_pnl, runtime, ran_at_iso |
| `data/retune/2026-05-04-pre-holdout/regime_report.md` | Create (via harness) | Human-readable per-config aggregate + per-symbol breakdown + caveats + data-ranges table |
| `git commit` on `feat/methodology-a4-1-5-phase3-artefact` | Create | Single Phase 3 commit; PR opens as draft |

**No changes to** harness module, tests, `strategy/regime.py`, `backtest.py`, `config.json`, `config.defaults.json`, or anything else. The branch is artefact-only.

---

## Phase 3 — Sweep execution

### Task 1: Branch off main and verify pre-flight invariants

**Files:**
- No code changes; verification only.

- [ ] **Step 1: Confirm clean main and HEAD == f38d94d**

```bash
git status
git log --oneline -1
```

Expected: working tree shows only untracked items (`.claude/`, `data/retune/2026-05-01-pre-holdout/` from a different plan, and this plan file under `docs/superpowers/plans/`). HEAD prints `f38d94d feat(methodology): A.4-1.5 pre-holdout regime threshold re-tune harness (#306)`.

If working tree has tracked modifications: STOP and report.

- [ ] **Step 2: Create the artefact branch**

```bash
git checkout -b feat/methodology-a4-1-5-phase3-artefact
```

Expected: switches cleanly. Branch is fresh, no commits added yet.

- [ ] **Step 3: Capture ohlcv.db sha256 (pre-flight reference)**

```bash
shasum -a 256 data/ohlcv.db | tee /tmp/ohlcv_sha_preflight.txt
```

Expected: prints `<64-hex-chars>  data/ohlcv.db`. Save this value — it must match the `ohlcv_sha256` field the harness writes into `regime_manifest.json` in Task 3 / Task 4.

If `data/ohlcv.db` does not exist: STOP. Harness will exit rc=2.

- [ ] **Step 4: Verify Guard B AST scanner is green**

```bash
pytest tests/test_holdout_isolation.py -v
```

Expected: all tests pass, including `test_no_holdout_references_in_non_whitelisted_modules`. The harness module is already whitelisted (committed in #306).

If any test fails: STOP and report — do NOT proceed to the sweep.

- [ ] **Step 5: Verify harness tests still pass**

```bash
pytest tests/test_regime_retune_pre_holdout.py -v
```

Expected: all tests pass (PR #306 body cites 33 tests in this file; pytest may report parametrized variants). Look for any post-merge drift.

If any test fails: STOP and report.

- [ ] **Step 6: Commit**

No commit — verification only.

---

### Task 2: Execute Run 1 (canonical artefact)

**Files:**
- Create (via harness): `data/retune/2026-05-04-pre-holdout/{regime_params.json, regime_manifest.json, regime_report.md}`

**This step requires Sam's "dale" before execution.** Surface the plan and wait.

Default out-dir is computed by the harness from `datetime.now(timezone.utc).date().isoformat()`; today's UTC date is `2026-05-04`, so the dir will be `data/retune/2026-05-04-pre-holdout/`. The harness creates it if missing and clears any stale canonical artefacts before writing (`_clear_stale_artefacts`).

- [ ] **Step 1: Run the harness**

```bash
python -m tools.regime_retune_pre_holdout --max-date 2025-04-30 2>&1 | tee /tmp/regime_retune_run1.log
```

Expected: log lines per `(symbol, config)` cell with `net_pnl=$+,.2f trades=N`. Final log lines: `Leakage check: PASS`, `Decision flags: {...}`, `Artefacts written to data/retune/2026-05-04-pre-holdout/`. Exit code 0.

Halt branches (do NOT proceed to commit; report to reviewer with the log + halted_summary.json):
- rc=2 — no OHLCV DB (caught at Task 1 Step 3)
- rc=3 — sanity halt: `no_detector` won → bug in harness or dataset slicing; investigate
- rc=4 — sweep had errored cells → triage `data/retune/.../sweep_errors.json`
- rc=5 — manifest/canonical write failure → check `data/retune/.../raw_results.json`
- rc=6 — degenerate zero-pnl: every per-config sum below 1e-9 → catastrophic upstream

- [ ] **Step 2: Capture rc and proceed conditionally**

```bash
echo "rc=$?"
```

Expected: `rc=0`. If non-zero, STOP. Do NOT delete the halted_summary.json — it's the diagnostic for reviewer triage.

- [ ] **Step 3: Inspect the artefact directory**

```bash
ls -la data/retune/2026-05-04-pre-holdout/
```

Expected: exactly three files — `regime_params.json`, `regime_manifest.json`, `regime_report.md`. No `.tmp` orphans, no `halted_summary.json`, no `sweep_errors.json`, no `raw_results.json`.

If any halt-branch artefact is present: STOP — rc=0 should not coexist with a halt artefact.

- [ ] **Step 4: Commit**

No commit — Run 2 reproducibility check still pending.

---

### Task 3: Execute Run 2 (reproducibility check to /tmp)

**Files:**
- Create (in temp): `/tmp/regime_retune_run2/{regime_params.json, regime_manifest.json, regime_report.md}` (NOT under `data/retune/`)

The reproducibility gate is: `regime_params.json` byte-identical across two runs with same cutoff + same code commit + same OHLCV DB. `regime_manifest.json` will differ in `ran_at_iso` and `runtime_seconds` by design; everything else must match.

- [ ] **Step 1: Run the harness into a temp dir**

```bash
python -m tools.regime_retune_pre_holdout --max-date 2025-04-30 --out-dir /tmp/regime_retune_run2 2>&1 | tee /tmp/regime_retune_run2.log
```

Expected: same final log lines as Run 1 (`Leakage check: PASS`, same decision flags), exit code 0.

If rc != 0 here but Run 1 succeeded: STOP. Indicates non-determinism in the harness path itself or environmental drift between runs.

- [ ] **Step 2: Diff `regime_params.json` byte-for-byte**

```bash
diff data/retune/2026-05-04-pre-holdout/regime_params.json /tmp/regime_retune_run2/regime_params.json
```

Expected: empty output (exit code 0).

If diff is non-empty: STOP. Reproducibility broken — winner choice or threshold values are non-deterministic. Report immediately with both files attached.

- [ ] **Step 3: Diff `regime_manifest.json` excluding expected drift fields**

```bash
diff <(python3 -c "import json; m=json.load(open('data/retune/2026-05-04-pre-holdout/regime_manifest.json')); m.pop('ran_at_iso',None); m.pop('runtime_seconds',None); print(json.dumps(m, sort_keys=True, indent=2))") \
     <(python3 -c "import json; m=json.load(open('/tmp/regime_retune_run2/regime_manifest.json')); m.pop('ran_at_iso',None); m.pop('runtime_seconds',None); print(json.dumps(m, sort_keys=True, indent=2))")
```

Expected: empty output. Strips the two known-drift fields and verifies everything else (cutoff, ohlcv_sha256, code_commit, symbol_overrides_sha256, per_config_pnl, per_config_trades, winner, runner_up, winner_margin_pct, decision_flags, per_symbol_data_ranges, scope_notes, grid, leakage_check, symbols, harness, spec_ref) is byte-identical.

If any other field differs: STOP. Manifest non-determinism — investigate before committing. Common causes: cells run in non-deterministic order (re-check that `_aggregate_results` sums float-stably), or harness picked up a different `code_commit` between invocations (would be the case if a commit landed between runs — unlikely but verifiable).

- [ ] **Step 4: Cleanup the temp dir**

```bash
rm -rf /tmp/regime_retune_run2
```

- [ ] **Step 5: Commit**

No commit — verification only.

---

### Task 4: Manifest gate — independent SQL no-leakage cross-check

**Files:**
- Read-only: `data/retune/2026-05-04-pre-holdout/regime_manifest.json`, `data/ohlcv.db`

The harness's internal `_verify_no_leakage` already asserts every per-(symbol, tf) MAX timestamp is `< cutoff_ms`. This task does an **independent** SQL query against `data/ohlcv.db` to cross-check, modeled after A.4-1's Phase 3 Task 8.

- [ ] **Step 1: Run the cross-check script**

```bash
python3 <<'EOF'
import json, sqlite3, sys
from datetime import datetime, timezone

CUTOFF_ISO = "2025-04-30T00:00:00+00:00"
cutoff_ms = int(datetime.fromisoformat(CUTOFF_ISO).timestamp() * 1000)

with open("data/retune/2026-05-04-pre-holdout/regime_manifest.json") as f:
    manifest = json.load(f)

con = sqlite3.connect("data/ohlcv.db")
problems = []
for sym, tfs in manifest["per_symbol_data_ranges"].items():
    for tf, span in tfs.items():
        # Manifest-declared MAX must be strictly < cutoff
        if span["max_ts_ms"] is not None and span["max_ts_ms"] >= cutoff_ms:
            problems.append(f"{sym} {tf}: manifest max_ts_ms {span['max_ts_ms']} >= cutoff {cutoff_ms}")
        # Independent triangulation: ask the DB directly
        row = con.execute(
            "SELECT MAX(open_time) FROM ohlcv "
            "WHERE symbol=? AND timeframe=? AND open_time<?",
            (sym, tf, cutoff_ms),
        ).fetchone()
        db_max = row[0]
        if db_max != span["max_ts_ms"]:
            problems.append(
                f"{sym} {tf}: manifest max_ts_ms {span['max_ts_ms']} != "
                f"independent DB max {db_max}"
            )
        # Sanity (advisory): if the production DB has zero post-cutoff bars,
        # the leakage test is weaker but not wrong.
        n_post_row = con.execute(
            "SELECT COUNT(*) FROM ohlcv "
            "WHERE symbol=? AND timeframe=? AND open_time>=?",
            (sym, tf, cutoff_ms),
        ).fetchone()
        if n_post_row[0] == 0:
            print(f"INFO  {sym} {tf}: production DB has no bars >= cutoff (weaker leakage test)",
                  file=sys.stderr)

if problems:
    print("LEAKAGE GATE FAILED:", file=sys.stderr)
    for p in problems:
        print(f"  - {p}", file=sys.stderr)
    sys.exit(1)
print("LEAKAGE GATE PASS — manifest MAX timestamps strictly < cutoff "
      "AND match independent DB query for all (symbol, tf).")
EOF
```

Expected: prints `LEAKAGE GATE PASS`. INFO lines are advisory.

If `LEAKAGE GATE FAILED` for any reason: STOP and report. Do NOT commit. Critical.

- [ ] **Step 2: Verify ohlcv.db sha256 matches Task 1 Step 3 capture**

```bash
python3 -c "import json; m=json.load(open('data/retune/2026-05-04-pre-holdout/regime_manifest.json')); print('manifest:', m['ohlcv_sha256'])"
cat /tmp/ohlcv_sha_preflight.txt
```

Expected: the manifest's `ohlcv_sha256` matches the pre-flight `shasum -a 256` capture. The DB has not been mutated mid-run.

If mismatch: STOP. Means the DB changed between pre-flight and the harness's hash step (corruption or concurrent write). Report and re-run.

- [ ] **Step 3: Verify cutoff fields in manifest**

```bash
python3 -c "
import json
m = json.load(open('data/retune/2026-05-04-pre-holdout/regime_manifest.json'))
assert m['cutoff_effective_iso'] == '2025-04-30T00:00:00+00:00', m['cutoff_effective_iso']
assert m['cutoff_effective_ms'] == 1745971200000, m['cutoff_effective_ms']
print('cutoff invariants OK:', m['cutoff_effective_iso'], m['cutoff_effective_ms'])
"
```

Expected: prints `cutoff invariants OK: 2025-04-30T00:00:00+00:00 1745971200000`.

The integer `1745971200000` is `int(datetime(2025,4,30,tzinfo=UTC).timestamp() * 1000)` — matches the holdout dataset MANIFEST.json `cutoff.start_inclusive_utc`.

- [ ] **Step 4: Commit**

No commit — verification only.

---

### Task 5: Evaluate decision flags and prepare commit body

**Files:**
- Read-only: `data/retune/2026-05-04-pre-holdout/regime_manifest.json`, `data/retune/2026-05-04-pre-holdout/regime_report.md`

By the time we reach this task, halt-branch flags (sanity_check, degenerate_zero_pnl) cannot be true — the harness would have returned rc=3/6 and the canonical artefacts would not exist. So at this point the only flags whose interpretation matters are `change_detection` and `stability_check`.

- [ ] **Step 1: Read the four decision flags**

```bash
python3 -c "
import json
m = json.load(open('data/retune/2026-05-04-pre-holdout/regime_manifest.json'))
df = m['decision_flags']
print('change_detection:', df['change_detection'])
print('sanity_check:', df['sanity_check'])
print('stability_check:', df['stability_check'])
print('degenerate_zero_pnl:', df['degenerate_zero_pnl'])
print('winner:', m['winner'])
print('runner_up:', m['runner_up'])
print('winner_margin_pct:', m['winner_margin_pct'])
print('per_config_pnl:', json.dumps(m['per_config_pnl'], sort_keys=True, indent=2))
"
```

Expected: `sanity_check: False` and `degenerate_zero_pnl: False` (else we'd not be here). The other two values inform commit body framing.

- [ ] **Step 2: Apply flag interpretation**

Interpretation matrix per spec D9 §2.10:

- `change_detection == True` → winner ≠ `60_40`. Document explicitly in commit body: `CHANGE substantivo registrado per spec D9 §2.10. Phase 5 promotion requires explicit review`.
- `change_detection == False` → winner == `60_40`. No CHANGE; commit body says `Re-tune confirms current production thresholds (60_40)`.
- `stability_check == True` → 2nd-best within 5% of winner. Add caveat to commit body: `Stability caveat: 2nd-best (<runner_up>) within 5% of winner. Regime detection operating in flat region; choice is somewhat arbitrary on objective alone — qualitative tradeoffs should weigh in at promotion`.
- `stability_check == False` → margin is decisive. No caveat needed; mention `Margin: <X>% of |winner|`.

- [ ] **Step 3: Verify regime_params.json shape matches winner**

```bash
python3 -c "
import json
m = json.load(open('data/retune/2026-05-04-pre-holdout/regime_manifest.json'))
p = json.load(open('data/retune/2026-05-04-pre-holdout/regime_params.json'))
winner = m['winner']
if winner == 'no_detector':
    assert p == {'regime_disabled': True}, p
    print('regime_params.json shape OK: regime_disabled')
else:
    assert 'regime_thresholds' in p, p
    rt = p['regime_thresholds']
    assert set(rt.keys()) == {'bull_above', 'bear_below'}, rt
    assert isinstance(rt['bull_above'], int)
    assert isinstance(rt['bear_below'], int)
    print(f'regime_params.json shape OK: regime_thresholds bull_above={rt[\"bull_above\"]} bear_below={rt[\"bear_below\"]}')
"
```

Expected: prints either `regime_disabled` (impossible at this point — would have halted at rc=3) or `regime_thresholds bull_above=<int> bear_below=<int>`.

- [ ] **Step 4: Commit**

No commit — preparation only.

---

### Task 6: Stage, commit, and push the artefact

**Files:**
- Add: `data/retune/2026-05-04-pre-holdout/regime_params.json`
- Add: `data/retune/2026-05-04-pre-holdout/regime_manifest.json`
- Add: `data/retune/2026-05-04-pre-holdout/regime_report.md`

**This step requires a fresh "dale" from Sam** (the Task 2 "dale" authorized the run, not the commit; closure-by-closer rule). Before this task, also surface the decision-flag summary so Sam can authorize with full context.

- [ ] **Step 1: Stage the three artefact files explicitly**

```bash
git add data/retune/2026-05-04-pre-holdout/regime_params.json
git add data/retune/2026-05-04-pre-holdout/regime_manifest.json
git add data/retune/2026-05-04-pre-holdout/regime_report.md
git status
```

Expected: `git status` shows exactly three new files staged under `data/retune/2026-05-04-pre-holdout/`. Nothing else staged. Untracked items (`.claude/`, `data/retune/2026-05-01-pre-holdout/`, this plan file) remain untracked — do NOT stage them.

If anything unexpected is staged: `git restore --staged <path>` to unstage, then re-verify.

- [ ] **Step 2: Build the commit message body**

Pull these values from `regime_manifest.json` (Task 5 Step 1 output):

- `<ohlcv_sha>` — manifest's `ohlcv_sha256`
- `<code_commit>` — manifest's `code_commit` (will be the branch HEAD = the parent of this commit)
- `<overrides_sha>` — manifest's `symbol_overrides_sha256`
- `<runtime>` — manifest's `runtime_seconds`
- `<winner>` — manifest's `winner`
- `<winner_pnl>` — manifest's `winner_pnl`
- `<runner_up>` — manifest's `runner_up`
- `<runner_up_pnl>` — manifest's `runner_up_pnl`
- `<margin_pct>` — manifest's `winner_margin_pct`
- `<pnl_60_40>`, `<pnl_70_30>`, `<pnl_80_20>`, `<pnl_no_detector>` — manifest's `per_config_pnl`
- `<change_flag>`, `<sanity_flag>`, `<stability_flag>`, `<degenerate_flag>` — manifest's `decision_flags`

Conditional sections:

- If `<change_flag> == True`:
  ```
  CHANGE substantivo registrado per spec D9 §2.10. Winner is <winner>, not the
  current production 60_40. Phase 5 promotion requires explicit review and
  separate post-A.4-holdout-pass PR; this commit does NOT promote.
  ```
- If `<change_flag> == False`:
  ```
  Re-tune confirms current production thresholds (60_40). No CHANGE; Phase 5
  promotion is a no-op for this artefact.
  ```
- If `<stability_flag> == True`:
  ```
  Stability caveat: 2nd-best (<runner_up>) within 5% of winner (<margin_pct>%).
  Regime detection operating in flat region; choice is somewhat arbitrary on
  objective alone — qualitative tradeoffs should weigh in at promotion.
  ```
- If `<stability_flag> == False`: omit the stability caveat block.

- [ ] **Step 3: Commit (HEREDOC, with placeholders filled in)**

```bash
git commit -m "$(cat <<'EOF'
feat(methodology): A.4-1.5 Phase 3 — regime threshold re-tune artefact

Sweep complete over [earliest, 2025-04-30T00:00:00Z), 4 configs × 10 symbols.

Cutoff (--max-date): 2025-04-30T00:00:00+00:00 UTC
OHLCV DB sha256: <ohlcv_sha>
Code commit (in manifest): <code_commit>
symbol_overrides sha256: <overrides_sha>
Runtime: ~<runtime>s
Leakage check: PASS (independent SQL cross-check confirmed)

Winner: <winner> (sum net_pnl $<winner_pnl>)
Runner-up: <runner_up> (sum net_pnl $<runner_up_pnl>)
Margin: <margin_pct>% of |winner|

Per-config breakdown:
- 60_40:       $<pnl_60_40>
- 70_30:       $<pnl_70_30>
- 80_20:       $<pnl_80_20>
- no_detector: $<pnl_no_detector>

Decision flags:
- change_detection:    <change_flag>
- sanity_check:        <sanity_flag>
- stability_check:     <stability_flag>
- degenerate_zero_pnl: <degenerate_flag>

[CHANGE block — include only if change_detection == True]
[Stability block — include only if stability_check == True]

Reproducibility verified: Run-2 to /tmp produced byte-identical
regime_params.json (diff empty). Manifest fields outside ran_at_iso
and runtime_seconds also identical.

References (this commit closes nothing):
- #305 (A.4-1.5 ticket — Phase 4 review pending)
- #306 (harness PR, merged)
- #287 (A.4-1 ATR retune, gated by A.4-1.5 closure)
- #250 (A.4 holdout evaluation epic)
- bf581f1 (origin commit for current 60/40 thresholds)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Note: the HEREDOC body must have literal numerical / boolean values substituted before committing — pasting the placeholders verbatim is a plan failure. The implementer (or subagent) must read the manifest in Task 5 and inline the actual values.

- [ ] **Step 4: Verify the commit**

```bash
git log --oneline -1
git show --stat HEAD
```

Expected: one commit on top of `f38d94d` with title `feat(methodology): A.4-1.5 Phase 3 — regime threshold re-tune artefact`. `git show --stat` shows three files added under `data/retune/2026-05-04-pre-holdout/`, no other changes.

If the commit added unexpected files: `git reset --soft HEAD~1`, fix the staging, retry. Do NOT amend after push.

- [ ] **Step 5: Push the branch**

```bash
git push -u origin feat/methodology-a4-1-5-phase3-artefact
```

Expected: push succeeds; `-u` sets upstream tracking. No `--force`, no `--no-verify`.

- [ ] **Step 6: Commit**

Already done above. No additional commit at this step.

---

### Task 7: Open the draft PR

**Files:**
- No file changes — `gh` invocation only.

PR opens as draft. Phase 4 multi-agent review is reviewer-driven and out of scope for this plan. Body uses `## References` (never `Closes #N`).

- [ ] **Step 1: Verify recent merge convention**

```bash
gh pr list --state merged --limit 5 --json mergeCommit,title,headRefName,number
```

Expected: prints last 5 merged PRs. Note the merge style (squash vs merge commit) — relevant only at Phase 5 close, not here.

- [ ] **Step 2: Open the draft PR**

Build the body inline. The `<...>` placeholders use the same manifest values pulled in Task 6 Step 2.

```bash
gh pr create --draft --title "feat(methodology): A.4-1.5 Phase 3 artefact" --body "$(cat <<'EOF'
## Summary

Phase 3 of A.4-1.5 mini-epic. Single commit adding the pre-holdout regime threshold re-tune artefact under `data/retune/2026-05-04-pre-holdout/`. The harness (`tools/regime_retune_pre_holdout.py`, merged as #306 / `f38d94d`) was invoked with `--max-date 2025-04-30`, ran the locked 4-config grid over the 10 portfolio symbols, and emitted the three artefact files. **No code changes** in this PR — artefact only.

## Run summary

- Cutoff (`--max-date`): `2025-04-30T00:00:00+00:00` UTC (strict `<` slicing)
- OHLCV DB sha256: `<ohlcv_sha>`
- Code commit recorded in manifest: `<code_commit>`
- Runtime: ~`<runtime>`s
- Leakage check: PASS (independent SQL cross-check confirmed)
- Winner: `<winner>` (sum net_pnl `$<winner_pnl>`)
- Runner-up: `<runner_up>` (sum net_pnl `$<runner_up_pnl>`)
- Margin: `<margin_pct>`% of |winner|

## Decision flags

| Flag | Value | Interpretation |
|------|-------|----------------|
| `change_detection`   | `<change_flag>`   | <`Winner ≠ 60_40 — CHANGE substantivo` if True else `Winner == 60_40 — no CHANGE`> |
| `sanity_check`       | `<sanity_flag>`   | False (else rc=3 halt path; canonical artefacts would not exist) |
| `stability_check`    | `<stability_flag>`| <`2nd-best within 5% — flat-region caveat` if True else `Margin decisive`> |
| `degenerate_zero_pnl`| `<degenerate_flag>`| False (else rc=6 halt path; canonical artefacts would not exist) |

## Per-config aggregate

| Config       | Sum net_pnl |
|--------------|-------------|
| 60_40        | `$<pnl_60_40>` |
| 70_30        | `$<pnl_70_30>` |
| 80_20        | `$<pnl_80_20>` |
| no_detector  | `$<pnl_no_detector>` |

## Verification gate (all green)

- Byte-identical `regime_params.json` across two consecutive runs (diff empty)
- Manifest MAX(timestamp) strictly < `2025-04-30T00:00:00 UTC` for every (symbol, tf)
- Independent SQL cross-check confirmed no-leakage AND match between manifest declared MAX and the production DB query
- OHLCV DB sha256 stable from pre-flight to manifest hash step
- Cutoff fields in manifest match holdout MANIFEST.json (`cutoff_effective_iso == 2025-04-30T00:00:00+00:00`, `cutoff_effective_ms == 1745971200000`)
- `regime_params.json` shape matches winner (either `{"regime_thresholds": {...}}` or `{"regime_disabled": true}`)
- Guard B AST scanner passes — no new holdout references
- `tests/test_regime_retune_pre_holdout.py` still green at `f38d94d` HEAD

## Out of scope

- **Phase 4 multi-agent review** — reviewer-driven, follows this PR draft.
- **Promotion of `regime_params.json`** → `strategy/regime.py` + `backtest.py`. Phase 5 separate PR after A.4 holdout passes.
- **Closing #305.** This PR `## References` it; Sam closes after Phase 4 sign-off.
- **A.4-1 (#287) unblocking.** Closure-by-closer rule: A.4-1 unblocks only after #305 is explicitly closed by Sam, not by this PR's merge.

## Phase 4 review checklist (for reviewer agent)

Phase 4 evaluates the artefact (data), not the harness code. Suggested briefing focus:
- Magnitude check: are `per_config_pnl` values plausible vs known historical baselines?
- Leakage proof verification: re-run the independent SQL cross-check from Task 4
- Decision flag interpretation accuracy: does the commit body framing match the flag values?
- Commit message and PR body accuracy: do all numbers in narrative match `regime_manifest.json`?

## References (this PR closes nothing)

- #305 (A.4-1.5 ticket)
- #306 (harness PR, merged as f38d94d)
- #287 (A.4-1 ATR retune — gated by A.4-1.5 closure)
- #250 (A.4 holdout evaluation epic)
- spec: docs/superpowers/specs/es/2026-05-03-asunciones-tecnicas-pre-holdout.md §2.10
- bf581f1 (origin commit for current 60/40 thresholds)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Note: same placeholder rule as Task 6 Step 3 — substitute literal values from `regime_manifest.json` before invoking. Pasting verbatim is a plan failure.

- [ ] **Step 3: Verify the PR opened as draft**

```bash
gh pr view --json number,isDraft,title,headRefName
```

Expected: `isDraft: true`, `headRefName: feat/methodology-a4-1-5-phase3-artefact`, title matches Step 2. Capture the PR number for later reference.

- [ ] **Step 4: Surface PR URL to Sam + reviewer**

Print the PR URL. Phase 4 multi-agent review (reviewer's responsibility) starts from this draft.

- [ ] **Step 5: Commit**

No commit — `gh pr create` does not modify the repo.

---

## Phase 4 — Multi-agent review (out of scope; reviewer-driven)

Documented here for awareness only — the Dev agent does not execute Phase 4. The reviewer agent dispatches in parallel:

- `code-reviewer` — evaluate artefact data quality, magnitude plausibility, commit message and PR body accuracy
- `silent-failure-hunter` — re-run the independent SQL cross-check; verify no halt-branch artefacts in canonical dir
- `comment-analyzer` — review `regime_report.md` content for accuracy / forward-references / rot
- `type-design-analyzer` — verify `regime_params.json` and `regime_manifest.json` shapes match harness contract

Phase 4 of an artefact PR is typically faster than a code PR (1-2 rounds vs 3 for code, per A.4-1.5 Phase 2 lessons). The Dev agent may be re-summoned to address criticals but does not initiate the review.

---

## Verification gate (Phase 3 done = ALL of these)

- [ ] Sweep run 1 completes with rc=0 AND canonical artefacts present (`regime_params.json`, `regime_manifest.json`, `regime_report.md`)
- [ ] No halt-branch artefacts (`halted_summary.json`, `sweep_errors.json`, `raw_results.json`) in the canonical dir
- [ ] `regime_params.json` byte-identical between Run 1 and Run 2 (diff empty)
- [ ] `regime_manifest.json` byte-identical between Run 1 and Run 2 excluding `ran_at_iso` and `runtime_seconds` (diff empty)
- [ ] Manifest MAX(timestamp) strictly `<` `2025-04-30T00:00:00 UTC` for every (symbol, tf) AND matches independent DB query
- [ ] Manifest `ohlcv_sha256` matches the pre-flight `shasum -a 256 data/ohlcv.db` capture
- [ ] Manifest `cutoff_effective_iso == "2025-04-30T00:00:00+00:00"` and `cutoff_effective_ms == 1745971200000`
- [ ] `regime_params.json` shape matches winner (either `regime_thresholds` keys are valid ints, or `regime_disabled: true`)
- [ ] Decision flags evaluated; sanity_check and degenerate_zero_pnl confirmed False (else canonical artefacts would not exist)
- [ ] Single commit on `feat/methodology-a4-1-5-phase3-artefact` adding exactly the three artefact files; no other staged content
- [ ] Branch pushed; draft PR open with `## References` (no `Closes #N`)
- [ ] Guard B AST scanner still passes
- [ ] `tests/test_regime_retune_pre_holdout.py` still green
- [ ] No reads of `data/holdout/` performed at any point during this plan's execution

---

## Halt-branch protocol (rc != 0)

If at any point the harness returns rc != 0:

1. STOP. Do NOT delete halt-branch artefacts; they are the diagnostic.
2. Capture the full stderr of the run plus the contents of `halted_summary.json` / `sweep_errors.json` / `raw_results.json` as appropriate.
3. Report to reviewer (Sam) with:
   - The rc value and its pre-registered meaning per spec D9 §2.10
   - The flag values from `agg["decision_flags"]` (also embedded in halted_summary.json)
   - A first-pass hypothesis on root cause:
     - rc=2: ohlcv.db missing (pre-flight should have caught)
     - rc=3 (sanity_check / no_detector wins): bug in harness slicing OR regime detector OR severe regime composition issue in pre-holdout window
     - rc=4 (errored cells): likely transient I/O or upstream API hiccup; check sweep_errors.json
     - rc=5 (write failure): disk-full / permissions / non-serializable manifest field
     - rc=6 (degenerate_zero_pnl): all per-config sums below 1e-9 — catastrophic upstream (all symbols disabled? OHLCV corruption? signal generation broken?)
4. Do NOT commit or push. Do NOT retry blindly.
5. Wait for reviewer triage decision.

---

## Hard constraints recap (kickoff)

- NO read of `data/holdout/`
- NO chmod / monkey-patch / Guard B suppression
- NO promotion to `strategy/regime.py` or `backtest.py` in this PR
- NO `--no-verify`, `--no-gpg-sign`, `push --force`
- NO `gh pr merge --auto`
- NO `Closes #N` / `Fixes #N` / `Resolves #N` anywhere — `## References` only
- NO touching `data/retune/2026-05-01-pre-holdout/`
- NO ajuste post-holdout to grid, cutoff, objective, or basket — locked by historical record
