# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

A BTC/USDT automated trading signal system with multi-timeframe technical analysis (4H macro → 1H signal → 5M entry trigger). It monitors top 20 crypto symbols, generates scored signals, tracks positions, and pushes alerts to Telegram. Stack: Python backend (FastAPI), React/TypeScript frontend (Vite), SQLite, Docker.

## Running the System

### Backend (Python)
```bash
pip install pandas numpy requests fastapi uvicorn

python btc_api.py          # REST API at http://localhost:8000
python btc_scanner.py      # Standalone scanner (runs once, used by API)
python trading_webhook.py  # Telegram webhook receiver at http://localhost:9000
python watchdog.py         # Process supervisor (keeps API + webhook alive)
python btc_report.py       # Generate standalone HTML market report
```

### Frontend (React/TypeScript)
```bash
cd frontend
npm install
npm run dev      # Dev server at http://localhost:5173
npm run build    # Production build (tsc + vite)
npm run preview  # Preview production build
```

### Docker (Production)
```bash
docker compose up --build  # Frontend at :3000, n8n at :5678
# Note: btc_api.py and watchdog.py run separately in Python, not via Docker
```

### Tests
```bash
python -m pytest tests/ -v
python -m pytest tests/test_scanner.py -v   # Scanner logic only
python -m pytest tests/test_api.py -v       # API endpoints only
```

### Windows Automation
- `scripts/INSTALAR_AUTOSTART.ps1` — registers watchdog.py as a Task Scheduler task ("BTCScannerWatchdog") that starts on boot
- `scripts/REINICIAR_SERVICIOS.ps1` — restart all services
- Batch scripts `INICIAR_API.bat` / `INICIAR_SCANNER.bat` for manual start

## Architecture

### Signal Flow
```
Binance API (Bybit fallback)
  → btc_scanner.py: fetch OHLCV, calculate LRC/RSI/BB/SMA100
  → Multi-timeframe scoring (0–9)
  → btc_api.py: store to signals.db (SQLite), evaluate notification filters
  → trading_webhook.py (port 9000) → OpenClaw CLI → Telegram
     OR n8n workflow (port 5678) → Telegram node
```

### Components
| File | Purpose | Port |
|------|---------|------|
| `btc_api.py` | FastAPI REST server, DB management, scanner thread | 8000 |
| `btc_scanner.py` | Signal generation engine (indicators + scoring) | — |
| `trading_webhook.py` | Webhook receiver → Telegram via OpenClaw CLI | 9000 |
| `watchdog.py` | Process supervisor for API + webhook (Windows only) | — |
| `btc_report.py` | Standalone HTML report generator (Binance Futures, ETF flows) | — |
| `frontend/` | React 18 dashboard (symbols grid, signals table, positions) | 3000/5173 |
| `signals.db` | SQLite: `signals` + `positions` tables | — |

### Key Backend Logic (`btc_scanner.py`)
- **Indicators:** LRC (100-bar Linear Regression Channel), RSI, Bollinger Bands, SMA100, ATR, ADX
- **Entry zone:** LRC_LONG_MAX = 25% (long), LRC_SHORT_MIN = 75% (short, gated by regime=BEAR)
- **Score tiers:** 0–1 = 50% size, 2–3 = normal, ≥4 = premium signal
- **Risk per trade:** fixed 1% of capital. **Do not add multiplicative risk scalers on top** — per-symbol volatility adaptation is handled by the tuned `atr_sl_mult/tp/be` values in `config.json["symbol_overrides"]` (epic #121).
- **Curated symbols (static, 10 coins):** `DEFAULT_SYMBOLS` in `btc_scanner.py` — BTC, ETH, ADA, AVAX, DOGE, UNI, XLM, PENDLE, JUP, RUNE. This list is static since epic #135 confirmed via 768+ backtest combinations that the 13 removed tokens (BNB, SOL, XRP, DOT, MATIC, LINK, LTC, ATOM, NEAR, FIL, APT, OP, ARB) are not profitable with this strategy regardless of parameters.
- **Time-limit barrier:** per-symbol `time_limit_hours` in `symbol_overrides`. Closes positions at `bar["close"]` when `now - entry_time >= time_limit_hours`. Tie-break: SL/TP win over time-limit in the same bar. Legacy `atr_*` kwargs path (auto_tune / grid_search direct callers) skips time-limit by design — those callers must opt in by passing `symbol_overrides` explicitly. Live path (`api.positions.check_position_stops`) reads the barrier from `cfg.symbol_overrides` on each tick (stateless), so config edits apply retroactively to open positions; a `log.warning` is emitted when `hours_open` exceeds the horizon by more than two scanner intervals (~10 min default) to surface that case.
- **Participation cap (epic #294 PR2):** per-symbol `max_participation_rate` in `symbol_overrides`. Skips entries when `desired_notional > cap × rolling 24h median bar volume`. Live path uses last fully-closed bar (`iloc[-2]`) for the median to avoid intraday partial-volume oscillation. Surfaces in `sizing_1h.liquidity_cap` dict with `config_rejected` field for operator visibility into validator rejections. Legacy `atr_*` kwargs path bypasses the cap (mirrors time-limit C2-a gating).
- **SHORT is bidirectional and auto-gated** by `detect_regime()` — contributes ~50% of the validated 4-year backtest P&L. See `docs/superpowers/specs/es/2026-04-17-formula-ganadora-resultados-finales.md`.
- **Regime detector** (`detect_regime`, once daily, cached in `data/regime_cache.json`): composite score = 40% price (SMA50/200, 30d momentum) + 30% Fear & Greed + 30% Binance Futures funding rate. Scores >60 = BULL/LONG, <40 = BEAR/SHORT-enabled, 40–60 = NEUTRAL/LONG-only.
- **Scan interval:** 300 seconds (configurable in `config.json`)
- **Authoritative system doc:** `docs/superpowers/specs/es/2026-04-18-documento-completo-sistema-trading.md` — read this before touching sizing, symbol selection, or the regime detector.

### Key API Endpoints (`btc_api.py`)
- `GET /symbols` — real-time status for all monitored symbols
- `GET /signals` — signal history (filterable)
- `POST /scan` — force manual scan
- `GET /config` / `POST /config` — read/write config.json
- `GET /ohlcv` — OHLC data for frontend charts
- `POST|GET /positions`, `PUT /positions/{id}`, `POST /positions/{id}/close` — position CRUD
- `GET /docs` — Swagger UI

### Frontend Structure (`frontend/src/`)
- `api.ts` — typed fetch wrapper, base URL is `/api` (nginx-proxied to port 8000)
- `types.ts` — TypeScript interfaces (`SymbolStatus`, `Signal`, `Position`, etc.)
- Components auto-refresh every 30 seconds; manual refresh + force-scan buttons available

### Operational Model
Signal generation is automatic; entry/close decisions require manual approval via CLI or frontend (Telegram is outbound only — no inbound bot for trade approval). Exclusions E2–E5 in `btc_scanner.py:305-335` are manual-check by design — see `docs/superpowers/specs/es/2026-05-01-operational-model-manual-gating.md` for the full classification and the backtest-vs-live distinction.

### Cost model v2 (post-Phase 0 of epic #338)
- **Slippage formula:** sqrt-participation (Almgren-Chriss family). `slippage_bps = base_bps + size_factor × sqrt(notional/liquidity_per_min)`, capped at `EXTREME_PARTICIPATION_CAP_BPS = 500` (5%) per fill. Migration from v1 linear in `backtest_costs.py` (PR #341). v1 linear path still available via `model='v1'` for parity testing.
- **Anchor parity preserved:** at 0.1% participation, v2 and v1 produce identical total slippage per tier (calibration design invariant tested in `test_backtest_costs_v2.py::TestAnchorParity`).
- **Funding-rate accounting:** per-tier conservative bps per 8h (`major=1.0`, `mid=2.0`, `small=5.0` in `costs_calibration.json`). Charged on every 8h funding interval the position is held (floor semantics: 7h pays 0, 8h pays 1, 24h pays 3). Conservative mode = always positive cost regardless of position direction.
- **Forensic mitigation:** the DOGE -$30K single-trade case from audit H8 (#323) is mitigated >1000× under v2. v1 produced unbounded ~$19.8M per-fill cost on the catastrophically thin bar; v2 caps at $1,050. Vol-targeting in the new strategy class (below) prevents the $21K notional from being placed in the first place.
- **Calibration sources cited in `costs_calibration.json`:** Almgren-Chriss (2001), Donier-Bonart (2015), Tóth et al (2011).

### Regime-allocation strategy class (epic #338, post-Phase 1)
Structurally distinct alternative strategy class to the LRC architecture. Mutually exclusive via `cfg.regime_allocation.enabled` (nested) or `cfg.regime_allocation_enabled` (flat, test convenience). Default **OFF** in `config.defaults.json` — opt-in only; not yet validated (Phase 2-6 pending).

**Locked parameters** (§8 of epic spec):
| Param | Value | Where |
|---|---|---|
| Signal | Equal-weight vote ensemble | `strategy/donchian_ensemble.py::ZARATTINI_LOOKBACKS = (5, 10, 20, 30, 60, 90, 150, 250, 360)` days |
| Update frequency | Daily (23:00 UTC close) | `_simulate_strategy_regime_allocation` in `backtest.py` |
| Portfolio vol target | 30% annualized | `cfg.regime_allocation.portfolio_vol_target` |
| Sizing | Vol-targeting (replaces R-multiple) | `strategy/vol_targeting.py::compute_position_size` |
| SHORT | Bidirectional rotational (requires perps + cross-margin) | dispatch direction sign from ensemble vote |
| Max position per symbol | 20% of capital | `cfg.regime_allocation.max_position_pct` |
| Leverage cap | 2x | `cfg.regime_allocation.max_leverage` (applied by portfolio orchestrator) |
| Min position | $50 USD (Binance min) | `cfg.regime_allocation.min_position_usd` |
| Exits | Signal-based (no SL/TP/TL) | `SIGNAL_FLIP` / `SIGNAL_EXIT` / `BANKRUPT` / `SIM_END` |

**Architectural notes:**
- When flag is on: `evaluate_signal` delegates to `_populate_regime_allocation_decision` (uses df1d only); `simulate_strategy` delegates to `_simulate_strategy_regime_allocation` (daily-update loop). LRC + trend-pullback paths are bypassed entirely.
- When flag is off: LRC path is byte-identical (confirmed by `test_strategy_core` + `test_backtest_*` regression).
- Single-symbol scope in `_simulate_strategy_regime_allocation`. Portfolio-level orchestration (n_active_symbols, leverage cap across symbols) is the caller's responsibility — not built in Phase 1.
- Warmup: 390 daily bars required (longest lookback 360 + vol window 30). Symbols with shorter history return NONE with `regime_allocation_warmup` reason.
- Cost model v2 + funding rate is the default for the regime-allocation path. Funding accrues per 8h interval the position is held (epic §8.5 SHORT bidirectional → perp dependency).

**Authoritative spec doc:** `docs/superpowers/specs/es/2026-05-13-epic-regime-allocation-strategy-pivot.md` — read this before touching anything under the `regime_allocation` flag.

## Configuration

**`config.json`** (root) — primary config read by both scanner and API:
```json
{
  "webhook_url": "http://localhost:5678/webhook/crypto-scanner",
  "telegram_chat_id": "...",
  "telegram_bot_token": "...",
  "scan_interval_sec": 300,
  "num_symbols": 20,
  "signal_filters": {
    "min_score": 4,
    "require_macro_ok": false,
    "notify_setup": false
  },
  "security": {
    "webhook_allow_private_ips": true
  },
  "proxy": ""
}
```

Proxy format when needed: `socks5://127.0.0.1:1080`

### `security.webhook_allow_private_ips` (#127)
SSRF guard for `webhook_url` (and `notifier.channels.webhook.endpoints`). Default `false` — rejects loopback, RFC1918, link-local, multicast, unspecified, reserved. The localhost-n8n setup shown above (`http://localhost:5678/...`) requires `webhook_allow_private_ips: true`, because `localhost` resolves to `127.0.0.1` which is loopback. Even with the flag on, link-local (`169.254.169.254` / AWS EC2 IMDS) is ALWAYS blocked — the flag relaxes local-network trust, not cloud-metadata exposure. POST /config can carry both fields in one request (`{"webhook_url":"http://localhost:5678/...","security":{"webhook_allow_private_ips":true}}`).

## Logs & Data
- `logs/signals_log.txt` — human-readable signal entries/exits
- `logs/watchdog.log` — process supervisor log
- `logs/webhook.log` — webhook receiver log
- `data/symbols_status.json` — current symbol state (auto-generated)
- `data/signals_history.csv` — CSV export of all signals

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

### Inviting users — guardrail (#271, CLOSED 2026-05-16)

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

## Database access

Three-layer separation:

### 1. Pure SQL helpers (`db/*.py`, `auth/*.py`, etc.)

Receive `con: sqlite3.Connection` as a mandatory first argument. They run SQL and return data. No `transaction()` calls. No side-effects (no HTTP, no file I/O, no logging beyond DEBUG). Examples: `db_close_position_sql`, `db_get_capital`, `apply_pnl_to_capital`, `db_create_position`.

**Documented exceptions** (Cat. 2 hidden business operators living in helper directories — operator-extraction deferred to separate tickets per the rationale in `docs/superpowers/analysis/2026-05-25-446-preconditions-synthesis.md`):

- `db/schema.py::init_db` — bootstrap orchestrator; opens its own `transaction()` and calls migration helpers.
- `db/signals.py::save_scan` — dual-transaction pattern (scan write + outcomes write); calls `transaction()` directly twice.
- `auth/audit.py::log_auth_event` — fallback to stderr if DB write fails; calls `transaction()` directly.
- `notifier/dispatch_per_user.py::dispatch_signal_to_users` — fan-out orchestrator; calls `transaction()` directly and fires `notify()` side-effect.

These four are recognized exceptions today. When their operator-extraction lands, they migrate to `operators/` and this list shrinks.

### 2. Business operators (`operators/*.py`)

Own `transaction()` for one named business transition. Orchestrate side-effects. Declare atomicity. The only legal entry point for the transitions they represent. Currently: `PositionClosure` (closing a position with atomic capital roll-in + post-commit health/notify/event-log/snapshot).

Pattern:

```python
from operators.position_closure import PositionClosure

with PositionClosure(
    pos_id=42, exit_price=110.0, exit_reason="TP_HIT",
    mode="USER", caller_tenant_id=tenant_id,
) as closure:
    outcome = closure.execute()
```

### 3. Direct `with transaction()` for ad-hoc unit-of-work

When the caller needs a transactional scope around one or more pure SQL helpers but the operation isn't a named business transition, wrap the helpers in `with transaction() as con:` directly:

```python
from db.transaction import transaction
from db.signals import get_latest_signal

with transaction() as con:
    sig = get_latest_signal(con, "BTCUSDT")
```

### 4a. Precheck reads that feed a write transaction (`precheck_connection()`)

When an operator needs to read state BEFORE deciding whether to open a write transaction (e.g., ownership check, idempotency check), use `precheck_connection()` from `db.transaction`. The contract requires the caller to extract any field the write-tx will need into an **immutable snapshot value** (see `operators.precheck.PositionSnapshot`) BEFORE the block exits — the connection MUST NOT escape.

```python
from db.transaction import precheck_connection
from operators.precheck import PositionSnapshot

with precheck_connection() as con:
    row = db_get_position_by_id(con, pos_id)
snapshot = PositionSnapshot(pos_id=row["id"], tenant_id=row["tenant_id"], ...)
# Later: open transaction() and re-validate snapshot's mutable fields.
```

The write-tx that follows MUST re-validate the snapshot's mutable fields (e.g., `tenant_id`, `status`) against a fresh re-SELECT inside `BEGIN IMMEDIATE`. Immutable fields (e.g., `entry_price`, `qty`) are trusted from the snapshot directly. See `operators/position_closure.py` for the canonical implementation.

### 4b. Terminal reads (`snapshot_connection()`)

When a read is **terminal** — its result is serialized to an output (JSON file, HTTP response, log) and NOT used to drive a subsequent mutation — use `snapshot_connection()`:

```python
from db.transaction import snapshot_connection

with snapshot_connection() as con:
    all_pos = db_get_positions(con)
```

No follow-up write-tx, no re-validation obligation. Used today by `update_positions_json` (snapshot to JSON file).

### Threat model (applies to both 4a and 4b)

Both helpers set `PRAGMA query_only = 1` on the connection. INSERT/UPDATE/DELETE raise `sqlite3.OperationalError`. **This is a cooperative latch, not a sandbox:** callers can re-enable writes via `PRAGMA query_only = 0`, `executescript` with embedded PRAGMA, or writes to `temp.*` tables. SQLite does not provide an ontologically read-only connection.

The mechanism is a **detector**, not a defense. Its value is converting bugs of "helper mistakenly mutates when contract says read-only" into LOUD errors at test time. The semantic invariant "this phase does not mutate the world" lives at the CALL SITE (extract → snapshot → terminate or write-tx), not in the primitive. Pure SQL helpers receive `con` from their caller; they never call `precheck_connection` or `snapshot_connection` themselves.

The two helpers share implementation but bear distinct call-site contracts. Mixing them (using `snapshot_connection` for a precheck that will feed a write-tx, or `precheck_connection` for a terminal read) is a documentation error that future contributors should reject in code review.

New business operators emerge from evidence (caller composes >1 helper + side-effect with conditional behavior), not preemptively. See `docs/superpowers/analysis/2026-05-25-446-tx-or-use-analysis-and-direction.md` for the rationale (Voronov, 2026-05-25).

### Known scope gap

`F-05` (trading invariant "every mutation derived from one tick of price decision belongs to one serializable transaction") **applies per-close** in Phase 2 of `check_position_stops`, **not per-tick**. The Phase 2 loop wraps each `PositionClosure(SYSTEM)` in `try/except: continue`, so partial-failure observability across N positions in the same tick is currently absent. See #453 for the issue tracking the integrity-observational debt (Voronov reframe of Serrano F-NEW Plano 1, 2026-05-25).

## Capas de enforcement de invariantes (Voronov 2026-05-26)

El dominio del repo afirma invariantes que el almacenamiento no garantiza por defecto. Cada vez que esa asimetría no se nombra, el código paga la diferencia en **membranas silenciosas**: `or 0`, "código de revisor", re-validaciones parciales. Este registro lista las invariantes de dominio que tocan el cluster C2 (#467/#468/#469) y la capa que las enforza.

Cuatro capas posibles, de más fuerte a más débil:

| Capa | Cómo enforza | Quién detecta violación |
|---|---|---|
| **Schema** | DDL constraint (CHECK, NOT NULL, FK, UNIQUE) | El motor SQLite, en write |
| **Tipo** | Anotación + **órgano de rechazo en runtime** (`__post_init__` con `isinstance`, factory privada con sentinel, NewType propagado al consumer). En un lenguaje sin type-checker en CI, la anotación sola es convención disfrazada de sintaxis. La rung 'tipo' sólo es real cuando el constructor o el factory rechaza la entrada equivocada con `TypeError`. | mypy estricto en CI **o** runtime check explícito (`__post_init__` / factory sentinel) |
| **Test** | Invariant test que falla si la violación ocurre | pytest en CI |
| **Convención** | Comentario en código / sección de CLAUDE.md / revisión humana | Revisor (si recuerda mirar) |

### Regla de coherencia (Voronov post-Serrano 2026-05-26)

> "La fuerza de una garantía está acotada por encima por el órgano más débil que puede rechazarla en la frontera que la garantía dice proteger."

Tres consecuencias para esta codebase:
1. Las anotaciones forward-ref en dataclasses no son enforcement. Si una clase declara un field con un tipo específico, debe tener `__post_init__` que rechace lo contrario, o el field debe construirse vía factory privada con sentinel. Sin órgano de rechazo, la anotación pertenece a la rung 'convención', no a 'tipo'.
2. `NewType` solo cuenta como 'tipo' si el consumer también está anotado y el camino completo es estructuralmente coherente. Una `PrecheckConn` definida y luego pasada a una función con anotación `sqlite3.Connection` regresa a 'convención'.
3. Cerrar un issue (`#NNN`) contra una eliminación parcial de la patología deja la enfermedad en los sitios no tocados. Closure requiere que el predicado del issue sea verdad en todos los call sites, no sólo los listados en el plan.

### Invariantes registradas — estado tras Cluster D (post-#471 #470 #473, post-convergencia Serrano/Aurelius)

> Esta tabla **reemplaza** la antigua tabla C2 (que listaba sólo #467/#468/#469). Las tres filas C2 están retenidas aquí; añade las siete filas de Cluster D. Una sola tabla de verdad — la duplicación adjacente previa era deuda doc nombrada por Serrano MEDIUM 10.

| Invariante de dominio | Capa enforced | Mecanismo | Issue cerrado |
|---|---|---|---|
| `qty` siempre tiene valor numérico para positions activas (o `status='legacy_unmeasurable'`) | **Schema** | `CHECK (qty IS NOT NULL OR status='legacy_unmeasurable')` en `positions` (vía `_migrate_qty_not_null`) | #467 |
| `precheck_connection` y `snapshot_connection` son contratos distintos | **Tipo** | `NewType("PrecheckConn", sqlite3.Connection)` y `NewType("SnapshotConn", sqlite3.Connection)` en `db/transaction.py` — mypy detecta mis-uso | #468 |
| Los campos del snapshot consumidos por el write-tx no cambian entre precheck y BEGIN IMMEDIATE | **Tipo + runtime check** | `OwnershipValidatedSnapshot` (factory privada en `operators/precheck.py`) + field-by-field re-validation en `PositionClosure.execute()` cubre los 6 campos del `PositionSnapshot` | #469 + F6 |
| `qty > 0` para positions activas (cierra el 0.0-bypass) | **Schema** | `CHECK ((qty IS NOT NULL AND qty > 0) OR status='legacy_unmeasurable')` (via `_migrate_qty_positive`) | #471 |
| `tenant_id IS NOT NULL` para positions activas | **Schema** | `CHECK (tenant_id IS NOT NULL OR status IN ('legacy_unmeasurable','legacy_no_tenant'))` (via `_migrate_tenant_id_not_null`) | #471 |
| `tenant_id: int > 0` en la frontera de entrada (anotación + rechazo runtime) | **Tipo + runtime órgano de rechazo** | `_build_open_request` rechaza `tenant_id` no-int, ≤ 0, bool, o None con `BodyValidationError` (regla de coherencia post-Serrano) | #471 F6 |
| Idempotencia estructural: no dos open rows con el mismo `(tenant_id, scan_id)` | **Schema** | `CREATE UNIQUE INDEX idx_positions_open_scan_unique ... WHERE status='open' AND scan_id IS NOT NULL` (via `_migrate_unique_open_scan`) | #470 |
| Probe + INSERT + cache write atómicos por request (no TOCTOU race entre Idempotency-Key probe y row INSERT) | **Operador-ligero** | `BirthRegistrar.register` corre todo bajo UNA `with transaction()` (BEGIN IMMEDIATE) — colapsa los rungs por el reframe de Aurelius | #470 (race), #473 |
| Idempotencia HTTP con body-fingerprint (misma key + diferente body → 409, no replay) | **Tipo (HTTP) + Schema** | tabla `idempotency_keys` con columna `body_sha256` (SHA-256 del canonical-JSON post-Pydantic); `BirthRegistrar` levanta `DuplicateIdempotencyKeyError` si el fingerprint no matchea | #473 |
| Input externo → `Position` legítima (allowlist symbol, direction enum, qty>0, SL/TP relacional, entry_ts window) | **Tipo + runtime órgano de rechazo** | Pydantic `OpenPositionRequest` (extra='forbid') + factory privada `_build_open_request` con `_OPEN_REQUEST_SENTINEL` en `api/positions_birth.py` | #471 F5/F6/F7/F9, #473 |
| Error taxonomy 422/409/503 vs 500 — traducción al layer originante, no por substring de prosa | **Tipo** | `BirthError` hierarchy (`BodyValidationError`, `AmbiguousQtyError`, `StaleEntryTsError`, `TenantViolationError`, `DuplicateIdempotencyKeyError`, `IdempotencyCacheUnavailableError`, `UniqueViolationError`, `SchemaIntegrityError`); `BirthRegistrar._translate_integrity_error` mapea por `sqlite_errorcode` + fragmento del CHECK (no por substring de prosa inglesa). `IdempotencyCacheUnavailableError` (503) cierra el silent-duplicate window cuando el cliente pidió `Idempotency-Key` y el cache está unreachable (Serrano HIGH 2 post-convergencia) | #473 |
| Post-commit atomicidad + observabilidad de `update_positions_json` | **Operador-ligero + log estructurado** | `BirthRegistrar.register` posee la tx; si el snapshot post-commit falla, emite `log.error("POSITION_SNAPSHOT_STALE pos_id=... tenant=... snapshot_error=...")` (no swallows silencioso — Serrano HIGH 5) | #473 F8 |
| Observabilidad + fail-closed del cache de idempotencia | **Log estructurado + Tipo** | `IdempotencyCache.get/.set` emiten `log.error("IDEMPOTENCY_CACHE_UNREACHABLE ...")` cuando la tabla falla, y levantan `_CacheUnavailable`. `BirthRegistrar` lo traduce a `IdempotencyCacheUnavailableError` (503) **sólo cuando** el request portaba `Idempotency-Key` — un request sin key bypassa el cache y nunca ve la excepción. Cierra el silent-duplicate window (cache no-op + INSERT commit + retry → dos rows bajo la misma key) | Serrano MEDIUM 11 + HIGH 2 |
| Cluster D migrations atómicas como grupo | **Schema** | Las cuatro sub-migraciones (`_migrate_qty_positive`, `_migrate_tenant_id_not_null`, `_migrate_unique_open_scan`, `_migrate_idempotency_keys`) corren bajo UNA `with transaction()` en `init_db` — partial failure roll-back-ea el cluster entero | Serrano HIGH 7 |

### Principio dual de la frontera Cluster D (Voronov 2026-05-26)

> Una `Position` existe si y solo si su acto de nominación satisfizo simultáneamente: (a) el contrato existencial del schema (qué la convierte ontológicamente en Position), y (b) el contrato de nominación de la frontera de entrada (qué valida que el input externo intentaba declararla legítimamente). Schema es la frontera que ningún caller evade; nominación es donde el error toma forma semántica.

> `close()` valida una transición entre dos estados conocidos del mismo objeto. `open()` no valida transición — valida un acto de nominación. Son primos, no hermanos. Cluster D NO introduce un `PositionOpen` operador simétrico a `PositionClosure` — eso sería "falsa simetría — imitación visual; no comparte contrato". `BirthRegistrar` es un op-ligero: validación ocurrió arriba (Pydantic + `_build_open_request`); el registrar solo posee la atomicidad transacción + post-commit.

### Documented status: `legacy_no_tenant`

Status especial usado por `_migrate_tenant_id_not_null` (#471) para reconocer rows históricas pre-multi-tenant cuya `tenant_id` no es recuperable. El schema CHECK exempta `legacy_unmeasurable` Y `legacy_no_tenant`. Rows ya marcadas `legacy_unmeasurable` (de la migración C2) NO se re-clasifican — el OR del CHECK las exempta directamente. Convierte 2018 mentiras silenciosas (tenant_id=NULL implícito) en reconocimientos explícitos.

### Patrón nombrado: "invariantes de dominio sin contraparte estructural"

Cada futuro issue de la familia `or X`, "código de revisor", "trust-and-document" debería compararse contra este registro. Si la invariante pertenece a una capa más fuerte que `convención`, moverla es la fix correcta.

### Finding meta — asimetría contractual create vs close (Voronov post-medición 2026-05-26)

Medición de `signals.db` reveló 670 de 2018 positions con `qty IS NULL` (33%), **ZERO backfillables** desde `size_usd/entry_price`. La asunción del plan original era que `qty NULL` era deuda de cierre (size_usd existió, se perdió). La realidad: deuda de nacimiento (size_usd nunca prometido).

> **El sistema tiene un `close()` que asume invariantes que `open()` nunca prometió.** `qty NULL` no es el problema — es el síntoma. La membrana de cierre asume un contrato que la membrana de apertura nunca firmó.

Implicación: hasta que `create_position` exija lo que `close_position` asume, todo CHECK en la salida es teatro defensivo. Issue separado: asimetría contractual create vs close (open issue antes del PR merge — Task 13.5 del plan 2026-05-26-467-468-469).

### Documented status: `legacy_unmeasurable`

Status especial usado por `_migrate_qty_not_null` (#467) para reconocer 670 rows históricas cuya `qty` nunca fue medida y no es derivable. El schema CHECK constraint exempta este status: `CHECK (qty IS NOT NULL OR status='legacy_unmeasurable')`. Convierte 670 mentiras silenciosas en 670 reconocimientos explícitos.

### Known scope gap (post-D, post-convergencia)

Las siguientes patologías están reconocidas y deferidas — no son parte del cierre estructural de #471/#470/#473 y no bloquean su merge. Cada una tiene/necesita issue separado.

- **Rate limiting (#473 F10) — `Advances #473`, no `closes`.** El endpoint `POST /positions` no tiene throttle. Un cliente legítimo con la `Idempotency-Key` correcta puede inundar el endpoint creando rows distintas (cada body único pasa). El sistema confía en autenticación + JWT para acotar abuso. F10 vive en issue follow-up separado (#483). El PR body del Cluster D dice `Advances #473 (F10 deferred to #483)` para evitar overstatement (Serrano LOW 15).
- **Direction enum sólo en boundary (#482)** — el schema acepta cualquier TEXT en `positions.direction`; el `Literal["LONG","SHORT"]` vive sólo en la frontera Pydantic. Una migración manual o cliente legacy podría escribir `"long"` en lowercase. Mover a `CHECK (direction IN ('LONG','SHORT'))` es follow-up trivial.
- **`scan_id` FK (#484)** — `scan_id` es nullable y referencia una tabla que no existe (no hay `scans` con esa semántica de signal_id). El UNIQUE parcial cierra la race condition (#470) pero NO la integridad referencial.
- **Idempotency cache eager sweeper (Serrano MEDIUM 4)** — lazy cleanup es per-key only; one-shot keys que nadie re-pregunta nunca leakean en la tabla. El índice `idx_idempotency_expires` ya está creado para soportar un sweeper futuro. Follow-up pendiente (sin issue formal aún — bajo impacto).
- **`entry_ts` window relajada (Serrano MEDIUM 9)** — `[now-7d, now+60s]` rechaza backfills legítimos y clientes con skew >60s. Requiere decisión UX antes de relajar. Sin issue formal aún.
- **`legacy_no_tenant` consumer filters (Serrano MEDIUM 12)** — el status nuevo está en el schema; ningún consumer del UI / agente filtra rows con ese status explícitamente. Hoy es teórico (rows con `legacy_no_tenant` no son `open`, y las queries más activas filtran por status). Audit de cada consumer pendiente.

## Known Limitations
- `watchdog.py` uses Windows-specific commands (`tasklist`, `taskkill`, `wmic`, `netstat`) and won't run on Linux/Mac
- The webhook process itself is not supervised by the watchdog (only btc_api.py is)
- Strategy backtest numbers in `docs/superpowers/specs/es/2026-04-17-formula-ganadora-resultados-finales.md` and `docs/superpowers/specs/es/2026-04-18-documento-completo-sistema-trading.md` are **pre-#223/#224** (phantom-profit fix). The "real strategy contribution" decomposition in PR #223 showed those numbers were inflated. **Do not cite those numbers as baseline** — see #272 for the re-baselining work.
