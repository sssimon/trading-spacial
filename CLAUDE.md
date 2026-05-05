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
  "proxy": ""
}
```

Proxy format when needed: `socks5://127.0.0.1:1080`

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

   Discovered during A.4-1.5 sweep halt (2026-05-04, #305) — sanity check fired with PENDLE showing $-1,702,401 = 170× initial capital, traced to single-trade overshoot via amplification. Spec D9 §2.10 + `docs/superpowers/research/2026-05-02-structural-fix-parameter-study.md` document the methodological framing.

### Inviting users — guardrail (#271)

`trading.sdar.dev` does **not** get additional user accounts until both: (a) Epic A passes its validation bar (A.4 documented, A.6 published), and (b) Epic B (#253) is implemented. This is a self-imposed contract; closing #271 requires explicit confirmation of both conditions.

## Known Limitations
- `watchdog.py` uses Windows-specific commands (`tasklist`, `taskkill`, `wmic`, `netstat`) and won't run on Linux/Mac
- The webhook process itself is not supervised by the watchdog (only btc_api.py is)
- Strategy backtest numbers in `docs/superpowers/specs/es/2026-04-17-formula-ganadora-resultados-finales.md` and `docs/superpowers/specs/es/2026-04-18-documento-completo-sistema-trading.md` are **pre-#223/#224** (phantom-profit fix). The "real strategy contribution" decomposition in PR #223 showed those numbers were inflated. **Do not cite those numbers as baseline** — see #272 for the re-baselining work.
