# A.4-1.5 Regime Sweep — 2026-05-11 (Halted Summary)

**Status:** Sanity-halted (exit code 3). No canonical artefacts written. Sweep summary preserved here as methodology evidence.

## Why this run was halted

`no_detector` won the raw `sum(net_pnl)` aggregate by 2.18% over the runner-up (`70_30`). Per spec D9 §2.10, the sanity check fires when `no_detector` wins — the harness refuses to write canonical artefacts and dumps the full run state to `halted_summary.json`.

## Why this is **not** the same failure as 2026-05-06

The 5-06 sweep also sanity-halted with `no_detector` winning, but that was a Bankruptcy Bias artefact (per-symbol bankruptcy continued generating zero-`risk_amount` trades, inflating the no_detector trade count and distorting the aggregate). #313 (#280) shipped a per-symbol bankruptcy halt that fixes that mechanism.

Evidence #313 fixed the mechanism:

| Sweep | Cutoff | 60_40 trades | no_detector trades | Total trades |
|---|---|---|---|---|
| 2026-05-06 | 2025-04-30 | 4539 | 8277 | 21,193 |
| 2026-05-11 | 2025-04-30 | 418 | 567 | **1,840** |

Trade count dropped 92%. The ~19,000 trades that disappeared are exactly the post-bankruptcy fictional zero-PnL trades that #280 now halts at the per-symbol bankruptcy floor.

## Root cause of the 5-11 sanity halt

CLAUDE.md "Caveats heredados — A.4 (#250) MUST honor" caveat #1: the current `atr_sl_mult/tp/be` values in `config.json["symbol_overrides"]` were tuned over the full history **including the holdout range**. A.4-1 must re-tune over `[earliest, holdout_start - 1 bar]` before regime evaluation is interpretable.

Per-symbol breakdown from `halted_summary.json` confirms this: every symbol bottoms out at ~$-9K (= $10K initial − $1K bankruptcy floor) under **all four regime configurations**. PENDLE saturates at $-15,171 and JUP at $-11,953 due to K=10-capped overshoots immediately before the bankruptcy halt fires.

| Symbol | 60_40 | 70_30 | 80_20 | no_detector |
|---|---|---|---|---|
| BTC | -9,016 | -9,000 | -9,009 | -9,004 |
| ETH | -9,006 | -9,008 | -9,016 | -9,009 |
| ADA | -9,004 | -9,004 | -9,004 | -9,011 |
| AVAX | -9,002 | -9,002 | -9,002 | -9,061 |
| DOGE | -9,020 | -9,020 | -9,020 | -9,087 |
| UNI | -9,070 | -9,070 | -9,070 | -9,033 |
| XLM | -9,006 | -9,006 | -9,006 | -9,019 |
| PENDLE | -15,171 | -15,171 | -15,171 | -15,171 |
| JUP | -11,953 | -11,953 | -11,953 | -9,712 |
| RUNE | -9,301 | -9,301 | -9,301 | -9,301 |

With every symbol bankrupting under every config, the per-config `sum(net_pnl)` is approximately `10 × $-9K = $-90K` ± per-symbol noise. The 2.18% margin between winner and runner-up is noise within the bankruptcy floor, not regime signal.

## Methodological interpretation

The regime threshold **is not evaluable** on the pre-holdout window with the current production ATR multipliers. The sanity halt is the correct outcome: any winner here would reflect bankruptcy-floor ranking, not regime edge.

The correct ordering per spec D9 §2.10 is:

1. A.4-1 ATR re-tune (re-derive per-symbol ATR multipliers on the pre-holdout window without leakage) — **kicked off after this evidence was captured**.
2. A.4-1.5 regime re-tune **with the new ATR multipliers active** — TBD after step 1 lands.
3. A.4-2 walk-forward → A.4-3 holdout.

## Files

- `halted_summary.json` — full halt state including aggregate, per-config, per-symbol breakdown, and report markdown. Schema matches `tools/regime_retune_pre_holdout.py` halt-branch contract (rc=3).

## References

- Spec D9 §2.10 — decision flag pre-registration
- CLAUDE.md caveat #1 (ATR leakage) and caveat #4 (per-symbol vs portfolio aggregation gap)
- PR #313 (#280) — per-symbol bankruptcy handler that surfaced this cleanly
- PR #312 (closed superseded) — the 2026-05-06 sweep that motivated #280
