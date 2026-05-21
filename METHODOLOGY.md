# Methodology

> Why this project looks like a trading scanner but behaves like a research artifact.

## What this project actually is

On the surface this is a Bitcoin / altcoin signal scanner with a React dashboard. Underneath, it's something rarer in retail trading: **a working laboratory for evaluating systematic strategies with research-grade discipline**.

Concretely, this repo enforces:

- A **locked holdout dataset** (12 months of OHLCV + Fear & Greed + funding rate, SHA-256 + commit hashed in `data/holdout/MANIFEST.json`, filesystem `chmod -R 444/555`) that strategy code and parameter tuning paths cannot read. Two independent guards prevent leakage: a runtime guard (`data/holdout_access.py` is the only legitimate read entry point) and a structural CI guard (`tests/test_holdout_isolation.py` AST-scans every `.py` for any reference to the holdout path).
- **Pre-registration** of every methodology change: hypothesis, decision rule, gates, and abort conditions written down *before* running the experiment. See `docs/superpowers/specs/es/` for ~40 such pre-regs going back to April 2026.
- **Bugs vs. modeling** framing on every result: if a backtest improves, was it because the strategy got better or because a simulator bug got fixed? PRs #223, #224, #309, #313 are all documented inflection points where prior results were known to be inflated by simulator behavior, not strategy edge. See *Structural fixes shipped* below.

If you are evaluating this project as a strategy to trade live: **don't, yet.** Phase 3 of the regime-allocation pivot returned `PHASE_3_INSUFFICIENT_DATA` in May 2026; Direction A of the post-inflection re-baselining returned `EDGE_WEAK`. The only confirmed edge to date is **operator-discretion exit timing** (Q2). The dashboard exists to surface signals for a human to evaluate, not to execute them automatically.

If you are evaluating this project as a methodology artifact: read on.

## Pre-registration discipline

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

## Holdout dataset isolation

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

## How to read the backtest numbers

Any number in this repo — backtest P&L, Sharpe ratio, win rate, drawdown — needs to be read against three questions:

1. **Pre or post #223/#224?** PR #223 fixed a sign error in `_close_position` that inflated historical results. Numbers from specs dated before 2026-04-25 (notably [`2026-04-17-formula-ganadora-resultados-finales.md`](docs/superpowers/specs/es/2026-04-17-formula-ganadora-resultados-finales.md) and [`2026-04-18-documento-completo-sistema-trading.md`](docs/superpowers/specs/es/2026-04-18-documento-completo-sistema-trading.md)) are **pre-fix and known-inflated**. Do not cite them as baseline.

2. **K-cap binding?** PR #309 added a symmetric `K=10` per-trade overshoot cap. Without it, single trades on thin bars could amplify into `170× initial_capital` losses (the PENDLE case during A.4-1.5 sweep). With it, any trade where `clamped_trade_count > 0` reflects cap-bounded behavior on those bars, not strategy edge. The metrics dict surfaces `clamped_trade_count` per symbol — if it accounts for `>5%` of trades, the headline number is measuring the cap, not the strategy.

3. **Bankruptcy halt fired?** PR #313 added per-symbol bankruptcy halt: when simulated equity drops below `0.1 × INITIAL_CAPITAL`, the symbol emits a `BANKRUPT` exit and stops opening new positions for the simulation. Pre-#313, the simulator continued issuing fictional zero-risk trades after bankruptcy, which silently inflated `sum(net_pnl)` for any config that drove a symbol broke. The A.4-1.5 sweep had to operator-override the regime config because `no_detector` "won" only via post-bankruptcy ghost trades on JUPUSDT. Always check `bankruptcy_count` in the metrics dict.

**Recommended framing in narrative**: previous backtests reflected simulator bugs (#223, #313) and modeling decisions (#309), not pure strategy behavior. The bug fixes recovered real numbers; the modeling cap (K=10) is a calibration with its own uncertainty band — don't conflate the two.

## Structural fixes shipped (and what they say about prior numbers)

| PR | Date | Type | What it fixed | Effect on prior numbers |
|---|---|---|---|---|
| [#223](https://github.com/sssimon/trading-spacial/pull/223) / #224 | 2026-04-25 | **Bug fix** | Sign error in `_close_position` that double-counted PnL on certain exit paths | Pre-fix numbers were inflated; not a "calibration improvement" |
| [#296](https://github.com/sssimon/trading-spacial/pull/296)+#297+#298+#299 | 2026-05-03 | **Triple Barrier structural fix** | Time-limit barrier, participation cap, per-symbol overrides honored in live + backtest paths | Closed the legacy `atr_*` kwargs bypass for the live path |
| [#309](https://github.com/sssimon/trading-spacial/pull/309) | 2026-05-11 | **Modeling decision** | Symmetric K=10 per-trade overshoot cap. Bounds `&#124;pnl_usd&#124; ≤ K × risk_amount` | Realistic; bounds the catastrophic-bar mechanism without enforcing pooled-portfolio capital management |
| [#313](https://github.com/sssimon/trading-spacial/pull/313) | 2026-05-11 | **Bug fix** | Post-bankruptcy ghost trades. Symbol halts at `0.1 × INITIAL_CAPITAL` floor | Closed the silent-continued-fictional-trading sub-gap; metrics dict now carries `bankruptcy_count` |
| [#329](https://github.com/sssimon/trading-spacial/pull/329) | 2026-05-12 | **Phase 2 R1 outcome** | SIGNAL_EXIT branch kept flag-gated False on live — mechanism engaged in backtest, profitability absent | Honest closure of a hypothesis that failed its pre-registered gate |

**The framing matters**: #223, #224, #313 are bugs. The simulator was wrong; fixing it recovers real numbers. #309 is a modeling decision with its own uncertainty band (`K=10` chosen as conservative threshold, not empirically tuned). Don't conflate "we fixed bugs" with "we made the simulator more realistic" — the former is methodologically stronger.

Full reasoning: PR #316 inflection-point spec §A.2 + [`2026-05-02-structural-fix-parameter-study.md`](docs/superpowers/research/2026-05-02-structural-fix-parameter-study.md).

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
