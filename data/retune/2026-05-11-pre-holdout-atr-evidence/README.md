# A.4-1 ATR Re-tune Sweep — 2026-05-11 (NO_DATA Across All Symbols)

**Status:** Sweep completed without errors (rc=0). Every symbol returned `recommendation: NO_DATA`. No promotion. Archived as methodology evidence.

## Result summary

All 10 curated symbols (BTC, ETH, ADA, AVAX, DOGE, UNI, XLM, PENDLE, JUP, RUNE) returned `NO_DATA` from `auto_tune.optimize_symbol`. Per `auto_tune.py`, `NO_DATA` fires when `top_candidates[0]["pnl"] <= 0` — i.e., the **best** of the 105 grid combinations produces non-positive train P&L for that symbol.

In plain English: **no combination of `(atr_sl_mult, atr_tp_mult, atr_be_mult)` in the 7 × 5 × 3 grid produced positive train P&L on the 12-month pre-holdout window for any of the 10 symbols.**

## Why this run differs from the 2026-05-01 sweep (Gemini)

The 5-01 sweep (artefacts now in PR history of #312) reported CHANGE / KEEP recommendations with positive Val PnL Δ for several symbols (BTC +$1,296, ETH +$562, UNI +$1,893, etc.). The 5-11 sweep produces NO_DATA universally. The difference is **simulator path**, not data or grid:

| Aspect | 2026-05-01 (Gemini) | 2026-05-11 (this run) |
|---|---|---|
| Simulator entry path | legacy `atr_*` kwargs | `cfg` + `symbol_overrides` |
| Time-limit barrier | **bypassed** | **active** |
| Participation cap | **bypassed** | **active** |
| Bankruptcy halt (#280) | not in main yet | **active** |
| K=10 overshoot cap (#309) | active (merged 2026-05-04) | active |

The 5-01 path bypassed three live-relevant gates — CLAUDE.md flags this as the legacy contract for callers that do not opt in to `symbol_overrides`. PR #287 (the harness) merged with the legacy path; the post-merge review (Claude, 2026-05-11) flagged this as a comparability bug vs the regime harness (`tools/regime_retune_pre_holdout.py`, #306) which uses the standard path. The fix shipped in the post-#287 follow-up commits forces the standard path whenever both `cutoff` and `app_config` are set, so this 5-11 run is the first ATR sweep that measures the strategy under live-equivalent conditions.

## What this finding rules out

- **Bankruptcy Bias as the sole explanation for prior inflated numbers.** #280 capped that mechanism; the regime sweep 2026-05-11 (separate evidence, PR #315) confirmed trade count dropped 92% post-#280. With BB removed, the strategy still does not produce positive train P&L under any grid combination.
- **"Regime threshold tuning will rescue this".** The regime sweep on the same day shows all symbols bankrupt under all four regime configurations on the same window with current (leaked) ATR. Combining the two sweeps: neither dimension alone (ATR within grid, regime within {60_40, 70_30, 80_20, no_detector}) produces a profitable configuration on the pre-holdout window.

## What this finding does NOT rule out

- **Out-of-grid ATR optima.** The grid covers `sl ∈ {0.5, 0.7, 1.0, 1.2, 1.5, 2.0, 2.5}`, `tp ∈ {2, 3, 4, 5, 6}`, `be ∈ {1.5, 2.0, 2.5}`. The true profitable optimum might lie outside (e.g., very wide SL ≥ 3 combined with very tight TP < 2). Expanding the grid is one option in the inflection-point spec.
- **Configuration-level changes.** Per-symbol `time_limit_hours`, `max_participation_rate`, and `cooldown_hours` were held fixed at their `config.defaults.json` values. If those values are themselves too restrictive (e.g., participation cap rejecting most entries), the train P&L would naturally collapse. Whether those values were derived under the same realism standard the ATR is being tuned against is a methodology question for the inflection-point spec.
- **Train window representativeness.** Train is `[cutoff − 15mo, cutoff − 3mo]` = `[2024-01-30, 2025-01-30]`. If this window is dominated by a single regime (e.g., a sideways or trending period unfavorable to the strategy), broader train windows might yield different results. This too is a methodology question.

## Files

- `report.md` — full side-by-side current vs re-tuned (all rows show "NO_DATA", current values preserved as new values by design)
- `params.json` — drop-in symbol_overrides; equals current config (no change)
- `manifest.json` — cutoff, seed, ohlcv_sha256, code_commit, leakage_check PASS, per-symbol data ranges, scope_notes including `gates_active`

## References

- Methodology inflection-point spec: `docs/superpowers/specs/es/2026-05-11-a4-hallazgo-inflexion-metodologica.md`
- Regime sweep evidence (same day): PR #315
- Harness: PR #287 + post-merge fix
- CLAUDE.md caveat #1 (ATR leakage), caveat #4 (per-symbol vs portfolio aggregation gap)
