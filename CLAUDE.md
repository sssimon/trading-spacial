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
- **Indicators:** LRC (100-bar Linear Regression Channel), RSI, Bollinger Bands, SMA100
- **Entry zone:** LRC_LONG_MAX = 25% (price within 25% of lower channel band)
- **Score tiers:** 0–1 = 50% size, 2–3 = normal, ≥4 = premium signal
- **Default symbols:** BTC, ETH, BNB, SOL, XRP, ADA, AVAX, DOGE, DOT, MATIC, LINK, LTC, UNI, ATOM, XLM, NEAR, FIL, APT, OP, ARB (top 20, dynamically fetched from CoinGecko with fallback)
- **Scan interval:** 300 seconds (configurable in config.json)

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

## Known Limitations
- `trading_webhook.py` has a hardcoded path to OpenClaw (`C:\Users\simon\AppData\Roaming\npm\openclaw.cmd`)
- `watchdog.py` uses Windows-specific commands (`tasklist`, `taskkill`, `wmic`, `netstat`) and won't run on Linux/Mac
- The webhook process itself is not supervised by the watchdog (only btc_api.py is)
