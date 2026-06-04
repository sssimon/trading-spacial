---
name: setup
description: How to run the backend, frontend, Docker stack, tests, and the Windows automation tasks. Load when starting services, debugging boot-up issues, or onboarding a process to the watchdog.
triggers:
  - "run"
  - "start"
  - "install"
  - "boot"
  - "watchdog"
  - "scheduler"
  - "tests"
  - "docker"
edges:
  - target: context/stack.md
    condition: when an install / version question arises
  - target: context/architecture.md
    condition: when connecting components by port or by data flow
last_updated: 2026-05-26
---

# Setup

## Prerequisites

- **Python 3** with `pandas`, `numpy`, `requests`, `fastapi`, `uvicorn`
- **Node.js ≥ 20** + `npm` (for `frontend/` and `mex-agent`)
- **Docker + Compose** (only for the production frontend + backend stack)
- **Windows** is required for `watchdog.py` and the Task Scheduler scripts; the rest is cross-platform.

## Backend (Python)

```bash
pip install pandas numpy requests fastapi uvicorn

python btc_api.py          # REST API at http://localhost:8000
python btc_scanner.py      # Standalone scanner (runs once, used by API)
python watchdog.py         # Process supervisor (keeps API alive — Windows only)
python btc_report.py       # Generate standalone HTML market report
```

## Frontend (React/TypeScript)

```bash
cd frontend
npm install
npm run dev      # Dev server at http://localhost:5173
npm run build    # Production build (tsc + vite)
npm run preview  # Preview production build
```

## Docker (Production)

```bash
docker compose up --build  # Frontend at :3000, backend at :8000
# Note: watchdog.py runs separately in Python (Windows-only local-dev supervision).
```

## Tests

```bash
python -m pytest tests/ -v
python -m pytest tests/test_scanner.py -v   # Scanner logic only
python -m pytest tests/test_api.py -v       # API endpoints only
```

## Windows Automation

- `scripts/INSTALAR_AUTOSTART.ps1` — registers `watchdog.py` as a Task Scheduler task (`BTCScannerWatchdog`) that starts on boot
- `scripts/REINICIAR_SERVICIOS.ps1` — restart all services
- Batch scripts `INICIAR_API.bat` / `INICIAR_SCANNER.bat` for manual start

### Funding-carry shadow (paper-only, daily)

`python -m tools.funding_carry.shadow` — recomputes the pooled GROSS funding rate (intensive,
mean funding rate over a trailing window, annualized) from live Binance FAPI funding data and
appends to `data/shadow/`. Schedule 1x/day post-funding-settlement via watchdog/cron. SEPARATE
process from `btc_scanner.py`; reads/writes only `data/funding.db` + `data/shadow/`. Paper-only:
no positions, no orders, no holdout, no marks/spot (funding rate only).

Decay logic (REV 5, all anchors frozen in constants.py from the fossil):
- REFUTED: the live gross-rate CI-hi falls below `T_FLOOR` (the rate that just covers v3 cost
  amortized over H_REF_YEARS) for DECAY_KILL_N consecutive NON-OVERLAPPING blocks → the funding
  rate no longer covers deployment cost = edge arbitraged. Do NOT escalate to v0.2/#4.
- THIN: gross rate compressed below `R_FOSSIL_LO` (the fossil's historical band) but still above
  the cost floor — early warning, not a kill.
- ALIVE: rate holds in/above its historical band.
The kill counter advances at most once per non-overlapping W-week block (daily runs within a
block just log). False-REFUTED is controlled by the live bootstrap CI, not the W heuristic.

## Logs & Data

- `logs/signals_log.txt` — human-readable signal entries/exits
- `logs/watchdog.log` — process supervisor log
- `logs/webhook.log` — webhook receiver log
- `data/symbols_status.json` — current symbol state (auto-generated)
- `data/signals_history.csv` — CSV export of all signals
- `data/regime_cache.json` — daily-computed regime composite (see [[architecture.md]] §Regime detector)
- `data/holdout/` — **read-only locked dataset**; touch only via `open_holdout(...)`, see [[../patterns/holdout-access.md]]

## Common Issues

- **`watchdog.py` won't run on Linux/Mac** — it uses Windows-specific commands (`tasklist`, `taskkill`, `wmic`, `netstat`). See [[decisions.md]] §Known Limitations.
- **Webhook process not supervised** — `watchdog.py` watches `btc_api.py` only. The webhook needs its own restart strategy.
- **`webhook_url: http://localhost:...` rejected** — the SSRF guard default is `false`. Set `security.webhook_allow_private_ips: true` in `config.json`. See [[stack.md]] for the full SSRF policy.
