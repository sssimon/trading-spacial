# trading-spacial

[![CI](https://github.com/sssimon/trading-spacial/actions/workflows/ci.yml/badge.svg)](https://github.com/sssimon/trading-spacial/actions/workflows/ci.yml)

> A research-grade laboratory for evaluating systematic crypto-trading strategies, with a working signal scanner + dashboard on top.

The surface is a Bitcoin / altcoin signal scanner: multi-timeframe technical analysis (4H macro → 1H signal → 5M entry), scored signals delivered to Telegram per-user, React dashboard with position tracking and an in-app LLM copilot.

The substance is the methodology underneath: pre-registered hypotheses, a locked holdout dataset with two-layer access guards, explicit structural-fix ledger (bug fixes vs. modeling decisions), and honest closure of failed hypotheses. **See [`METHODOLOGY.md`](METHODOLOGY.md)** for what makes this different from the 50,000 other crypto bots on GitHub.

**Status (2026-05-22):** the LRC strategy class has been re-baselined post-PR #223 and returned `EDGE_WEAK`. The only confirmed edge is operator-discretion exit timing (Direction A Q2). Two structurally distinct strategy directions have been explored and closed: (1) regime-allocation pivot (epic [#338](https://github.com/sssimon/trading-spacial/issues/338)) closed 2026-05-15 with verdict `PHASE_3_INSUFFICIENT_DATA`, and (2) trend-pullback (R3) closed earlier with FAIL verdict. Current active work is operator-tooling (multi-tenant production [#253](https://github.com/sssimon/trading-spacial/issues/253), per-user copilot history [#428](https://github.com/sssimon/trading-spacial/issues/428), onboarding wizard [#427](https://github.com/sssimon/trading-spacial/issues/427)). Do not trade this system live.

---

## Architecture

```text
Binance API (Bybit fallback)
  └─ btc_scanner.py      — fetch OHLCV, compute indicators (LRC, RSI, BB, SMA, ATR, ADX),
     |                     score signals (0–9), gate by regime detector
     ├─ strategy/         — modular indicators, regime detection, sizing, vol-targeting
     ├─ strategies/       — ⚠️ legacy ADX-based router (kept until consolidation; see strategies/router.py)
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
- **K-cap (#309)**: `abs(pnl_usd) ≤ 10 × risk_amount` per trade. Bounds the catastrophic-bar mechanism.
- **Bankruptcy halt (#313)**: symbol stops new entries when equity drops below `0.1 × INITIAL_CAPITAL`. Existing positions close naturally.

For the why behind these bounds, see [`METHODOLOGY.md`](METHODOLOGY.md) § Structural fixes shipped.

**Curated symbols (static, 10):** BTC, ETH, ADA, AVAX, DOGE, UNI, XLM, PENDLE, JUP, RUNE. Static since epic #135 confirmed via 768+ backtest combinations that the 13 removed tokens (BNB, SOL, XRP, DOT, MATIC, LINK, LTC, ATOM, NEAR, FIL, APT, OP, ARB) are not profitable with this strategy regardless of parameters.

---

## Stack

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

---

## Quick Start

### 1. Backend

```bash
pip install -r requirements.txt        # runtime only
# OR for development (adds pytest + httpx):
pip install -r requirements-dev.txt

cp .env.example .env       # then fill in AUTH_JWT_SECRET (see comment in file)
python btc_api.py          # REST API → http://localhost:8000
python watchdog.py         # Process supervisor (Windows only)
```

On first launch the system has no users. See **First-time setup** below.

### 2. Frontend

```bash
cd frontend
npm install
npm run dev      # Dev server → http://localhost:5173
npm run build    # Production build
```

### 3. Docker

```bash
# Generate a JWT secret and persist it (or use a secrets manager)
echo "AUTH_JWT_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(64))')" >> .env

docker compose up --build
# Backend  → :8000  (multi-stage image, runs as uid 1000)
# Frontend → :3000
```

Setup banner appears in `docker compose logs trading` on first boot —
click the printed `http://localhost:8000/setup?token=...` URL to create
the admin user.

### 4. Windows autostart

```powershell
.\scripts\INSTALAR_AUTOSTART.ps1   # registers watchdog as Task Scheduler task
.\scripts\REINICIAR_SERVICIOS.ps1  # restart all services
```

---

## First-time setup

The system has no default user. The first user is created via one of three
paths — pick the one that matches your deployment.

The auth subsystem refuses to boot without `AUTH_JWT_SECRET`. Generate one
and keep it secret:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(64))'
```

### Path A — Web setup (recommended for self-hosters)

Default behaviour. On first boot, the server prints a banner like:

```
================================================================
  SETUP REQUIRED — first-time installation detected
================================================================

  No users exist yet. Create the first admin user via:

  Web (recommended):
    http://localhost:8000/setup?token=<TOKEN>

  Or CLI:
    python scripts/create_user.py
================================================================
```

Open the URL. The form works in any browser — including text-mode browsers
(`lynx`, `w3m`) with JavaScript disabled. Submit email + password (≥ 12
chars, must contain a letter and a digit). After submission, `/setup` is
permanently disabled (returns 404) and you'll be redirected to `/login`.

The setup token lives only in process memory. If you lose it, restart the
backend — a new token is generated.

### Path B — CLI (recommended for remote servers)

Use this when you'd rather not expose any web setup surface. Either set
`AUTH_DISABLE_WEB_SETUP=1` to suppress the web form, or just run the CLI
directly:

```bash
python scripts/create_user.py --email you@example.com --role admin
# (prompts for password twice via getpass — no echo)
```

Same password rules. Creates an admin user; the next time the backend
boots it sees the user and skips the setup banner.

### Path C — Environment variables (automated deploys)

For Ansible, Terraform, docker-compose with secrets, etc. Set both env
vars before booting:

```bash
AUTH_INITIAL_ADMIN_EMAIL=admin@example.com
AUTH_INITIAL_ADMIN_PASSWORD=<from your secrets manager>
```

If both are set AND no users exist, the backend creates the admin during
startup, marks setup as complete, and continues without printing the
banner.

> ⚠️ The password is plaintext in environment variables. For real
> production, source it from a secrets manager (Vault, AWS Secrets
> Manager, sops, doppler) and inject at runtime. Do **not** commit a
> real password to `.env`.

Setting only one of the two variables (e.g. email but no password) is a
hard boot failure — there is no silent fallback.

### Password reset

There is no web "forgot password" flow on purpose. Recovery requires
shell access to the server:

```bash
python scripts/reset_password.py --email user@example.com
```

This rehashes the password and revokes every active refresh token for the
user (force re-login on every device).

### Edge case: admin row deleted

If the only admin user gets deleted but `system_state` still has
`setup_completed_at`, the system is inaccessible via the web — `/setup`
returns 404 by design (it's a one-shot bootstrap, not a recovery flow).

Recover via CLI (creates a new admin without re-enabling `/setup`):

```bash
python scripts/create_user.py --role admin
```

Or, if you specifically want the web setup form to come back, take a
backup first and then clear the marker:

```bash
cp signals.db signals.db.backup-$(date +%Y%m%d-%H%M%S)
sqlite3 signals.db "DELETE FROM system_state WHERE key='setup_completed_at'"
# Restart the backend; the next boot will print a fresh setup banner.
```

---

## Configuration

Copy and fill in `config.json` (excluded from git — never commit tokens):

```json
{
  "webhook_url": "http://localhost:5678/webhook/crypto-scanner",
  "telegram_chat_id": "YOUR_CHAT_ID",
  "telegram_bot_token": "YOUR_BOT_TOKEN",
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

Proxy format (if needed): `socks5://127.0.0.1:1080`

**Note on `security.webhook_allow_private_ips`** (#127): default is `false` — the SSRF guard blocks loopback / RFC1918 / link-local webhook URLs. The localhost-n8n example above requires the flag set to `true`. Even with the flag enabled, link-local (e.g. `http://169.254.169.254/` AWS EC2 metadata endpoint) is always blocked.

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/symbols` | Real-time status for all monitored pairs |
| `GET` | `/signals` | Signal history (filterable) |
| `GET` | `/signals/latest` | Latest signal with full detail |
| `POST` | `/scan` | Force a manual scan |
| `GET` | `/config` | Read current config |
| `POST` | `/config` | Update config |
| `GET` | `/ohlcv` | OHLC data for charts |
| `GET/POST` | `/positions` | Position management |
| `PUT` | `/positions/{id}` | Update position |
| `POST` | `/positions/{id}/close` | Close position |
| `GET` | `/docs` | Swagger UI |

---

## Tests

```bash
python -m pytest tests/ -v
python -m pytest tests/test_scanner.py -v
python -m pytest tests/test_api.py -v
```

---

## Project Structure

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
│                              #   in audit follow-ups)
│
│  # — Modular code —
├── api/                       # REST endpoints split by domain
├── auth/                      # JWT auth + setup paths
├── db/                        # SQLite schema + migrations + capital
├── notifier/                  # Per-user signal dispatch (multi-tenant)
├── strategy/                  # Indicators, regime, sizing, kill-switch, vol-targeting
├── strategies/                # ⚠️ Legacy ADX router — being consolidated (tracked)
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
│   ├── holdout/               # 🔒 Locked holdout dataset — data/holdout/ (read-only, guard-protected)
│   ├── regime_cache.json
│   ├── symbols_status.json
│   └── signals_history.csv
└── logs/                      # Runtime logs (signals, webhook, watchdog)
```

---

## Data & Logs

| Path | Contents |
|------|----------|
| `signals.db` | SQLite: `signals` + `positions` tables |
| `logs/signals_log.txt` | Human-readable signal entries/exits |
| `logs/watchdog.log` | Process supervisor log |
| `data/symbols_status.json` | Current symbol state (auto-generated) |
| `data/signals_history.csv` | CSV export of all signals |

---

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

---

## Notes

- `config.json` is git-ignored — contains credentials. Use the template in this README to bootstrap.
- `watchdog.py` is Windows-only (uses `tasklist`, `taskkill`, `wmic`). Linux production is supervised by systemd; the unit files are not yet in the repo (tracked).
- The curated symbol list is **static** (10 coins) since epic #135 — see Signal Logic section above.
- Binance Futures is the primary data source; Bybit is the fallback.
- The locked holdout dataset at `data/holdout/` is read-only and guard-protected. See [`METHODOLOGY.md`](METHODOLOGY.md) § Holdout dataset isolation before writing any new code that touches it.

## Research methodology

This isn't just a trading scanner. The methodology underneath — pre-registration, holdout isolation, structural-fix ledger, honest closure of failed hypotheses — is what makes this repo different from the generic crypto-bot landscape.

→ **Read [`METHODOLOGY.md`](METHODOLOGY.md)**, then [`docs/README.md`](docs/README.md) for the full spec index.
