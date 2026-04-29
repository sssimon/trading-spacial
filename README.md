# Crypto Trading Scanner — Ultimate Macro & Order Flow V6.0

[![CI](https://github.com/sssimon/trading-spacial/actions/workflows/ci.yml/badge.svg)](https://github.com/sssimon/trading-spacial/actions/workflows/ci.yml)

Automated signal system for the top 20 crypto pairs by market cap. Uses multi-timeframe technical analysis (4H macro context → 1H signal → 5M entry trigger) to generate scored entry alerts delivered to Telegram.

---

## Architecture

```
Binance API (Bybit fallback)
  └─ btc_scanner.py     — fetch OHLCV, calculate indicators, score signals
       └─ btc_api.py    — FastAPI server, SQLite storage, notification filters
            └─ trading_webhook.py  →  Telegram (via OpenClaw CLI)
               n8n workflow        →  Telegram (alternative)

frontend/               — React 18 dashboard (Vite + TypeScript)
watchdog.py             — Windows process supervisor (keeps API alive)
```

### Signal Logic

| Timeframe | Role | Indicators |
|-----------|------|-----------|
| 4H | Macro context | SMA100, trend direction |
| 1H | Main signal | LRC (100-bar), RSI, Bollinger Bands |
| 5M | Entry trigger | Reversal candle confirmation |

**Entry zone:** price within 25% of the lower Linear Regression Channel band (`LRC% ≤ 25`)

**Score tiers:**
- `0–1` → 50% position size
- `2–3` → standard size
- `≥ 4` → premium signal (+50% size)

**Default TP/SL:** 4% take profit / 2% stop loss

---

## Stack

| Layer | Tech |
|-------|------|
| Backend | Python 3.12, FastAPI, SQLite |
| Frontend | React 18, TypeScript, Vite, lightweight-charts |
| Alerts | Telegram (via n8n or OpenClaw CLI) |
| Infrastructure | Docker, Windows Task Scheduler |

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
  "proxy": ""
}
```

Proxy format (if needed): `socks5://127.0.0.1:1080`

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

```
├── btc_api.py              # FastAPI server (port 8000)
├── btc_scanner.py          # Signal engine (indicators + scoring)
├── btc_report.py           # Standalone HTML market report generator
├── trading_webhook.py      # Webhook receiver → Telegram (port 9000)
├── watchdog.py             # Process supervisor (Windows)
├── docker-compose.yml      # Frontend + n8n containers
├── requirements_scanner.txt
├── frontend/               # React 18 dashboard
│   └── src/
│       ├── components/     # SymbolsGrid, SignalsTable, PositionsPanel, ...
│       ├── api.ts          # Typed fetch wrapper
│       └── types.ts        # TypeScript interfaces
├── tests/
│   ├── test_scanner.py
│   └── test_api.py
├── scripts/                # Windows automation (PS1 + BAT)
├── Backtesting_BTCUSDT/    # Backtesting results and charts (V6)
└── data/                   # Position sizing calculator, trade tracker
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

### El scanner no genera señales
1. Verificar conexion a Binance: `curl -s https://api.binance.com/api/v3/ping`
2. Revisar logs: `tail -f logs/btc_api.log`
3. Verificar que `config.json` existe y tiene formato valido
4. Forzar scan manual: `curl -X POST http://localhost:8000/scan`
5. Revisar el endpoint de salud: `curl http://localhost:8000/health`

### Telegram no envia mensajes
1. Verificar `telegram_bot_token` y `telegram_chat_id` en `config.json`
2. Probar envio: `curl http://localhost:8000/webhook/test`
3. Verificar que `signal_filters.min_score` no es demasiado alto (default: 4)
4. Revisar logs para errores de Telegram: `grep -i telegram logs/btc_api.log`
5. Si usa proxy: verificar formato `socks5://127.0.0.1:1080`

### El dashboard no carga datos
1. Verificar que `btc_api.py` esta corriendo: `curl http://localhost:8000/status`
2. Si usa Docker: verificar que el container esta activo: `docker ps`
3. Verificar proxy en nginx: `curl http://localhost:3000/api/status`
4. Revisar la consola del navegador para errores CORS

### Errores de base de datos
1. Verificar que `signals.db` existe y no esta corrupto
2. Si esta corrupto, restaurar desde backup: `cp backups/signals_YYYYMMDD.db signals.db`
3. Para recrear la DB: eliminar `signals.db` y reiniciar `btc_api.py`

### El watchdog no inicia (Windows)
1. Verificar que Python esta en PATH: `python --version`
2. Ejecutar como administrador: `powershell -ExecutionPolicy Bypass -File scripts/INSTALAR_AUTOSTART.ps1`
3. Verificar tarea en Task Scheduler: buscar "BTCScannerWatchdog"
4. Revisar logs: `type logs\watchdog.log`

## Deployment Checklist

- [ ] Crear `config.json` con credenciales (copiar template del README)
- [ ] Configurar `telegram_bot_token` y `telegram_chat_id`
- [ ] Opcional: configurar `api_key` para proteger endpoints sensibles
- [ ] Verificar conectividad a Binance: `curl https://api.binance.com/api/v3/ping`
- [ ] Instalar dependencias: `pip install -r requirements.txt`
- [ ] Iniciar API: `python btc_api.py`
- [ ] Verificar salud: `curl http://localhost:8000/health`
- [ ] Probar Telegram: `curl http://localhost:8000/webhook/test`
- [ ] Iniciar frontend: `cd frontend && npm install && npm run dev`
- [ ] Verificar dashboard en `http://localhost:5173`
- [ ] Para produccion: `docker compose up --build`
- [ ] Configurar autostart (Windows): ejecutar `scripts/INSTALAR_AUTOSTART.ps1`
- [ ] Verificar logs se generan en `logs/`

---

## Notes

- `config.json` is git-ignored — contains sensitive credentials
- `watchdog.py` is Windows-only (uses `tasklist`, `taskkill`, `wmic`)
- Symbols list is dynamically fetched from CoinGecko every hour with fallback to a hardcoded top-20 list
- Binance Futures API is the primary data source; Bybit is the fallback
