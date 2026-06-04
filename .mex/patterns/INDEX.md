---
name: patterns-index
description: Index of pattern files. Each pattern is a task-shaped runbook with steps + gotchas + verification. ROUTER.md tells you to check this file at the start of any task.
last_updated: 2026-06-04
---

# Pattern Index

| When you are about to… | Open |
|---|---|
| Close a position (USER or SYSTEM mode) | [closing-a-position.md](closing-a-position.md) |
| Read from `data/holdout/` (A.2 walk-forward, A.4 evaluation) | [holdout-access.md](holdout-access.md) |
| Pick between `precheck_connection()` and `snapshot_connection()` | [precheck-vs-snapshot.md](precheck-vs-snapshot.md) |
| Register a trial in a parameter/window sweep (#278) | [registering-a-trial.md](registering-a-trial.md) |
| Fire a falsification read of the locked holdout (A.4-3, #322) | [firing-the-holdout.md](firing-the-holdout.md) |
| Compute live portfolio drawdown / equity (kill-switch, shadow, dashboard) | [computing-portfolio-dd.md](computing-portfolio-dd.md) |
| Touch the backtest cost model / calibration (v3 two-body bound) | [cost-model-v3.md](cost-model-v3.md) |
| Abrir, correr o cerrar una celda del programa de edge (Edición 1) | [estudiar-una-celda.md](estudiar-una-celda.md) |

## How to grow this index

When you complete a task that did not match any existing pattern:

1. Create `patterns/<task-slug>.md` with the structure used by the files above (Purpose / When / Steps / Gotchas / Verify Checklist).
2. Add a row here.
3. Flag in your end-of-turn message: "Created `patterns/<slug>.md` from this session."

When you complete a task that matched an existing pattern but you discovered a new gotcha or deviation:

1. Update the pattern surgically (add to Gotchas, do not rewrite the whole file).
2. Flag in your end-of-turn message: "Updated `patterns/<slug>.md` with: <one-line summary>."
