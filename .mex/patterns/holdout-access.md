---
name: holdout-access
description: Runbook for reading data/holdout/ — the locked out-of-sample dataset. The only legitimate entry point is open_holdout(rel_path, evaluation_mode=True). Anything else is a CI failure.
triggers:
  - "holdout"
  - "open_holdout"
  - "A.2"
  - "A.4"
  - "walk-forward"
  - "out-of-sample"
last_updated: 2026-05-26
---

# Pattern: Holdout dataset access

## Purpose

`data/holdout/` is a locked, read-only dataset (chmod 444/555) used to validate strategy parameter changes honestly out-of-sample. Any uncontrolled read leaks the holdout and burns the bala única.

See [[../context/decisions.md]] §Validation Methodology for the full provenance + caveats.

## When to use

- A.2 walk-forward implementation
- A.4 holdout evaluation harness (Phase 3 ATR re-tune, Phase 4 review, A.4-2 / A.4-3)
- Diagnostic scripts that genuinely need the locked window (rare; defaults to "no")

## Steps

```python
from data.holdout_access import open_holdout, HoldoutAccessError

try:
    path = open_holdout("BTCUSDT_1h.parquet", evaluation_mode=True)
except HoldoutAccessError as e:
    # Caller is not on the allow-list, or evaluation_mode=False, or path traversal attempt.
    raise

df = pd.read_parquet(path)
```

To add a new module to the allow-list:

1. Edit `tests/test_holdout_isolation.py` and add the module's relative path to `HOLDOUT_LEGITIMATE_MODULES` with a one-line justification.
2. The PR reviewer must approve the entry (this is the human gate).
3. CI re-runs the AST scanner; if any non-whitelisted module still references the holdout via string / Path / f-string, CI fails.

## Gotchas

- **A.4-3 holdout execution is currently blocked.** Per [[../context/decisions.md]] §Caveat 5 (#322), do not call `simulate_strategy` with holdout-window frames, do not call `open_holdout(..., evaluation_mode=True)`, do not run the harness "just to see". The bala única dies on partial peeks too.
- **No monkey-patch, no env override.** `open_holdout` does not accept a flag to bypass the guard. If you think you need to, you don't — you need to add yourself to `HOLDOUT_LEGITIMATE_MODULES` instead.
- **Docstrings are skipped by the AST scanner.** Mentioning "holdout" in a docstring is fine. Passing `"holdout"` as a string at runtime is not.
- **Drift is not auto-detectable.** F&G and funding-rate hashes freeze the snapshot at fetch time. Before publishing A.4 results, re-fetch and diff against the source APIs to detect provider revisions.

## Verify Checklist

Before merging any code that touches the holdout:

- [ ] The only reference to `holdout` is via `open_holdout(...)` — no string literals, no `Path(...) / "holdout"`, no `os.path.join(..., "holdout", ...)`.
- [ ] The module is whitelisted in `tests/test_holdout_isolation.py::HOLDOUT_LEGITIMATE_MODULES` with a justification.
- [ ] `tests/test_holdout_isolation.py` passes locally.
- [ ] If this is A.4-3 evaluation: closure criteria for #322 are documented as met (re-tune produced candidates, A.4-2 passed, drift check done).
- [ ] If a Bayesian posterior is part of the deliverable, the `pymc-bayesian-modeling` skill was invoked (NOT a prose-only update). See [[../context/decisions.md]] §Agent tooling note.
