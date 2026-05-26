---
name: architecture
description: Signal flow, components, key backend logic, API surface, frontend layout, cost model v2, and the regime-allocation strategy class. Load when working on system design, signal generation, sizing/costs, or the regime detector.
triggers:
  - "architecture"
  - "signal flow"
  - "scanner"
  - "regime"
  - "cost model"
  - "slippage"
  - "funding"
  - "strategy"
  - "endpoints"
  - "API"
edges:
  - target: context/conventions.md
    condition: when the work crosses into database access, transaction lifecycle, or invariant enforcement
  - target: context/decisions.md
    condition: when the work touches sizing, ATR tuning, holdout, or regime thresholds (leakage caveats apply)
  - target: context/stack.md
    condition: when adding a new library or external API
last_updated: 2026-05-26
---

# Architecture

## Signal Flow

```
Binance API (Bybit fallback)
  → btc_scanner.py: fetch OHLCV, calculate LRC/RSI/BB/SMA100
  → Multi-timeframe scoring (0–9)
  → btc_api.py: store to signals.db (SQLite), evaluate notification filters
  → trading_webhook.py (port 9000) → OpenClaw CLI → Telegram
     OR n8n workflow (port 5678) → Telegram node
```

## Components

| File | Purpose | Port |
|------|---------|------|
| `btc_api.py` | FastAPI REST server, DB management, scanner thread | 8000 |
| `btc_scanner.py` | Signal generation engine (indicators + scoring) | — |
| `trading_webhook.py` | Webhook receiver → Telegram via OpenClaw CLI | 9000 |
| `watchdog.py` | Process supervisor for API + webhook (Windows only) | — |
| `btc_report.py` | Standalone HTML report generator (Binance Futures, ETF flows) | — |
| `frontend/` | React 18 dashboard (symbols grid, signals table, positions) | 3000/5173 |
| `signals.db` | SQLite: `signals` + `positions` tables | — |

## Key Backend Logic (`btc_scanner.py`)

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

## Key API Endpoints (`btc_api.py`)

- `GET /symbols` — real-time status for all monitored symbols
- `GET /signals` — signal history (filterable)
- `POST /scan` — force manual scan
- `GET /config` / `POST /config` — read/write config.json
- `GET /ohlcv` — OHLC data for frontend charts
- `POST|GET /positions`, `PUT /positions/{id}`, `POST /positions/{id}/close` — position CRUD
- `GET /docs` — Swagger UI

## Frontend Structure (`frontend/src/`)

- `api.ts` — typed fetch wrapper, base URL is `/api` (nginx-proxied to port 8000)
- `types.ts` — TypeScript interfaces (`SymbolStatus`, `Signal`, `Position`, etc.)
- Components auto-refresh every 30 seconds; manual refresh + force-scan buttons available

## Operational Model

Signal generation is automatic; entry/close decisions require manual approval via CLI or frontend (Telegram is outbound only — no inbound bot for trade approval). Exclusions E2–E5 in `btc_scanner.py:305-335` are manual-check by design — see `docs/superpowers/specs/es/2026-05-01-operational-model-manual-gating.md` for the full classification and the backtest-vs-live distinction.

## Cost model v2 (post-Phase 0 of epic #338)

- **Slippage formula:** sqrt-participation (Almgren-Chriss family). `slippage_bps = base_bps + size_factor × sqrt(notional/liquidity_per_min)`, capped at `EXTREME_PARTICIPATION_CAP_BPS = 500` (5%) per fill. Migration from v1 linear in `backtest_costs.py` (PR #341). v1 linear path still available via `model='v1'` for parity testing.
- **Anchor parity preserved:** at 0.1% participation, v2 and v1 produce identical total slippage per tier (calibration design invariant tested in `test_backtest_costs_v2.py::TestAnchorParity`).
- **Funding-rate accounting:** per-tier conservative bps per 8h (`major=1.0`, `mid=2.0`, `small=5.0` in `costs_calibration.json`). Charged on every 8h funding interval the position is held (floor semantics: 7h pays 0, 8h pays 1, 24h pays 3). Conservative mode = always positive cost regardless of position direction.
- **Forensic mitigation:** the DOGE -$30K single-trade case from audit H8 (#323) is mitigated >1000× under v2. v1 produced unbounded ~$19.8M per-fill cost on the catastrophically thin bar; v2 caps at $1,050. Vol-targeting in the new strategy class (below) prevents the $21K notional from being placed in the first place.
- **Calibration sources cited in `costs_calibration.json`:** Almgren-Chriss (2001), Donier-Bonart (2015), Tóth et al (2011).

## Regime-allocation strategy class (epic #338, post-Phase 1)

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

## What Does NOT Exist Here

- **No inbound Telegram bot** for trade approval — Telegram is outbound only; approval is CLI / frontend manual.
- **No portfolio-level capital pooling** — per-symbol `INITIAL_CAPITAL=$10K` streams are independent. Cross-symbol allocation, aggregate drawdown, pooled bankruptcy is deferred. See [[decisions.md]] caveat 4.
- **No live-trading order placement** — the system emits signals; execution is operator-driven through the frontend.
- **No multiplicative risk scalers on top of `RISK_PER_TRADE=0.01`** — per-symbol volatility adaptation lives in `symbol_overrides` (epic #121), not in a sizing multiplier.
