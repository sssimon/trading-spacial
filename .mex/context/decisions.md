---
name: decisions
description: Validation methodology (holdout dataset, A.4 caveats, leakage audit), inviting-users guardrail (#271, closed 2026-05-16), Cluster D status, and known limitations. Load before touching ATR tuning, holdout reads, regime thresholds, or onboarding a non-Samuel user.
triggers:
  - "holdout"
  - "validation"
  - "A.4"
  - "leakage"
  - "re-tune"
  - "bankruptcy"
  - "K=10"
  - "overshoot"
  - "regime threshold"
  - "epic A"
  - "epic B"
  - "invite user"
  - "papá"
  - "tenant"
edges:
  - target: context/architecture.md
    condition: when a validation caveat depends on understanding sizing or the regime detector
  - target: context/conventions.md
    condition: when an invariant or schema decision is being revisited
  - target: patterns/holdout-access.md
    condition: when the work needs to read from data/holdout/
last_updated: 2026-05-26
---

# Decisions

<!-- HOW TO USE THIS FILE:
     This is the event clock. When a decision changes: DO NOT delete the old entry.
     Mark it as superseded, add the new entry above it. History is the point. -->

## Validation Methodology — Holdout Dataset (epic #246, ticket #247)

The repo contains a **locked holdout dataset** at `data/holdout/` that must NOT be touched by scanner / auto_tune / backtest tuning code paths. It exists so that strategy parameter changes can be validated honestly out-of-sample.

- **Cutoff:** fixed (not rolling), 12 months back from the lock date `2026-04-30`. Holdout window starts `2025-04-30T00:00:00 UTC`.
- **Contents:** OHLCV (10 curated symbols × 4 timeframes), Fear & Greed daily, BTC funding rate. SHA-256 + commit recorded in `data/holdout/MANIFEST.json`.
- **Filesystem state:** `chmod -R 444/555`, read-only.
- **Authoritative provenance doc:** `docs/superpowers/specs/es/2026-04-30-a1-holdout-dataset-provenance.md` — read this before A.2/A.4 work.

### Read-guard policy (decision: A + B with B reinforced)

- **Guard A — `data/holdout_access.py`** is the **only** legitimate read entry point: `open_holdout(rel_path, *, evaluation_mode=True)` returns the resolved Path. Anything else raises `HoldoutAccessError`. **No monkey-patch / env override** is offered — A is opt-in ergonomics by design.
- **Guard B — `tests/test_holdout_isolation.py`** is the structural net. AST scanner walks every `.py` in the repo and fails CI if any non-whitelisted module references the holdout via string literal, `*.join(..., 'holdout', ...)`, `Path / 'holdout' / ...`, or f-string with `'holdout'`. Docstrings are skipped.
- **To use the holdout from a new module** (A.2 walk-forward, A.4 evaluation): either call `open_holdout(..., evaluation_mode=True)` and never reference the path directly, or add the module to `HOLDOUT_LEGITIMATE_MODULES` in `tests/test_holdout_isolation.py` with a justification reviewed in the PR.

See [[../patterns/holdout-access.md]] for the runbook.

### Caveats heredados — A.4 (#250) MUST honor

1. **Re-tune required.** The current `atr_sl_mult/tp/be` were tuned over the full history including the holdout range. A.4 must re-tune over `[earliest, holdout_start - 1 bar]` BEFORE evaluating against the holdout (else: leakage).

   **Audit of leakage scope** (verified 5 mayo 2026 in spec D9 §2.9 amendment review):

   | Param | Source | Window | Leaked into holdout? |
   |-------|--------|--------|----------------------|
   | ATR multipliers (10 × {sl, tp, be} = 30 values) | Iterative tuning pre-A.4 (#121 + iterations) | Full history (incl. holdout range) | **YES** — being fixed by A.4-1 |
   | Time-limits per-symbol (10) | #281 diagnostic, "winner-median holding" + research §5 | `[2023-10-29, 2025-04-29]` (sim_end un día antes del corte locked) | NO |
   | Max participation rate per-symbol (10) | Almgren-Chriss + Donier-Bonart academic anchors | N/A (no data fit) | NO |
   | Cooldown per-symbol (10) | Rule: `max(time_limit, NW=4, floor=6)` | Transitive of TL (not leaked) | NO |
   | Tier mapping (cost-based per-symbol cap assignment) | #281 cost spectrum | `[2023-10-29, 2025-04-29]` | NO |
   | Score tiers `{0.5, 1.0, 1.5}` (operator partition with arithmetic sizing convention; values + thresholds `SCORE_PREMIUM=4`/`SCORE_STANDARD=2` stable from inception per `git log -p` depth-2) | Hardcoded constants — depth-2 verified | N/A | NO |
   | RISK_PER_TRADE = 0.01 (Van Tharp / standard finance convention; stable from inception per depth-2) | Hardcoded constant — depth-2 verified | N/A | NO |
   | Regime thresholds `{>60, <40}` (`strategy/regime.py:372-377`, `backtest.py:404-409`) | **Optimized via backtest** in commit `bf581f1` (2026-04-18) over 4 documented configs `{(60,40), (70,30), (80,20), no detector}`. Window de optimización: undocumented en commit/changelog/script. Inferred to include data through ~2026-04-18 based on commit timestamp and absence of cutoff specification. If the inferred window is incorrect, the leakage analysis may differ — but absence of documentation is itself the methodological problem we're correcting. | Inferred `[..., 2026-04-18]` (overlaps holdout `[2025-04-30, 2026-04-30]`) | **YES** — re-tune required pre-Phase-3 (issue separado A.4-1.5; spec D9 §2.10) |

   Hardcoded constants en estas filas son rule/principle-derived (operator-chosen partitions, convention-derived risk percentages), no data-derived-then-frozen — verified pre-Phase-3 via depth-2 archaeology (`git log -p` con value-change filter sobre cada constant). Excepción detectada en archaeology depth-2: regime thresholds `>60/<40` fueron data-derived; escape clause activada → issue separado A.4-1.5 abierto, mini-harness paralelo a A.4-1, gating Phase 3. Si en el futuro se descubre otra constante data-derived-then-frozen no listada arriba, abrir issue separado siguiendo el mismo patrón.
2. **Regime composition not guaranteed.** The 12-month window may not cover all regimes. A.4 must report bull/bear/neutral mix and call out gaps.
3. **Drift not auto-detectable.** F&G and funding rate hashes freeze the snapshot at fetch time. A.4 must re-fetch + diff against source APIs to detect provider revisions.
4. **Per-symbol vs portfolio aggregation gap.** The backtest simulator computes `sum(net_pnl)` across independent per-symbol streams; per-symbol `INITIAL_CAPITAL=$10K` floors at $0 individually (`effective_capital = max(0, capital)` in `_close_position`) but the trade that crosses zero is unbounded by `capital_open` via the `pnl_pct / sl_pct_actual` amplification ratio (especially under TIME_LIMIT exits with tight SL multipliers).

   PR #309 addresses the per-trade overshoot via a symmetric `K=10` cap (`MAX_OVERSHOOT_RATIO` in `backtest.py`). The principle that no realistic execution holds through a 10× SL move is rule-derived from standard risk-management practice; the specific value `K=10` is chosen as a canonical conservative threshold rather than empirically tuned, and is subject to revision under explicit pre-registration if downstream evidence supports it. Post-cap, `|pnl_usd| ≤ K × risk_amount = K × max(0, capital) × RISK_PER_TRADE × size_mult`. Observability: `trade["overshoot_clamped"]` (bool, AND-gated with `risk_amount > 0`) + `metrics["clamped_trade_count"]` (int) surface cap-binding incidence.

   **K-cap bounds the per-trade overshoot mechanism but does NOT implement pooled-portfolio capital management.** Each symbol's $10K remains independent; portfolio-level allocation, cross-symbol position halt, and aggregate drawdown enforcement are out of scope for PR #309. Phases requiring pooled-portfolio semantics need separate infrastructure work (deferred — separate future epic).

   **A.4 phases (Phase 3 ATR re-tune #287, Phase 4 review, A.4-2 walk-forward, A.4-3 holdout evaluation) using "sum net_pnl across portfolio" or analogous aggregate inherit this gap and MUST acknowledge in interpretation tree.** Specifically: holdout interpretation MUST report `clamped_trade_count` per symbol and per config; if `clamped_trade_count > 0` for any symbol/config combination, interpretation MUST note that the result reflects cap-bounded behavior on those trades; if `clamped_trade_count` accounts for `>5%` of trades for any symbol, interpretation MUST consider whether the metric is measuring strategy edge or cap-binding behavior. The 5% threshold is a starting heuristic; revise via pre-registration if a more defensible threshold emerges.

   **Per-symbol bankruptcy halt (PR #313, #280) addresses the silent-continued-fictional-trading sub-gap at the per-symbol level.** Once a symbol's simulated equity falls below `BANKRUPTCY_THRESHOLD = 0.1 × INITIAL_CAPITAL` ($1000 with current `INITIAL_CAPITAL=10_000.0`), `simulate_strategy` emits a single `exit_reason="BANKRUPT"` trade record and halts new entries for that symbol; existing open positions still close naturally via SL/TP/TIME_LIMIT. `calculate_metrics` excludes BANKRUPT records from win-rate / PF / Sharpe / Sortino / streaks / score-tier aggregates; `max_drawdown_pct` and `total_return_pct` are unaffected (they derive from `equity_curve`). The metrics dict carries `bankruptcy_count` for operator visibility. **Portfolio-level bankruptcy handling remains deferred** — a portfolio-level simulator (when it lands) will need its own ticket to pool capital across symbols and decide whether one symbol's bankruptcy should halt the whole portfolio or just that symbol's stream. For A.4-1, A.4-1.5, A.4-2, and A.4-3, the per-symbol fix is sufficient: each symbol's $10K stream is now bounded both per-trade (K=10 cap) and at the bankruptcy floor.

   Discovered during A.4-1.5 sweep halt (2026-05-04, #305) — sanity check fired with PENDLE showing $-1,702,401 = 170× initial capital, traced to single-trade overshoot via amplification. Spec D9 §2.10 + `docs/superpowers/research/2026-05-02-structural-fix-parameter-study.md` document the methodological framing. The Bankruptcy Bias sub-gap surfaced concretely in `data/retune/2026-05-06-pre-holdout/regime_report.md` (A.4-1.5 sweep) — `no_detector` "won" the raw `sum(net_pnl)` only because JUPUSDT went bankrupt under it and the simulator continued processing zero-`risk_amount` trades; the reviewer had to operator-override to ship `60_40`. PR #313 (#280) removes the need for that override going forward.

   **Note on framing:** of the structural fixes correcting pre-#223 backtest inflation, **#223/#224 (sign error in `_close_position`) and #313 (post-bankruptcy ghost trades) are bug fixes** — previous results were calculation errors, not just imprecise calibration. Only **#309 (K=10) is a modeling decision** with its own uncertainty band. Don't conflate these in narrative: the replacement framing "previous backtests reflected simulator bugs, not strategy behavior" is more accurate and methodologically stronger than "we made the simulator more realistic". See PR #316 inflection-point spec §A.2 for the full unpacking.

5. **A.4-3 holdout execution blocked until re-tune produces viable candidates (issue #322).** The 2026-05-11 A.4-1 attempt returned NO_DATA across 10 symbols × 105 grid points under the post-fix simulator (`cfg + symbol_overrides` path, gates active). The grid topology diagnostic + Bayesian update pending (issue #318) before any decision about expanding the grid OR escalating to stakeholder paths (issue #321). Until #322 closure criteria are all met (re-tune produces candidates AND A.4-2 walk-forward passes AND drift check on holdout snapshots completed), do NOT execute A.4-3 — no calling `simulate_strategy` with holdout-window frames, no `open_holdout(..., evaluation_mode=True)`, no harness runs even "just to see". Partial information from a holdout peek burns the bala única just as surely as a full run.

   **Agent tooling note (Bayesian update mechanics).** The skill `pymc-bayesian-modeling` (installed 2026-05-15, globally available; `Skill` tool name `pymc-bayesian-modeling`) is the canonical tool when an §A.4 prior re-evaluation checkpoint needs to be materialized as a quantified posterior — PyMC + NUTS sampling + LOO/WAIC model comparison + posterior predictive checks. Invoke it for: the #318 posterior over grid-coverage probability; A.4-1.5 model comparison across the 4 regime configs `{60_40, 70_30, 80_20, no_detector}`; the regime-allocation Phase 3 verdict-conditional posterior over P(strategy viable); any hierarchical symbol × config posterior on sweep grids. Do NOT invoke it for the institutional 2-3-sentence prose magnitude updates (R1/R2/R3/Phase-2/3 pre-reg §A.4 checkpoints by default produce prose only). The §A.4 pattern is prose-by-default and PyMC-on-demand.

## Inviting users — guardrail (#271, CLOSED 2026-05-16)

The original guardrail (Epic A passes + Epic B implemented) was **overridden 2026-05-15** and **closed 2026-05-16**. Inviting non-Samuel users (papá, María, etc.) on `trading.sdar.dev` is now **unblocked**.

**What changed:**
- **Epic A waived** — archived as terminal-not-passed (PR #316 inflection-point: edge inflated by simulator bugs; #338 regime-allocation pivot returned `PHASE_3_INSUFFICIENT_DATA`; PR #357 Direction A verdict `EDGE_WEAK`, only Q2 operator-discretion exit timing confirmed as edge). Framing shifted to "operator-discretion + per-user data isolation" — invitees use the system as operator-filters of their own positions, not as auto-strategy users. The original "share validated strategy" risk no longer applies.
- **Epic B (#253) shipped** — B.1→B.8 all merged in `080a74e`. B.7 IDOR suite green (17/17). B.8 production migration executed 2026-05-16 (3,306 signal_outcomes + 410 notifications stamped `tenant_id=1`, capital row created, zero downtime).

**Operational checklist when inviting a new user** (from #271 closure comment):
1. Create the account via the `auth` flow
2. Verify they only see their own positions / notifications in the UI (no leakage from Samuel's data)
3. Confirm the per-user signal dispatcher (B.4, `notifier/dispatch_per_user.py`) routes scanner output to both users
4. Monitor for 1–2 weeks alongside the B.8 backup-retention empirical validation (signal-dispatch row + capital update + clean window — see `project_b8_backup_retention.md`)
5. If a real isolation issue surfaces (something the IDOR suite missed), **reopen #271** and document the gap before continuing

## Known Limitations

- `watchdog.py` uses Windows-specific commands (`tasklist`, `taskkill`, `wmic`, `netstat`) and won't run on Linux/Mac
- The webhook process itself is not supervised by the watchdog (only `btc_api.py` is)
- Strategy backtest numbers in `docs/superpowers/specs/es/2026-04-17-formula-ganadora-resultados-finales.md` and `docs/superpowers/specs/es/2026-04-18-documento-completo-sistema-trading.md` are **pre-#223/#224** (phantom-profit fix). The "real strategy contribution" decomposition in PR #223 showed those numbers were inflated. **Do not cite those numbers as baseline** — see #272 for the re-baselining work.
