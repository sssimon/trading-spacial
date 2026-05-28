---
name: claude
description: Always-loaded project anchor for Claude Code. Read this first. Contains project identity, non-negotiables, commands, and pointer to ROUTER.md for full context.
last_updated: 2026-05-26
---

# trading-spacial

## What This Is

A BTC/USDT automated trading signal system with multi-timeframe technical analysis (4H macro → 1H signal → 5M entry trigger), per-symbol position lifecycle, and per-user data isolation. Signals are emitted automatically; entry/close decisions are operator-driven.

## Non-Negotiables

1. **Closing a position goes through `PositionClosure` only.** Direct writes to `status='closed'`, direct calls to `db_close_position_sql`, or own `transaction()` around the close path are contract violations. See [[.mex/patterns/closing-a-position.md]].
2. **The locked holdout dataset (`data/holdout/`) is only readable via `open_holdout(rel_path, evaluation_mode=True)`.** No string literals, no `Path(...) / "holdout"`. Adding a module to the allow-list requires editing `tests/test_holdout_isolation.py::HOLDOUT_LEGITIMATE_MODULES` with a justification reviewed in the PR. See [[.mex/patterns/holdout-access.md]].
3. **A.4-3 holdout execution is currently blocked.** Do not call `simulate_strategy` with holdout-window frames, do not call `open_holdout(...)` for evaluation, even "just to see". The bala única dies on partial peeks too. Closure criteria for #322 must all be met first. See [[.mex/context/decisions.md]] §Caveat 5.
4. **`RISK_PER_TRADE = 0.01` is fixed. Do not add multiplicative risk scalers on top.** Per-symbol volatility lives in `symbol_overrides` (epic #121). See [[.mex/context/architecture.md]] §Key Backend Logic.
5. **Pre-#223/#224 backtest numbers are inflated.** Do not cite the numbers in `docs/superpowers/specs/es/2026-04-17-formula-ganadora-resultados-finales.md` or `2026-04-18-documento-completo-sistema-trading.md` as baseline. See #272 for re-baselining work.
6. **Authoritative spec docs override CLAUDE.md prose.** When in doubt about sizing / symbol selection / regime detector, read `docs/superpowers/specs/es/2026-04-18-documento-completo-sistema-trading.md` before changing code.
7. **Admin-merge of a PR requires explicit verification.** Read the FULL CI failure summary, confirm each failure is on the orthogonal-flake list in [[.mex/context/ci-discipline.md]], and `mex log` the bypass with the specific tests + tracking issues. Skipping any of these is how regressions reach main through the gate (the PR #500 → #502 cascade demonstrated this). See [[.mex/context/ci-discipline.md]] for the rule + the orthogonal-flake list.

## Commands

```bash
# Backend
python btc_api.py          # REST API at http://localhost:8000
python btc_scanner.py      # Standalone scanner (runs once, used by API)
python watchdog.py         # Process supervisor (Windows only)

# Tests
python -m pytest tests/ -v
python -m pytest tests/test_scanner.py -v
python -m pytest tests/test_api.py -v

# Frontend
cd frontend && npm install && npm run dev   # Dev server at http://localhost:5173

# Docker (production frontend + backend)
docker compose up --build

# mex memory CLI
mex check                  # drift score
mex sync                   # build prompts to fix flagged files
mex log "<message>"        # append to .mex/events/decisions.jsonl
```

See [[.mex/context/setup.md]] for the full set, including Windows automation and common issues.

## Interpreting `mex check` output

`mex check` reports `MISSING_PATH` for any slash-bearing token its regex parses as a file path. The detector is over-eager — most findings are false positives. Treat as noise unless verified:

- **URLs** — `http://...`, `https://...`, `socks5://...` (anything with `://`)
- **Globs** — `db/*.py`, `operators/*.py`, `tests/test_*.py`
- **Pytest paths** — `foo.py::ClassName`, `foo.py::test_bar` (the `::Name` suffix breaks the literal-path match)
- **Line ranges** — `strategy/regime.py:372-377` (the `:line-range` suffix breaks the match)
- **Code fragments with `/`** — `try/except`, `pnl_pct / sl_pct_actual`, `atr_sl_mult/tp/be`, `IdempotencyCache.get/.set`, `chmod -R 444/555`
- **Runtime-created files** — `logs/*.log`, `data/symbols_status.json`, `data/regime_cache.json` (created by the running services; absent on a fresh checkout)
- **Historical artefacts** — `data/retune/2026-05-06-*/...` and similar dated reports that may have been moved or archived

Before treating a `MISSING_PATH` finding as real drift: strip the `::Name` / `:line-range` suffix, then verify the underlying file with `Glob` or `Read`. If it exists, the finding is noise — **leave the doc alone**.

Real drift looks like one of these:
1. A file actually deleted from the repo (`git log --diff-filter=D` confirms removal).
2. A module renamed but still cited by old name in a context/ or pattern file.
3. A doc claims a writer creates `path/foo.json`, but the writer code is gone.
4. A pattern's `## Steps` cites an API/function signature that no longer matches the source.

When fixing real drift: edit the smallest possible scope (one line, one section) — do not rewrite the file. After the fix, append a one-liner via `mex log "fixed drift: <what>"` so the event clock records it.

## Scaffold Growth

After a task: if no pattern existed, create one in `.mex/patterns/` and add it to `.mex/patterns/INDEX.md`. If an existing pattern needed an update, edit it surgically. If a `.mex/context/` file is now out of date, update it. See [[.mex/ROUTER.md]] §Behavioural Contract.

## Navigation

Read [[.mex/ROUTER.md]] next. It contains the routing table, the current project state (Working / Not built / Known issues), and the per-task CONTEXT → BUILD → VERIFY → DEBUG → GROW loop.

## User-language note

User writes in Venezuelan Spanish. `leete` / `léete` means "léelo" (read it), not "delete it". Respond in Venezuelan Spanish when the user does.
