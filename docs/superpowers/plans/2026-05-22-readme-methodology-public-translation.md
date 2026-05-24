# README + METHODOLOGY Public Translation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Translate the hidden research-methodology moat (pre-registration, holdout isolation, structural fixes, cost model v2) into public-facing artifacts (`METHODOLOGY.md` + `docs/README.md` + rewritten `README.md`) so an outside reader perceives the actual rigor of the project rather than "another crypto scanner V6.0".

**Architecture:** Three coordinated docs. `METHODOLOGY.md` (new, at repo root) is the moat showcase — anchored in current state, links out to canonical specs. `docs/README.md` (new) is the navigation surface over the 37 specs + 35 plans + research notes already in `docs/superpowers/`. `README.md` (rewritten) is the operator-facing entry point: drops `Ultimate Macro V6.0` branding, unifies to English, updates Project Structure / Signal Logic / Architecture to reflect current code (regime detector, K-cap, BANKRUPT halt, multi-tenant dispatcher, vol-targeting Phase 1), and adds a prominent link to METHODOLOGY.md.

**Tech Stack:** Markdown only. `gh repo edit` for GitHub metadata (description / topics / homepage).

**Out of scope (deferred to their own tickets):**
- Fixing `strategy/` vs `strategies/` split (#2 in audit — separate decision)
- Documenting prod systemd / what supervises Linux EC2 (#7 in audit — separate ticket)
- `mempalace.yaml` relocation (#9 — verified benign during audit, micro-move)
- Tagging retroactive commits (#8 — separate cleanup pass)

---

## File Inventory

**To create:**
- `METHODOLOGY.md` — repo root, ~400-600 lines, public-facing moat doc
- `docs/README.md` — ~80-120 lines, navigation index

**To modify:**
- `README.md` — full rewrite of header, architecture, signal logic, project structure, stack table, troubleshooting (translate to English), deployment checklist (translate); preserve the practical "how to run" command blocks that work

**External (no file change, but action required):**
- `gh repo edit` to set description, topics, homepageUrl

**To reference (read-only):**
- `CLAUDE.md` — primary source of current-state truth
- `docs/superpowers/specs/es/2026-04-30-a1-holdout-dataset-provenance.md` — holdout
- `docs/superpowers/specs/es/2026-04-18-documento-completo-sistema-trading.md` — system overview (with pre-#223 caveat)
- `docs/superpowers/specs/es/2026-05-01-operational-model-manual-gating.md` — operational model
- `docs/superpowers/specs/es/2026-05-11-a4-hallazgo-inflexion-metodologica.md` — inflection point #316
- `docs/superpowers/specs/es/2026-05-11-strategy-structural-audit.md` — structural audit
- `docs/superpowers/specs/es/2026-05-13-epic-regime-allocation-strategy-pivot.md` — current pivot
- `docs/superpowers/specs/es/2026-05-16-multi-tenant-threat-model.md`
- `docs/superpowers/research/2026-05-02-structural-fix-parameter-study.md` — K-cap study

---

## Verification Pattern (adapted from TDD for docs)

Each content task uses: **draft → self-review against checklist → commit**.

Each task ends with explicit acceptance criteria (instead of `pytest PASS`). Self-review against the criteria before committing. If the criteria fail, fix inline before moving to the next task.

---

### Task 1: Draft `METHODOLOGY.md` skeleton

**Files:**
- Create: `METHODOLOGY.md`

- [ ] **Step 1: Create the file with section structure only**

Write to `METHODOLOGY.md`:

```markdown
# Methodology

> Why this project looks like a trading scanner but behaves like a research artifact.

## What this project actually is

(TBD — Task 2)

## Pre-registration discipline

(TBD — Task 2)

## Holdout dataset isolation

(TBD — Task 2)

## How to read the backtest numbers

(TBD — Task 3)

## Structural fixes shipped (and what they say about prior numbers)

(TBD — Task 3)

## Cost model v2 — why naive `slippage = participation × spread` is wrong

(TBD — Task 4)

## Operational model — signals are not trades

(TBD — Task 4)

## Where the research is going

(TBD — Task 4)

## How to evaluate any claim in this repo

(TBD — Task 5)

## References

(TBD — Task 5)
```

- [ ] **Step 2: Verify acceptance criteria**

Open `METHODOLOGY.md`. Confirm:
- File is at repo root (not in `docs/`)
- 10 H2 headings present in the order above
- No content yet — only TBD placeholders that will be filled by Tasks 2-5

- [ ] **Step 3: Commit**

```bash
git add METHODOLOGY.md
git commit -m "$(cat <<'EOF'
docs(methodology): skeleton — 10 sections, TBDs filled in subsequent commits

Empty section structure committed first to lock the outline before writing
content. Each subsequent commit fills 2-3 sections.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Fill `METHODOLOGY.md` — What it is, Pre-registration, Holdout

**Files:**
- Modify: `METHODOLOGY.md`

- [ ] **Step 1: Replace the "What this project actually is" section**

Replace the `(TBD — Task 2)` placeholder under `## What this project actually is` with:

```markdown
On the surface this is a Bitcoin / altcoin signal scanner with a React dashboard. Underneath, it's something rarer in retail trading: **a working laboratory for evaluating systematic strategies with research-grade discipline**.

Concretely, this repo enforces:

- A **locked holdout dataset** (12 months of OHLCV + Fear & Greed + funding rate, SHA-256 + commit hashed in `data/holdout/MANIFEST.json`, filesystem `chmod -R 444/555`) that strategy code and parameter tuning paths cannot read. Two independent guards prevent leakage: a runtime guard (`data/holdout_access.py` is the only legitimate read entry point) and a structural CI guard (`tests/test_holdout_isolation.py` AST-scans every `.py` for any reference to the holdout path).
- **Pre-registration** of every methodology change: hypothesis, decision rule, gates, and abort conditions written down *before* running the experiment. See `docs/superpowers/specs/es/` for ~40 such pre-regs going back to April 2026.
- **Bugs vs. modeling** framing on every result: if a backtest improves, was it because the strategy got better or because a simulator bug got fixed? PRs #223, #224, #309, #313 are all documented inflection points where prior results were known to be inflated by simulator behavior, not strategy edge. See *Structural fixes shipped* below.

If you are evaluating this project as a strategy to trade live: **don't, yet.** Phase 3 of the regime-allocation pivot returned `PHASE_3_INSUFFICIENT_DATA` in May 2026; Direction A of the post-inflection re-baselining returned `EDGE_WEAK`. The only confirmed edge to date is **operator-discretion exit timing** (Q2). The dashboard exists to surface signals for a human to evaluate, not to execute them automatically.

If you are evaluating this project as a methodology artifact: read on.
```

- [ ] **Step 2: Replace the "Pre-registration discipline" section**

Replace the placeholder with:

```markdown
Every non-trivial decision that touches the simulator, parameter grid, or evaluation rule goes through a pre-registration document in `docs/superpowers/specs/es/<date>-<topic>-pre-reg.md`. The pre-reg locks:

1. **Hypothesis** — what we expect to find
2. **Decision rule** — exact numerical gates and what each outcome means
3. **Methodology** — data window, train/test split, metrics, statistical treatment
4. **Abort conditions** — what would make us stop and revisit

The pre-reg is committed *before* any code that consumes the data runs. After execution, a separate `-result.md` or follow-up PR records the outcome against the pre-registered gates. If the result contradicts the hypothesis or a gate fails, we don't move the goalposts — we document the failure and either pivot or abort.

Examples:
- [`2026-05-11-a4-hallazgo-inflexion-metodologica.md`](docs/superpowers/specs/es/2026-05-11-a4-hallazgo-inflexion-metodologica.md) — discovered during A.4 re-tune that the entire historical edge was simulator-bug-inflated. Did not paper over; documented and pivoted.
- [`2026-05-13-r3-fail-closure-path-a-honoring.md`](docs/superpowers/specs/es/2026-05-13-r3-fail-closure-path-a-honoring.md) — R3 trend-pullback hypothesis pre-registered with a hard gate. Failed the gate. Closed honestly.
- [`2026-05-13-epic-regime-allocation-strategy-pivot.md`](docs/superpowers/specs/es/2026-05-13-epic-regime-allocation-strategy-pivot.md) — when the LRC strategy class hit `EDGE_WEAK`, pre-registered a structurally distinct alternative (regime-allocation) with locked parameters, mutual-exclusion gating, and Phase 2-6 plan.

This discipline exists because retail trading literature is saturated with results that fail out-of-sample. The defense is institutional, not technical: write down the rule before you see the data.
```

- [ ] **Step 3: Replace the "Holdout dataset isolation" section**

Replace the placeholder with:

```markdown
The holdout dataset at `data/holdout/` is the project's single most valuable artifact and is governed accordingly.

**Lock parameters:**
- 12-month fixed window (not rolling): `2025-04-30T00:00:00 UTC` → `2026-04-30`
- 10 curated symbols × 4 timeframes of OHLCV + Fear & Greed daily + BTC funding rate
- SHA-256 + commit hash recorded in `data/holdout/MANIFEST.json`
- Filesystem state `chmod -R 444/555` (read-only)

**Two-layer access guard:**

1. **Runtime guard (Guard A)** — `data/holdout_access.py` exposes a single function `open_holdout(rel_path, *, evaluation_mode=True)` that returns the resolved Path. Anything else raises `HoldoutAccessError`. There is no monkey-patch escape hatch, no env var override.

2. **Structural guard (Guard B)** — `tests/test_holdout_isolation.py` AST-scans every `.py` file in the repo on CI. Any non-whitelisted module that references the holdout path via string literal, `os.path.join(..., 'holdout', ...)`, `Path / 'holdout'`, or f-string with `'holdout'` fails the build. Docstrings are skipped. The whitelist (`HOLDOUT_LEGITIMATE_MODULES`) is small and reviewed in PR.

To use the holdout from a new module: either call `open_holdout(..., evaluation_mode=True)` and never reference the path directly, or add the module to the whitelist with explicit justification.

**The reason for two layers:** Guard A is opt-in ergonomics. Guard B is the structural net that catches mistakes — including AI-assisted refactors that might naively grep for paths. Belt and suspenders.

**Leakage caveats inherited (must be honored by future evaluation passes):**
- ATR multipliers (10 × {sl, tp, be} = 30 values) were tuned over full history *including* the holdout range. A re-tune over `[earliest, holdout_start - 1 bar]` is required before evaluating against the holdout. Tracked in issue [#322](https://github.com/sssimon/trading-spacial/issues/322).
- Regime thresholds `>60/<40` were also data-derived during the 4-config optimization in commit `bf581f1` (2026-04-18); window undocumented in commit/changelog. Treated as leaked-pending-re-tune. Tracked in issue A.4-1.5.
- Other constants (RISK_PER_TRADE=0.01, score thresholds, K=10 overshoot cap) were verified rule/principle-derived (not data-derived-then-frozen) via `git log -p` depth-2 archaeology.

Full provenance: [`2026-04-30-a1-holdout-dataset-provenance.md`](docs/superpowers/specs/es/2026-04-30-a1-holdout-dataset-provenance.md).
```

- [ ] **Step 4: Verify acceptance criteria**

Open `METHODOLOGY.md`. Confirm:
- 3 sections (`What this project actually is`, `Pre-registration discipline`, `Holdout dataset isolation`) now have content
- Each section links to at least one canonical spec via relative path
- All relative paths point to files that actually exist (spot-check 2-3 with `ls`)
- Tone is sober, not promotional ("don't trade this yet" callout intact)

- [ ] **Step 5: Commit**

```bash
git add METHODOLOGY.md
git commit -m "$(cat <<'EOF'
docs(methodology): fill — what-it-is, pre-registration, holdout guards

Three foundational sections covering the moat: project framing
(operator-discretion + research artifact, not auto-trader), pre-registration
discipline with examples, and the two-layer holdout access guard
(runtime + AST-scan CI).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Fill `METHODOLOGY.md` — Backtest reading, Structural fixes

**Files:**
- Modify: `METHODOLOGY.md`

- [ ] **Step 1: Replace the "How to read the backtest numbers" section**

Replace the placeholder with:

```markdown
Any number in this repo — backtest P&L, Sharpe ratio, win rate, drawdown — needs to be read against three questions:

1. **Pre or post #223/#224?** PR #223 fixed a sign error in `_close_position` that inflated historical results. Numbers from specs dated before 2026-04-25 (notably [`2026-04-17-formula-ganadora-resultados-finales.md`](docs/superpowers/specs/es/2026-04-17-formula-ganadora-resultados-finales.md) and [`2026-04-18-documento-completo-sistema-trading.md`](docs/superpowers/specs/es/2026-04-18-documento-completo-sistema-trading.md)) are **pre-fix and known-inflated**. Do not cite them as baseline.

2. **K-cap binding?** PR #309 added a symmetric `K=10` per-trade overshoot cap. Without it, single trades on thin bars could amplify into `170× initial_capital` losses (the PENDLE case during A.4-1.5 sweep). With it, any trade where `clamped_trade_count > 0` reflects cap-bounded behavior on those bars, not strategy edge. The metrics dict surfaces `clamped_trade_count` per symbol — if it accounts for `>5%` of trades, the headline number is measuring the cap, not the strategy.

3. **Bankruptcy halt fired?** PR #313 added per-symbol bankruptcy halt: when simulated equity drops below `0.1 × INITIAL_CAPITAL`, the symbol emits a `BANKRUPT` exit and stops opening new positions for the simulation. Pre-#313, the simulator continued issuing fictional zero-risk trades after bankruptcy, which silently inflated `sum(net_pnl)` for any config that drove a symbol broke. The A.4-1.5 sweep had to operator-override the regime config because `no_detector` "won" only via post-bankruptcy ghost trades on JUPUSDT. Always check `bankruptcy_count` in the metrics dict.

**Recommended framing in narrative**: previous backtests reflected simulator bugs (#223, #313) and modeling decisions (#309), not pure strategy behavior. The bug fixes recovered real numbers; the modeling cap (K=10) is a calibration with its own uncertainty band — don't conflate the two.
```

- [ ] **Step 2: Replace the "Structural fixes shipped" section**

Replace the placeholder with:

```markdown
| PR | Date | Type | What it fixed | Effect on prior numbers |
|---|---|---|---|---|
| [#223](https://github.com/sssimon/trading-spacial/pull/223) / #224 | 2026-04-25 | **Bug fix** | Sign error in `_close_position` that double-counted PnL on certain exit paths | Pre-fix numbers were inflated; not a "calibration improvement" |
| [#296](https://github.com/sssimon/trading-spacial/pull/296)+#297+#298+#299 | 2026-05-03 | **Triple Barrier structural fix** | Time-limit barrier, participation cap, per-symbol overrides honored in live + backtest paths | Closed the legacy `atr_*` kwargs bypass for the live path |
| [#309](https://github.com/sssimon/trading-spacial/pull/309) | 2026-05-11 | **Modeling decision** | Symmetric K=10 per-trade overshoot cap. Bounds `\|pnl_usd\| ≤ K × risk_amount` | Realistic; bounds the catastrophic-bar mechanism without enforcing pooled-portfolio capital management |
| [#313](https://github.com/sssimon/trading-spacial/pull/313) | 2026-05-11 | **Bug fix** | Post-bankruptcy ghost trades. Symbol halts at `0.1 × INITIAL_CAPITAL` floor | Closed the silent-continued-fictional-trading sub-gap; metrics dict now carries `bankruptcy_count` |
| [#329](https://github.com/sssimon/trading-spacial/pull/329) | 2026-05-12 | **Phase 2 R1 outcome** | SIGNAL_EXIT branch kept flag-gated False on live — mechanism engaged in backtest, profitability absent | Honest closure of a hypothesis that failed its pre-registered gate |

**The framing matters**: #223, #224, #313 are bugs. The simulator was wrong; fixing it recovers real numbers. #309 is a modeling decision with its own uncertainty band (`K=10` chosen as conservative threshold, not empirically tuned). Don't conflate "we fixed bugs" with "we made the simulator more realistic" — the former is methodologically stronger.

Full reasoning: PR #316 inflection-point spec §A.2 + [`2026-05-02-structural-fix-parameter-study.md`](docs/superpowers/research/2026-05-02-structural-fix-parameter-study.md).
```

- [ ] **Step 3: Verify acceptance criteria**

Open `METHODOLOGY.md`. Confirm:
- "How to read backtest numbers" gives 3 concrete questions with reasoning
- Structural fixes table lists at least 5 rows with PR links + categorization (bug vs modeling)
- "Bugs vs modeling" framing is preserved per the user-memory `feedback_bugs_vs_modeling_framing`
- PR numbers are accurate (spot-check 2-3 with `gh pr view <num>`)

- [ ] **Step 4: Commit**

```bash
git add METHODOLOGY.md
git commit -m "$(cat <<'EOF'
docs(methodology): fill — backtest reading guide + structural fixes table

Two sections that protect the reader from misinterpreting historical
numbers: the three-question checklist (pre/post #223, K-cap binding,
bankruptcy fired) and the structural-fixes ledger that categorizes each
inflection as bug vs modeling decision.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Fill `METHODOLOGY.md` — Cost model, Operational model, Research agenda

**Files:**
- Modify: `METHODOLOGY.md`

- [ ] **Step 1: Replace the "Cost model v2" section**

Replace the placeholder with:

```markdown
The original backtest cost model was `slippage_bps = base + linear × participation`. PR #341 replaced it with a sqrt-participation formulation grounded in market-microstructure literature:

```
slippage_bps = base_bps + size_factor × sqrt(notional / liquidity_per_min)
```

Capped at `EXTREME_PARTICIPATION_CAP_BPS = 500` (5%) per fill.

**Anchor parity preserved**: at 0.1% participation, v2 and v1 produce identical total slippage per tier — calibration invariant tested in `test_backtest_costs_v2.py::TestAnchorParity`.

**Funding-rate accounting** (new in v2): per-tier conservative bps per 8h funding interval (`major=1.0`, `mid=2.0`, `small=5.0` in `costs_calibration.json`). Floor semantics: 7h pays 0, 8h pays 1, 24h pays 3. Conservative mode = always positive cost regardless of direction (worst-case for the strategy).

**Forensic motivation**: the DOGE `-$30K` single-trade case from audit H8 (#323) is mitigated >1000× under v2. v1 produced an unbounded ~$19.8M per-fill cost on the catastrophically thin bar; v2 caps at $1,050. The new vol-targeting strategy class (regime-allocation epic) prevents the catastrophic $21K notional from being placed in the first place.

**Calibration sources** are cited inline in `costs_calibration.json`: Almgren-Chriss (2001), Donier-Bonart (2015), Tóth et al (2011).
```

- [ ] **Step 2: Replace the "Operational model" section**

Replace the placeholder with:

```markdown
This repo generates signals automatically; it does not place trades automatically.

The scanner emits a scored signal (0–9) on the curated 10-symbol basket every 300 seconds. The dashboard shows the signal. Telegram (per-user, since [#421](https://github.com/sssimon/trading-spacial/pull/421)) pushes a notification. **A human decides whether to enter, and at what size**.

Exclusions E2–E5 in `btc_scanner.py:305-335` are *manual-check by design* — the scanner does not gate on them because in backtest there is no operator to ask. In live, the operator decides whether to override.

This is not a defect waiting to be automated. The only confirmed edge from the post-inflection re-baselining (Direction A, PR [#357](https://github.com/sssimon/trading-spacial/pull/357)) was **Q2: operator-discretion exit timing**. Removing the human and full-automating would *destroy* the edge that the project has actually validated.

Full classification of the backtest-vs-live distinction: [`2026-05-01-operational-model-manual-gating.md`](docs/superpowers/specs/es/2026-05-01-operational-model-manual-gating.md).
```

- [ ] **Step 3: Replace the "Where the research is going" section**

Replace the placeholder with:

```markdown
The LRC strategy class (4H macro → 1H signal → 5M entry, ATR-based SL/TP) is mature but produced `EDGE_WEAK` in the post-#223 re-baselining. The active research direction is:

**Regime-allocation strategy class** (epic [#338](https://github.com/sssimon/trading-spacial/issues/338), pre-reg [`2026-05-13-epic-regime-allocation-strategy-pivot.md`](docs/superpowers/specs/es/2026-05-13-epic-regime-allocation-strategy-pivot.md))

Structurally distinct alternative: equal-weight Donchian ensemble (9 lookbacks: 5/10/20/30/60/90/150/250/360 days), daily updates at 23:00 UTC close, vol-targeting sizing (30% annualized portfolio vol target replaces R-multiple), bidirectional rotational SHORT, 2× leverage cap, signal-based exits (no SL/TP/TL).

Status as of 2026-05-22:
- **Phase 1** (architecture + flag-gated implementation): shipped 2026-05-13
- **Phase 2** (pre-Phase 3 sanity checks on synthetic data): pre-reg complete
- **Phase 3** (real-data evaluation): returned `PHASE_3_INSUFFICIENT_DATA` — not enough independent observations in the post-2017 universe to discriminate
- **Phase 4-6**: pending Phase 3 resolution

**Multi-tenant production** (epic [#253](https://github.com/sssimon/trading-spacial/issues/253), closed 2026-05-16)

Per-user data isolation (`tenant_id` foreign keys), IDOR-safe API, per-user Telegram dispatcher, per-user dashboard state. The methodology question now extends to: *whose operator-discretion edge are we measuring?* Each invitee (papá Simón id=2, María id=3) becomes their own operator-discretion data point.

**Per-user copilot history** (epic [#428](https://github.com/sssimon/trading-spacial/issues/428), open)

Persist + retrieve past LLM-copilot chats per tenant. Research lens: capture the operator-LLM dialogue at the moment of a discretionary decision, so we can later evaluate which operator decisions correlated with positive outcomes.
```

- [ ] **Step 4: Verify acceptance criteria**

Open `METHODOLOGY.md`. Confirm:
- Cost model section cites Almgren-Chriss / Donier-Bonart / Tóth
- Operational model section explicitly says "scanner generates signals, human decides entries" and links to operational-model spec
- Research agenda includes current state of the regime-allocation epic (Phase 3 = `PHASE_3_INSUFFICIENT_DATA`) and the multi-tenant production status (#253 closed)
- Issue / PR numbers spot-check via `gh issue view` or `gh pr view`

- [ ] **Step 5: Commit**

```bash
git add METHODOLOGY.md
git commit -m "$(cat <<'EOF'
docs(methodology): fill — cost model v2, operational model, research agenda

Cost model v2 (sqrt-participation + funding accrual, Almgren-Chriss
anchored). Operational model — signals are not trades, manual gating
is the edge per Direction A Q2. Forward-looking research: regime-
allocation Phase 3 status + multi-tenant invitee dimension.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Fill `METHODOLOGY.md` — Evaluation guide + References

**Files:**
- Modify: `METHODOLOGY.md`

- [ ] **Step 1: Replace the "How to evaluate any claim in this repo" section**

Replace the placeholder with:

```markdown
Default skepticism stance for any number, chart, or claim you find in this repo:

1. **Find the pre-reg.** If a result has no pre-registration document under `docs/superpowers/specs/es/`, treat it as exploratory analysis, not as evidence. Exploratory analysis informs hypothesis generation; it does not test hypotheses.

2. **Check the date against #223/#224/#309/#313.** Pre-2026-04-25 numbers are pre-PnL-sign-fix. Pre-2026-05-11 numbers are pre-K-cap and pre-bankruptcy-halt. Each of these inflected results materially. The structural-fixes table above is the canonical reference.

3. **Read the `pending_steps` or open issues.** Active gates that bar further claims are tracked as open GitHub issues labeled `methodology/*`. If `#322` (A.4-3 holdout execution) is open, no claim about holdout performance is valid yet.

4. **Look for the `clamped_trade_count` and `bankruptcy_count`.** Any backtest report should surface both. If a result is good but `bankruptcy_count > 0` or `clamped_trade_count > 5%` of trades on any symbol, the result is reporting cap-bounded behavior, not strategy edge.

5. **Sister variables.** When a finding is reported, ask: what parallel paths or related parameters might exhibit the same pattern? An honest analysis acknowledges them in the same writeup.

6. **Operator discretion is real.** Numbers from the live system include operator-discretion exit timing. Numbers from backtest do not. The two are not comparable without explicit translation.
```

- [ ] **Step 2: Replace the "References" section**

Replace the placeholder with:

```markdown
**Canonical specs** (all under `docs/superpowers/specs/es/`):
- [`2026-04-30-a1-holdout-dataset-provenance.md`](docs/superpowers/specs/es/2026-04-30-a1-holdout-dataset-provenance.md) — holdout provenance + lock parameters
- [`2026-05-11-a4-hallazgo-inflexion-metodologica.md`](docs/superpowers/specs/es/2026-05-11-a4-hallazgo-inflexion-metodologica.md) — inflection point that triggered the regime-allocation pivot
- [`2026-05-13-epic-regime-allocation-strategy-pivot.md`](docs/superpowers/specs/es/2026-05-13-epic-regime-allocation-strategy-pivot.md) — current active research direction
- [`2026-05-01-operational-model-manual-gating.md`](docs/superpowers/specs/es/2026-05-01-operational-model-manual-gating.md) — why exclusions E2–E5 are manual-check by design
- [`2026-05-11-strategy-structural-audit.md`](docs/superpowers/specs/es/2026-05-11-strategy-structural-audit.md) — comprehensive audit of strategy components
- [`2026-05-16-multi-tenant-threat-model.md`](docs/superpowers/specs/es/2026-05-16-multi-tenant-threat-model.md) — per-tenant isolation guarantees
- [`2026-05-03-asunciones-tecnicas-pre-holdout.md`](docs/superpowers/specs/es/2026-05-03-asunciones-tecnicas-pre-holdout.md) — assumptions audit pre-holdout-evaluation

**Research notes** (`docs/superpowers/research/`):
- [`2026-05-02-structural-fix-parameter-study.md`](docs/superpowers/research/2026-05-02-structural-fix-parameter-study.md) — K-cap parameter study
- [`2026-04-30-exit-logic-benchmark-crypto.md`](docs/superpowers/research/2026-04-30-exit-logic-benchmark-crypto.md) — exit logic benchmark

**Academic anchors cited in `costs_calibration.json`:**
- Almgren-Chriss (2001), "Optimal execution of portfolio transactions"
- Donier-Bonart (2015), "A Million Metaorder Analysis of Market Impact on the Bitcoin"
- Tóth et al (2011), "Anomalous price impact and the critical nature of liquidity in financial markets"

**External method references:**
- Pre-registration practice from clinical trials methodology (e.g. ClinicalTrials.gov)
- Holdout-set discipline from machine-learning competition norms (Kaggle private leaderboards)

**Navigation:** the full index of specs, plans, and research is at [`docs/README.md`](docs/README.md).
```

- [ ] **Step 3: Verify acceptance criteria**

Open `METHODOLOGY.md`. Confirm:
- All 10 sections now have content (no `(TBD — Task N)` placeholders remain)
- "How to evaluate" gives 6 concrete checks, not vague exhortations
- References section links to specs that actually exist (check 3 random links exist via `ls`)
- Forward reference to `docs/README.md` is present (will be created in Task 6)

- [ ] **Step 4: Commit**

```bash
git add METHODOLOGY.md
git commit -m "$(cat <<'EOF'
docs(methodology): fill — evaluation checklist + references

How to evaluate any claim in this repo (6 concrete checks: pre-reg
exists, date vs structural fixes, open issues, clamped/bankruptcy
counts, sister variables, operator discretion). References to
canonical specs, research notes, and academic anchors for the cost
model.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Create `docs/README.md` navigation index

**Files:**
- Create: `docs/README.md`

- [ ] **Step 1: Write the file**

Write to `docs/README.md`:

```markdown
# Documentation Index

> Navigation surface over the research artifacts in this repo. Start here.

## Start here

If you're new to the project and want to understand what it's actually about:
1. Read [`/METHODOLOGY.md`](../METHODOLOGY.md) — the moat, in 10 sections
2. Read [`/CLAUDE.md`](../CLAUDE.md) — current-state truth (architecture, configs, known limitations)
3. Skim the canonical specs below by topic

## Canonical specs by topic

### Project framing + methodology
- [`specs/es/2026-04-30-a1-holdout-dataset-provenance.md`](superpowers/specs/es/2026-04-30-a1-holdout-dataset-provenance.md) — holdout dataset provenance
- [`specs/es/2026-05-01-operational-model-manual-gating.md`](superpowers/specs/es/2026-05-01-operational-model-manual-gating.md) — why this isn't auto-trading
- [`specs/es/2026-05-03-asunciones-tecnicas-pre-holdout.md`](superpowers/specs/es/2026-05-03-asunciones-tecnicas-pre-holdout.md) — assumptions audit
- [`specs/es/2026-05-11-strategy-structural-audit.md`](superpowers/specs/es/2026-05-11-strategy-structural-audit.md) — strategy components audit

### Inflection points (where prior numbers were re-baselined)
- [`specs/es/2026-05-11-a4-hallazgo-inflexion-metodologica.md`](superpowers/specs/es/2026-05-11-a4-hallazgo-inflexion-metodologica.md) — the inflection itself
- [`research/2026-05-02-structural-fix-parameter-study.md`](superpowers/research/2026-05-02-structural-fix-parameter-study.md) — K-cap parameter study
- [`specs/es/2026-04-18-documento-completo-sistema-trading.md`](superpowers/specs/es/2026-04-18-documento-completo-sistema-trading.md) — full system doc (⚠️ numbers are pre-#223, see METHODOLOGY § Structural fixes)

### Strategy research
- [`specs/es/2026-04-15-analisis-estrategia-spot-v6.md`](superpowers/specs/es/2026-04-15-analisis-estrategia-spot-v6.md) — initial V6 analysis
- [`specs/es/2026-04-16-detector-regimen-multi-signal.md`](superpowers/specs/es/2026-04-16-detector-regimen-multi-signal.md) — regime detector design
- [`specs/es/2026-04-17-formula-ganadora-resultados-finales.md`](superpowers/specs/es/2026-04-17-formula-ganadora-resultados-finales.md) — winning formula (⚠️ pre-#223)
- [`specs/es/2026-04-18-vol-normalized-resultados.md`](superpowers/specs/es/2026-04-18-vol-normalized-resultados.md) — vol normalization
- [`specs/es/2026-05-13-epic-regime-allocation-strategy-pivot.md`](superpowers/specs/es/2026-05-13-epic-regime-allocation-strategy-pivot.md) — **current research direction**

### Kill-switch + risk
- [`specs/es/2026-04-21-kill-switch-design.md`](superpowers/specs/es/2026-04-21-kill-switch-design.md) — kill-switch v1
- [`specs/es/2026-04-23-kill-switch-v2-design.md`](superpowers/specs/es/2026-04-23-kill-switch-v2-design.md) — kill-switch v2

### Production + multi-tenant
- [`specs/es/2026-04-29-trading-sdar-dev-deploy-design.md`](superpowers/specs/es/2026-04-29-trading-sdar-dev-deploy-design.md) — deploy design
- [`specs/es/2026-05-16-multi-tenant-threat-model.md`](superpowers/specs/es/2026-05-16-multi-tenant-threat-model.md) — multi-tenant threat model
- [`specs/es/2026-05-21-telegram-per-user-config-pre-reg.md`](superpowers/specs/es/2026-05-21-telegram-per-user-config-pre-reg.md) — per-user Telegram

### LLM copilot
- [`specs/es/2026-05-19-trading-copilot-production-grade-pre-reg.md`](superpowers/specs/es/2026-05-19-trading-copilot-production-grade-pre-reg.md) — copilot prod-grade
- [`specs/es/2026-05-20-multi-provider-copilot-pre-reg.md`](superpowers/specs/es/2026-05-20-multi-provider-copilot-pre-reg.md) — multi-provider (DeepSeek)

## Active implementation plans

Plans for current work are in `superpowers/plans/`. Recent (2026-05-*):
- `2026-05-21-telegram-per-user-config.md` (shipped #421)
- `2026-05-21-telegram-multitenant-phase-a.md` (shipped epic #253)
- `2026-05-22-readme-methodology-public-translation.md` (this plan)

Archived plans: `superpowers/plans/archive/`

## Other documentation

- [`CHANGELOG-2026-04-17-18.md`](CHANGELOG-2026-04-17-18.md) — historical changelog snippet (April inflection week)
- [`atomic-deploy-migration.md`](atomic-deploy-migration.md) — deploy migration notes
- [`strategy-backtest-report.md`](strategy-backtest-report.md) — historical backtest report (⚠️ check dates against #223)
- `rollouts/` — operational rollout playbooks

## Convention notes

**Why are most specs in Spanish (`specs/es/`)?** The operator works primarily in Spanish; pre-regs are written in the language of thinking. Public-facing docs (`README.md`, `METHODOLOGY.md`, this file) are in English so the moat is legible to a broader audience. The double-surface is intentional: research depth in the native language, public framing in English.

**Pre-#223 caveat marker**: docs that contain backtest numbers from before the PR #223 sign-error fix are marked ⚠️ above. Read them for methodology and reasoning; do not cite their numbers.
```

- [ ] **Step 2: Verify acceptance criteria**

Open `docs/README.md`. Confirm:
- Index links to all major spec categories (framing, inflection points, strategy, kill-switch, production, copilot)
- Pre-#223 specs are marked with ⚠️
- Links use relative paths (`superpowers/specs/es/...`) that resolve from `docs/`
- "Start here" section gives 3 concrete reading steps

- [ ] **Step 3: Spot-check links**

```bash
cd docs
ls superpowers/specs/es/2026-04-30-a1-holdout-dataset-provenance.md \
   superpowers/specs/es/2026-05-13-epic-regime-allocation-strategy-pivot.md \
   superpowers/research/2026-05-02-structural-fix-parameter-study.md \
   ../METHODOLOGY.md ../CLAUDE.md
```

Expected: all 5 paths resolve. If any don't, fix the link in `docs/README.md` before committing.

- [ ] **Step 4: Commit**

```bash
git add docs/README.md
git commit -m "$(cat <<'EOF'
docs(index): navigation surface over specs/plans/research

Adds docs/README.md as the entry point for the 37 specs + 35 plans
in docs/superpowers/. Organized by topic (framing, inflection points,
strategy, kill-switch, production, copilot). Pre-#223 numbers marked
with ⚠️. Cross-links to METHODOLOGY.md and CLAUDE.md.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Rewrite `README.md` — header, intro, top-of-file

**Files:**
- Modify: `README.md` (lines 1–10, the header section)

- [ ] **Step 1: Read the current top of README**

```bash
sed -n '1,15p' README.md
```

Confirm current state begins with `# Crypto Trading Scanner — Ultimate Macro & Order Flow V6.0`.

- [ ] **Step 2: Replace the header + intro**

Use the Edit tool to replace lines 1–7 of `README.md`. Replace:

```markdown
# Crypto Trading Scanner — Ultimate Macro & Order Flow V6.0

[![CI](https://github.com/sssimon/trading-spacial/actions/workflows/ci.yml/badge.svg)](https://github.com/sssimon/trading-spacial/actions/workflows/ci.yml)

Automated signal system for the top 20 crypto pairs by market cap. Uses multi-timeframe technical analysis (4H macro context → 1H signal → 5M entry trigger) to generate scored entry alerts delivered to Telegram.

---
```

With:

```markdown
# trading-spacial

[![CI](https://github.com/sssimon/trading-spacial/actions/workflows/ci.yml/badge.svg)](https://github.com/sssimon/trading-spacial/actions/workflows/ci.yml)

> A research-grade laboratory for evaluating systematic crypto-trading strategies, with a working signal scanner + dashboard on top.

The surface is a Bitcoin / altcoin signal scanner: multi-timeframe technical analysis (4H macro → 1H signal → 5M entry), scored signals delivered to Telegram per-user, React dashboard with position tracking and an in-app LLM copilot.

The substance is the methodology underneath: pre-registered hypotheses, a locked holdout dataset with two-layer access guards, explicit structural-fix ledger (bug fixes vs. modeling decisions), and honest closure of failed hypotheses. **See [`METHODOLOGY.md`](METHODOLOGY.md)** for what makes this different from the 50,000 other crypto bots on GitHub.

**Status (2026-05-22):** the LRC strategy class has been re-baselined post-PR #223 and returned `EDGE_WEAK`. The only confirmed edge is operator-discretion exit timing (Direction A Q2). The active research direction is a structurally distinct regime-allocation strategy class (epic [#338](https://github.com/sssimon/trading-spacial/issues/338)); Phase 3 returned `PHASE_3_INSUFFICIENT_DATA` and the path forward is gated. Do not trade this system live.

---
```

- [ ] **Step 3: Verify acceptance criteria**

```bash
sed -n '1,15p' README.md
```

Confirm:
- Title is `trading-spacial`, no `Ultimate Macro V6.0`
- Subtitle line positions the project as research-grade
- Status block explicitly says "do not trade this live"
- Forward link to `METHODOLOGY.md` is present
- CI badge URL unchanged (already correct — repo is not a fork; the evaluator was wrong on that point)

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
docs(readme): rewrite header — drop V6.0 branding, position as research-grade

The header now says what the project actually is (research lab with a
scanner on top) rather than 'Ultimate Macro V6.0'. Status block warns
do-not-trade-live and links to METHODOLOGY.md for the moat.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Rewrite `README.md` — Architecture + Signal Logic sections

**Files:**
- Modify: `README.md` — the "Architecture" + "Signal Logic" sections (originally lines ~11–50)

- [ ] **Step 1: Replace the Architecture diagram + signal logic block**

Find the current `## Architecture` section in `README.md`. It currently shows:

```
Binance API (Bybit fallback)
  └─ btc_scanner.py     — fetch OHLCV, calculate indicators, score signals
       └─ btc_api.py    — FastAPI server, SQLite storage, notification filters
            └─ trading_webhook.py  →  Telegram (via OpenClaw CLI)
               n8n workflow        →  Telegram (alternative)
```

Replace with:

```markdown
## Architecture

```text
Binance API (Bybit fallback)
  └─ btc_scanner.py      — fetch OHLCV, compute indicators (LRC, RSI, BB, SMA, ATR, ADX),
     |                     score signals (0–9), gate by regime detector
     ├─ strategy/         — modular indicators, regime detection, sizing, vol-targeting
     ├─ strategies/       — ⚠️ legacy ADX-based router (kept for back-compat; see CLAUDE.md)
     └─ backtest.py       — simulator with K-cap overshoot bound + bankruptcy halt
            ↓
  └─ btc_api.py            — FastAPI server (port 8000), SQLite storage, scanner thread
     ├─ api/                 — REST endpoints (signals, positions, prefs, agent)
     ├─ auth/                — JWT auth, per-user setup, password reset by shell only
     ├─ db/                  — SQLite schema + migrations + capital tracker
     └─ notifier/            — per-user signal dispatch (multi-tenant since epic #253)
            ↓
  └─ Telegram (per-user)   — each operator configures their own bot + chat_id
                              via dashboard → UserMenu → Conexiones (since #421)

frontend/                    — React 18 dashboard (Vite + TypeScript)
                              symbols grid, signals table, positions, copilot dock
infra/                       — deploy configs (Caddy, GitHub Actions)
```

### Signal Logic

| Timeframe | Role | Indicators |
|-----------|------|------------|
| 4H | Macro context | SMA100, trend direction |
| 1H | Main signal | LRC (100-bar), RSI, Bollinger Bands |
| 5M | Entry trigger | Reversal candle confirmation |

**Entry zone:** `LRC_LONG_MAX = 25%` (long), `LRC_SHORT_MIN = 75%` (short, gated by `regime=BEAR`).

**Score tiers (operator-chosen partition, stable from inception):**
- `0–1` → 50% position size
- `2–3` → standard size
- `≥ 4` → premium signal (+50% size)

**Risk per trade:** fixed 1% of capital. Per-symbol volatility adaptation is handled by tuned `atr_sl_mult / tp / be` values in `config.json["symbol_overrides"]` (epic #121). Do not add multiplicative scalers on top.

**Regime detection** (`detect_regime`, once daily, cached in `data/regime_cache.json`):
Composite score = 40% price (SMA50/200, 30d momentum) + 30% Fear & Greed + 30% Binance Futures funding rate. Score >60 = BULL/LONG, <40 = BEAR/SHORT-enabled, 40–60 = NEUTRAL/LONG-only.

**Structural bounds (post-#223 simulator):**
- **K-cap (#309)**: `|pnl_usd| ≤ 10 × risk_amount` per trade. Bounds the catastrophic-bar mechanism.
- **Bankruptcy halt (#313)**: symbol stops new entries when equity drops below `0.1 × INITIAL_CAPITAL`. Existing positions close naturally.

For the why behind these bounds, see [`METHODOLOGY.md`](METHODOLOGY.md) § Structural fixes shipped.

**Curated symbols (static, 10):** BTC, ETH, ADA, AVAX, DOGE, UNI, XLM, PENDLE, JUP, RUNE. Static since epic #135 confirmed via 768+ backtest combinations that the 13 removed tokens (BNB, SOL, XRP, DOT, MATIC, LINK, LTC, ATOM, NEAR, FIL, APT, OP, ARB) are not profitable with this strategy regardless of parameters.
```

- [ ] **Step 2: Verify acceptance criteria**

```bash
grep -A 30 "## Architecture" README.md | head -40
```

Confirm:
- Diagram mentions `strategy/` (modular) and `strategies/` (legacy, marked ⚠️) — addresses the audit's #2 point without resolving it
- Diagram mentions `notifier/`, `api/`, `auth/`, `db/`, `infra/` — reflects current code structure
- Telegram per-user is mentioned (since #421)
- Score tiers note "operator-chosen partition, stable from inception"
- K-cap + bankruptcy halt are mentioned with PR refs
- Curated symbols list is current (10 coins, post-#135)
- Forward link to METHODOLOGY.md present

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
docs(readme): rewrite architecture + signal logic to match current code

Updated to reflect: modular strategy/ (vs legacy strategies/), per-user
Telegram dispatch (#421), K-cap (#309), bankruptcy halt (#313), regime
detector composition, current curated 10-symbol basket. Old diagram
implied single-tenant flow via OpenClaw CLI; new diagram surfaces the
multi-tenant dispatcher path.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Rewrite `README.md` — Project Structure + Stack table

**Files:**
- Modify: `README.md` — the "Project Structure" tree + "Stack" table

- [ ] **Step 1: Replace the Project Structure tree**

Find the `## Project Structure` section. Currently it shows `btc_api.py`, `btc_scanner.py`, `btc_report.py`, `trading_webhook.py`, `watchdog.py`, `docker-compose.yml`, `frontend/`, `tests/`, `scripts/`, `Backtesting_BTCUSDT/`, `data/`.

Replace the tree block with:

```text
├── README.md                  # You are here
├── METHODOLOGY.md             # The moat: pre-registration, holdout guards, structural fixes
├── CLAUDE.md                  # Current-state truth (architecture, configs, known limitations)
├── docs/                      # Specs, plans, research notes — see docs/README.md
│   ├── README.md              # Documentation index
│   └── superpowers/
│       ├── specs/es/          # ~40 pre-registration documents
│       ├── plans/             # ~35 implementation plans (active + archive/)
│       └── research/          # Research notes (K-cap study, exit benchmarks)
│
│  # — Entry points —
├── btc_api.py                 # FastAPI server (port 8000), scanner thread
├── btc_scanner.py             # Signal engine: indicators, scoring, regime detection
├── btc_report.py              # Standalone HTML market report generator
├── trading_webhook.py         # Webhook receiver → Telegram (legacy path, port 9000)
├── watchdog.py                # Process supervisor — ⚠️ Windows-only; Linux prod
│                              #   supervises via systemd (not yet in repo, tracked
│                              #   in audit #7)
│
│  # — Modular code —
├── api/                       # REST endpoints split by domain
├── auth/                      # JWT auth + setup paths
├── db/                        # SQLite schema + migrations + capital
├── notifier/                  # Per-user signal dispatch (multi-tenant)
├── strategy/                  # Indicators, regime, sizing, kill-switch, vol-targeting
├── strategies/                # ⚠️ Legacy ADX router — being consolidated (audit #2)
├── scanner/                   # HTTP helpers
├── cli/                       # CLI commands
├── tools/                     # Operator scripts
│
│  # — Backtest + tuning —
├── backtest.py                # Simulator (post-#309 K-cap + #313 bankruptcy halt)
├── backtest_costs.py          # Cost model v2 (sqrt-participation + funding)
├── auto_tune.py               # Parameter sweep harness
├── grid_search_tf.py          # Timeframe grid search
├── optimize_new_tokens.py     # New-token evaluation
│
│  # — Frontend + infra —
├── frontend/                  # React 18 + Vite + TypeScript dashboard
│   └── src/                   # Components, hooks, types, copilot dock
├── infra/                     # Deploy configs (Caddy, GitHub Actions)
├── scripts/                   # Windows automation (PS1 + BAT) + Linux setup
│
│  # — Tests + data —
├── tests/                     # pytest (api, scanner, backtest, multi-tenant, holdout)
├── data/                      # Operational data
│   ├── holdout/               # 🔒 Locked holdout dataset (read-only, guard-protected)
│   ├── regime_cache.json
│   ├── symbols_status.json
│   └── signals_history.csv
└── logs/                      # Runtime logs (signals, webhook, watchdog)
```

- [ ] **Step 2: Replace the Stack table**

Find the `## Stack` section. Currently the Infrastructure row says "Docker, Windows Task Scheduler". Replace the entire table with:

```markdown
| Layer | Tech |
|-------|------|
| Backend | Python 3.12, FastAPI, SQLite |
| Frontend | React 18, TypeScript, Vite, lightweight-charts |
| LLM copilot | Anthropic Claude + DeepSeek (multi-provider, epic #400) |
| Alerts | Telegram (per-user, configured by each operator via dashboard) |
| Auth | JWT, per-user setup, password reset by shell only |
| Data sources | Binance Futures (primary), Bybit (fallback), CoinGecko (symbol metadata), Alternative.me (Fear & Greed) |
| Production | Linux EC2 (`trading.sdar.dev`), Caddy reverse proxy, GitHub Actions deploy |
| Local dev | Windows or Linux/macOS, Docker for frontend + n8n |
```

- [ ] **Step 3: Verify acceptance criteria**

```bash
grep -A 50 "## Project Structure" README.md | head -60
```

Confirm:
- Tree shows `docs/`, `METHODOLOGY.md`, `CLAUDE.md` at top
- `strategy/` and `strategies/` both shown with the legacy marker on the second
- `data/holdout/` shown with 🔒 lock marker
- `watchdog.py` warning notes Linux-prod ambiguity + audit ticket reference
- Stack table no longer says "Windows Task Scheduler" as production
- Stack table includes LLM copilot row (epic #400) and per-user Telegram

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
docs(readme): rewrite project structure + stack to match current code

Tree now reflects: METHODOLOGY.md + docs/README.md at top, strategy/ vs
strategies/ split called out with legacy marker, data/holdout/ marked
locked, watchdog.py Linux-prod ambiguity flagged. Stack table adds LLM
copilot row, per-user Telegram, Linux EC2 production (Windows Task
Scheduler was misleading — that's local dev only).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Rewrite `README.md` — translate troubleshooting + deployment to English

**Files:**
- Modify: `README.md` — `## Troubleshooting`, `## Deployment Checklist`, `## Notes` sections

- [ ] **Step 1: Replace the Troubleshooting section**

Find `## Troubleshooting` in `README.md`. Replace the entire section (from `## Troubleshooting` through the end of the watchdog subsection) with:

```markdown
## Troubleshooting

### Scanner generates no signals
1. Verify Binance connectivity: `curl -s https://api.binance.com/api/v3/ping`
2. Check the API log: `tail -f logs/btc_api.log`
3. Confirm `config.json` exists and is valid JSON
4. Force a manual scan: `curl -X POST http://localhost:8000/scan`
5. Hit the health endpoint: `curl http://localhost:8000/health`

### Telegram is silent
1. Confirm `telegram_bot_token` and `telegram_chat_id` are set — note: since [#421](https://github.com/sssimon/trading-spacial/pull/421) these are **per-user** in `user_preferences`, not in `config.json`. Configure via the dashboard → avatar → Conexiones.
2. Test delivery: dashboard → Conexiones → "Probar envío" button, or `curl http://localhost:8000/webhook/test`
3. Check `signal_filters.min_score` isn't too restrictive (default: 4)
4. Search the API log for Telegram errors: `grep -i telegram logs/btc_api.log`
5. If using a proxy: confirm format `socks5://127.0.0.1:1080`

### Dashboard shows no data
1. Confirm `btc_api.py` is running: `curl http://localhost:8000/status`
2. If running under Docker: `docker ps`
3. Verify the nginx / Caddy proxy: `curl http://localhost:3000/api/status`
4. Check the browser console for CORS errors

### Database errors
1. Confirm `signals.db` exists and isn't corrupt
2. To restore from backup: `cp backups/signals_YYYYMMDD.db signals.db`
3. To recreate from scratch: delete `signals.db` and restart `btc_api.py`

### Watchdog won't start (Windows local dev only)
1. Check Python is on PATH: `python --version`
2. Run the installer as administrator: `powershell -ExecutionPolicy Bypass -File scripts/INSTALAR_AUTOSTART.ps1`
3. Verify the scheduled task exists: open Task Scheduler, look for `BTCScannerWatchdog`
4. Check the log: `type logs\watchdog.log`

> **Production note:** `watchdog.py` is Windows-only and is *not* what supervises production. Production runs on Linux EC2 (`trading.sdar.dev`) where supervision is via systemd. The systemd unit files are not yet checked into the repo — tracked in the audit punch list. If you're deploying to a Linux server, do not rely on `watchdog.py`.
```

- [ ] **Step 2: Replace the Deployment Checklist**

Find `## Deployment Checklist`. Replace with:

```markdown
## Deployment Checklist

- [ ] Create `config.json` with credentials (copy template from this README)
- [ ] Configure system-level Telegram only if you want a fallback channel — otherwise each user configures their own via the dashboard (since #421)
- [ ] Optional: set `api_key` to protect sensitive endpoints
- [ ] Verify Binance connectivity: `curl https://api.binance.com/api/v3/ping`
- [ ] Install dependencies: `pip install -r requirements.txt`
- [ ] Start the API: `python btc_api.py`
- [ ] Health check: `curl http://localhost:8000/health`
- [ ] Test legacy Telegram (if configured): `curl http://localhost:8000/webhook/test`
- [ ] Start the frontend: `cd frontend && npm install && npm run dev`
- [ ] Open the dashboard: `http://localhost:5173`
- [ ] For production: see `infra/` + GitHub Actions deploy workflow (`.github/workflows/deploy.yml`)
- [ ] Configure autostart:
  - **Windows local dev:** `scripts/INSTALAR_AUTOSTART.ps1`
  - **Linux production:** systemd unit (see ops docs — not yet in repo)
- [ ] Confirm logs are landing in `logs/`
- [ ] Confirm `data/regime_cache.json` populates after the first daily-bar fetch
```

- [ ] **Step 3: Replace the Notes section**

Find the `## Notes` section (or trailing bullet list). Replace with:

```markdown
## Notes

- `config.json` is git-ignored — contains credentials. Use the template in this README to bootstrap.
- `watchdog.py` is Windows-only (uses `tasklist`, `taskkill`, `wmic`). Linux production is supervised by systemd; the unit files are not yet in the repo (tracked).
- The curated symbol list is **static** (10 coins) since epic #135 — see Signal Logic section above.
- Binance Futures is the primary data source; Bybit is the fallback.
- The locked holdout dataset at `data/holdout/` is read-only and guard-protected. See [`METHODOLOGY.md`](METHODOLOGY.md) § Holdout dataset isolation before writing any new code that touches it.

## Research methodology

This isn't just a trading scanner. The methodology underneath — pre-registration, holdout isolation, structural-fix ledger, honest closure of failed hypotheses — is what makes this repo different from the generic crypto-bot landscape.

→ **Read [`METHODOLOGY.md`](METHODOLOGY.md)**, then [`docs/README.md`](docs/README.md) for the full spec index.
```

- [ ] **Step 4: Verify acceptance criteria**

```bash
grep -A 5 "## Troubleshooting" README.md | head -10
grep -A 5 "## Deployment Checklist" README.md | head -10
grep -A 5 "## Research methodology" README.md | head -10
```

Confirm:
- Troubleshooting is now in English
- Deployment Checklist is in English
- A "Research methodology" section exists at the bottom, linking to METHODOLOGY.md + docs/README.md
- The watchdog production note explicitly acknowledges Linux supervision is via systemd (out of repo)

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
docs(readme): translate troubleshooting + deployment to English, add methodology section

Unifies the README to English (was half English / half Spanish — see
audit #6). Adds a closing 'Research methodology' section that surfaces
METHODOLOGY.md and docs/README.md prominently, addressing audit #1
(moat invisibility). Watchdog Linux-supervision ambiguity called out
explicitly in Notes (audit #7).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Update GitHub repo metadata

**Files:**
- No file modifications (uses `gh` CLI)

- [ ] **Step 1: Set description, topics, homepage**

Run:

```bash
gh repo edit sssimon/trading-spacial \
  --description "Research-grade laboratory for evaluating systematic crypto-trading strategies. Locked holdout, pre-registered hypotheses, explicit structural-fix ledger. Working signal scanner + dashboard on top." \
  --homepage "https://trading.sdar.dev" \
  --add-topic "algorithmic-trading" \
  --add-topic "quantitative-finance" \
  --add-topic "backtesting" \
  --add-topic "cryptocurrency" \
  --add-topic "research-methodology" \
  --add-topic "pre-registration" \
  --add-topic "holdout-validation" \
  --add-topic "fastapi" \
  --add-topic "react"
```

- [ ] **Step 2: Verify metadata was set**

```bash
gh repo view --json description,homepageUrl,repositoryTopics
```

Confirm:
- `description` matches what was set
- `homepageUrl` is `https://trading.sdar.dev`
- `repositoryTopics` includes all 9 topics

If anything didn't take, re-run the relevant `gh repo edit` flag.

- [ ] **Step 3: No git commit needed** — GitHub metadata is server-side state, not in the repo. (If the user wants this captured: add a one-liner to `CLAUDE.md` noting the topics were set on 2026-05-22, but this is optional.)

---

### Task 12: Final self-review + cross-link verification

**Files:**
- Read-only verification + small fixes if needed

- [ ] **Step 1: Verify all internal links resolve**

Run:

```bash
grep -oE "\]\([^)]+\.md[^)]*\)" README.md METHODOLOGY.md docs/README.md | sort -u | head -40
```

For each unique link, manually verify the target exists by running `ls <path>` from the appropriate base directory.

If any link is broken, fix it inline with `Edit` before moving on. Don't commit broken links.

- [ ] **Step 2: Outsider-perspective read**

Read `README.md` end-to-end as if you've never seen this project. Ask yourself the audit's question: does the reader perceive "another crypto scanner V6.0" or "a research-grade laboratory"? Specifically, by the end of the first paragraph, has the methodology framing landed?

If the framing is muddy, edit the intro (Task 7 output) to be sharper.

- [ ] **Step 3: Verify no remaining `(TBD — Task N)` placeholders**

```bash
grep -n "TBD —" METHODOLOGY.md docs/README.md README.md 2>/dev/null
```

Expected: no matches. If any remain, complete them.

- [ ] **Step 4: Verify no remaining "Ultimate Macro" / "V6.0" branding**

```bash
grep -n "Ultimate Macro\|V6\.0" README.md METHODOLOGY.md docs/README.md
```

Expected: no matches. The historical CHANGELOG in `docs/` may reference V6, that's fine — only the top-level docs should be cleaned.

- [ ] **Step 5: If any fixes were made, commit them**

```bash
git status
# If there are unstaged changes:
git add README.md METHODOLOGY.md docs/README.md
git commit -m "$(cat <<'EOF'
docs(readme,methodology): final pass — cross-link fixes + outsider-read tweaks

Self-review pass after the main writeup. Fixed broken internal links
and sharpened the intro paragraph based on outsider-perspective read.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6: Push the branch + open PR**

```bash
git push origin <branch-name>
gh pr create --title "docs: public methodology translation — METHODOLOGY.md + docs/README.md + README rewrite" --body "$(cat <<'EOF'
## Summary
- Adds **`METHODOLOGY.md`** (new, repo root) — 10-section moat doc covering pre-registration, holdout guards, backtest-reading checklist, structural-fix ledger (bug vs modeling), cost model v2, operational model, current research direction
- Adds **`docs/README.md`** (new) — navigation index over the 37 specs + 35 plans + research notes already in `docs/superpowers/`
- Rewrites **`README.md`** — drops `Ultimate Macro V6.0` branding, unifies to English, updates Architecture / Signal Logic / Project Structure to reflect current code (per-user Telegram dispatcher, K-cap, BANKRUPT halt, regime detector, multi-tenant), adds a "Research methodology" section
- Sets GitHub repo description, homepage, topics via `gh repo edit`

## Motivation
External-evaluator audit identified the project's hidden moat (research discipline) as invisible from the repo surface. README was outdated (showed pre-#223 score-tier model, single-tenant Telegram, Windows-only watchdog as if it ran prod) and mixed English+Spanish. This PR translates the discipline that lives in `CLAUDE.md` + `docs/superpowers/specs/es/` into public-facing artifacts.

## Out of scope (deferred to separate tickets)
- `strategy/` vs `strategies/` consolidation (audit #2)
- Linux production systemd unit files (audit #7)
- `mempalace.yaml` relocation (audit #9 — verified benign)
- Retroactive tagging of gate-closure commits (audit #8)

## Test plan
- [x] All internal `.md` links resolve (Task 12 verification)
- [x] No remaining `(TBD)` placeholders or `Ultimate Macro` / `V6.0` strings
- [x] METHODOLOGY.md sections cross-reference real specs (spot-checked)
- [ ] After merge: outsider-perspective read by a fresh reviewer (papá / María)
- [ ] GitHub repo sidebar shows description + topics + homepage

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 7: Final status check**

```bash
git status
gh pr view --web 2>/dev/null || gh pr view
```

Confirm the branch is pushed, the PR is open, and CI is running.

---

## Self-Review Pass (run after writing the full plan above)

**Spec coverage check:**
- Audit #1 (methodology invisible) → covered by Tasks 1–6 + README "Research methodology" section in Task 10 ✓
- Audit #4 (no description/topics/website) → covered by Task 11 ✓
- Audit #5 (branding inconsistent) → covered by Task 7 (drop V6.0) ✓
- Audit #6 (README mixed languages) → covered by Task 10 (translate to English) ✓
- Audit #7 (watchdog Linux ambiguity) → addressed via README disclosures in Tasks 9 + 10; full ticket for systemd unit files explicitly out of scope ✓
- Audit #2 (strategy/ vs strategies/) → flagged in README (Tasks 8 + 9) with legacy marker; full consolidation explicitly out of scope ✓
- Audit #3 (CI badge) → addressed by *not* changing it (verified during evaluator-critique that the repo is not a fork; the badge is correct) ✓
- Audit #8 (no tags) → out of scope; noted in plan header ✓
- Audit #9 (mempalace.yaml) → out of scope; noted in plan header ✓

**Placeholder scan:** No `TBD` / `TODO` / "fill in later" placeholders in the plan tasks themselves. The `(TBD — Task N)` strings in Task 1 are intentional and resolved by Tasks 2–5.

**Type consistency:** Documentation work — no function/method signatures to verify. File paths verified during Task 1 inventory.

**Pre-existing-content respect:** The plan does not delete the "Running the System" section of `README.md` or the practical command blocks (they work). It only rewrites the parts that misrepresent current state.

**Memory respect:**
- `feedback_pre_registration_correction` — the plan corrects the README explicitly (not soft-clarifying), and calls out exactly which sections of CLAUDE.md are canonical sources of truth ✓
- `feedback_bugs_vs_modeling_framing` — the structural-fixes table in Task 3 explicitly categorizes each PR as bug-fix vs modeling-decision ✓
- `feedback_review_pipeline_discipline` — out-of-scope items have contemporaneous ticket references in the plan header ✓
- `feedback_fix_framing_prose_over_code` — README rewrites use prose intent ("research-grade laboratory") rather than code-derived language ✓

---

## Estimated effort

~2-3 hours total wall-clock, broken roughly as:
- Tasks 1–5 (METHODOLOGY.md): 60-90 min
- Task 6 (docs/README.md): 15 min
- Tasks 7–10 (README rewrite): 45-60 min
- Tasks 11–12 (metadata + review): 15 min
