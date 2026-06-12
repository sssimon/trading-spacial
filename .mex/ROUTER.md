---
name: router
description: Session bootstrap and navigation hub. Read at the start of every session before any task. Contains project state, routing table, and behavioural contract.
edges:
  - target: context/architecture.md
    condition: when working on system design, signal flow, sizing, cost model, regime detector
  - target: context/stack.md
    condition: when working with specific technologies, libraries, ports, or the SSRF guard
  - target: context/conventions.md
    condition: when writing new code that touches db/, operators/, auth/, api/, or schema migrations
  - target: context/decisions.md
    condition: when touching ATR tuning, holdout reads, regime thresholds, inviting users, or aggregating across symbols
  - target: context/setup.md
    condition: when starting services, debugging boot-up, or installing dependencies
  - target: patterns/INDEX.md
    condition: at the start of every task — check the pattern index for a matching pattern file
last_updated: 2026-06-04
---

# Session Bootstrap

If you haven't already read `CLAUDE.md`, read it now — it contains the project identity, non-negotiables, and commands.

Then read this file fully before doing anything else in this session.

## Current Project State

**Working:**
- BTC/USDT signal generation across 10 curated symbols, multi-timeframe (4H macro → 1H signal → 5M trigger).
- Position lifecycle via `PositionClosure` operator (atomic capital roll-in + post-commit health/notify/snapshot).
- Cluster D enforcement (#471 / #470 / #473) merged: `qty > 0`, `tenant_id NOT NULL`, idempotency unique-index, body-fingerprint idempotency.
- Per-user data isolation (Epic B / #253) shipped; inviting non-Samuel users on `trading.sdar.dev` unblocked since 2026-05-16.
- Holdout dataset locked at `data/holdout/`; read-guard A (`open_holdout`) + B (CI AST scanner) active.
- Binance v0.3: SL/TP observados (openOrders → observed_orders + resumen fuente-de-verdad en filas EXTERNAL).
- Vista Valles A: screener de vida + consolidación (observabilidad, lista neutral; ranking=celda B diferida, dossier=C diferido).
- Dossier C: due-diligence de hechos citados (Exa + DeepSeek extracción), sin veredicto, caché TTL 7d, botón en Valles.

**Not yet built:**
- Portfolio-level capital pooling, leverage cap across symbols, aggregate drawdown enforcement (deferred — separate future epic).
- A.4-3 holdout evaluation (blocked until #322 closure criteria met: re-tune candidates + A.4-2 walk-forward + drift check).
- `PositionOpen` symmetric operator — by design (see [[context/conventions.md]] §Principio dual de la frontera Cluster D).
- Regime-allocation strategy class validation Phases 2–6 (flag OFF by default in `config.defaults.json`).

**Known issues (deferred, with issue numbers):**
- F-05 per-close, not per-tick, in Phase 2 of `check_position_stops` — #453.
- Rate limiting on `POST /positions` — #482 (Advances #473, not closes).
- Direction enum sólo en boundary — #484.
- `scan_id` referential integrity — #483.
- Idempotency cache eager sweeper, `entry_ts` window relaxation, `legacy_no_tenant` consumer filter audit — sin issue formal aún.
- Pre-#223/#224 backtest numbers in older specs are inflated. **Do not cite as baseline.** See #272.

## Routing Table

Load the relevant file based on the current task. At the start of every task, check `patterns/INDEX.md` first.

| Task type | Load |
|-----------|------|
| Any specific task | `patterns/INDEX.md` first — match before reading context |
| Understanding signal flow, sizing, cost model, regime detector | `context/architecture.md` |
| Touching db/, operators/, auth/, api/, or schema migrations | `context/conventions.md` |
| Re-tuning ATR, reading holdout, changing regime thresholds, inviting a user | `context/decisions.md` |
| Adding a library, touching config.json, auditing webhook SSRF | `context/stack.md` |
| Starting services, install issues, watchdog setup | `context/setup.md` |
| Considering admin-merge / CI bypass / failing tests on a PR | `context/ci-discipline.md` |
| Writing a PR body — choosing the right verb for `<Verb> #N` references | `context/verb-taxonomy.md` |
| Estudiar una celda de edge (programa Edición 1), preparar dossier de deploy | `patterns/estudiar-una-celda.md` + `programa/INDEX.md` |

## Behavioural Contract

For every task, follow this loop:

1. **CONTEXT** — Load the relevant context file(s) from the routing table above. Check `patterns/INDEX.md` for a matching pattern. If one exists, follow it. Narrate what you load: "Loading conventions context…"
2. **BUILD** — Do the work. If a pattern exists, follow its Steps. If you are about to deviate from an established pattern, say so before writing any code — state the deviation and why.
3. **VERIFY** — Load `context/conventions.md` and run the Verify Checklist item by item. State each item and whether the output passes. Do not summarise — enumerate explicitly. Also run the Verify Checklist of any pattern you used.
4. **DEBUG** — If verification fails or something breaks, check `patterns/INDEX.md` for a debug pattern. Follow it. Fix the issue and re-run VERIFY.
5. **GROW** — After completing the task:
   - If no pattern existed for this task type, create one in `patterns/` using the format already used by the existing patterns. Add it to `patterns/INDEX.md`. Flag it: "Created `patterns/<name>.md` from this session."
   - If a pattern existed but you deviated or discovered a new gotcha, update it surgically — do not rewrite the whole file.
   - If any `context/` file is now out of date because of this work, update it surgically.
   - Update the "Current Project State" section above if the work was significant.

## Append-only event log

For decisions worth remembering across sessions (e.g., "we ruled out approach X because Y"), append a one-line note via `mex log "<message>"`. Stored at `.mex/events/decisions.jsonl`.
