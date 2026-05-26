---
name: stack
description: Technology stack, runtime ports, primary libraries, and security knobs. Load when adding a dependency, touching `config.json`, or auditing the webhook SSRF guard.
triggers:
  - "library"
  - "dependency"
  - "config.json"
  - "FastAPI"
  - "SQLite"
  - "webhook"
  - "SSRF"
  - "ports"
edges:
  - target: context/architecture.md
    condition: when a stack choice depends on a component (scanner, api, frontend)
  - target: context/setup.md
    condition: when stack version constraints affect how the project is installed
  - target: context/decisions.md
    condition: when revisiting why a tool was chosen (cost model, regime detector, etc.)
last_updated: 2026-05-26
---

# Stack

## Core Technologies

- **Python 3** with **FastAPI** + **uvicorn** — REST API (`btc_api.py`), webhook receiver (`trading_webhook.py`), watchdog (`watchdog.py`)
- **SQLite** (`signals.db`) — `signals` + `positions` tables; only persistence layer
- **React 18 + TypeScript + Vite** — frontend dashboard in `frontend/`
- **Docker Compose** — production frontend (`:3000`) + n8n workflow (`:5678`); `btc_api.py` + `watchdog.py` run as bare Python, not containerised
- **Node.js ≥ 20** — required for frontend build (`vite`, `tsc`) and for the `mex-agent` CLI

## Key Libraries

- **pandas / numpy** — OHLCV ingestion + indicator math
- **requests** — Binance / Bybit / Fear & Greed / funding-rate fetchers
- **FastAPI + Pydantic** — typed HTTP boundary. Pydantic models (e.g. `OpenPositionRequest` with `extra='forbid'`) are the runtime órgano de rechazo for the input layer. See [[conventions.md]] for the four-rung enforcement model.
- **OpenClaw CLI** — outbound Telegram channel from `trading_webhook.py`
- **n8n** — alternative outbound flow on port 5678
- **`pymc`** (skill-installed, `pymc-bayesian-modeling`) — on-demand for Bayesian posteriors at §A.4 checkpoints. **Not invoked for prose-default magnitude updates.**

## Configuration: `config.json`

Primary config read by both scanner and API:

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

## `security.webhook_allow_private_ips` (#127)

SSRF guard for `webhook_url` (and `notifier.channels.webhook.endpoints`). Default `false` — rejects loopback, RFC1918, link-local, multicast, unspecified, reserved. The localhost-n8n setup shown above (`http://localhost:5678/...`) requires `webhook_allow_private_ips: true`, because `localhost` resolves to `127.0.0.1` which is loopback. Even with the flag on, link-local (`169.254.169.254` / AWS EC2 IMDS) is ALWAYS blocked — the flag relaxes local-network trust, not cloud-metadata exposure. POST /config can carry both fields in one request (`{"webhook_url":"http://localhost:5678/...","security":{"webhook_allow_private_ips":true}}`).

## What We Deliberately Do NOT Use

- **No ORM** — direct `sqlite3` + the three-layer access pattern in [[conventions.md]] (pure SQL helpers / business operators / ad-hoc `with transaction()`).
- **No multiplicative risk scalers** on top of `RISK_PER_TRADE=0.01`. Per-symbol volatility lives in `symbol_overrides`, not in a sizing layer.
- **No inbound Telegram bot.** Telegram is outbound only; approval flows through CLI / frontend.
- **No mocks for the holdout dataset.** The only legitimate read entry is `data/holdout_access.py::open_holdout(rel_path, *, evaluation_mode=True)` — see [[../patterns/holdout-access.md]].
