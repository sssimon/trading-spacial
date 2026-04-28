"""Config domain — load/save/validate config.json + endpoints.

Extracted from btc_api.py in PR2 of the api+db refactor (2026-04-27).

Config is layered (lowest → highest precedence, later wins):
  1. Hardcoded safety net (this module)
  2. config.defaults.json (committed: symbol_overrides + tuned defaults)
  3. config.secrets.json (gitignored: telegram/webhook creds)
  4. config.json (legacy single-file; backward-compat for Simon prod)
  5. TRADING_* environment variables
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import verify_api_key

log = logging.getLogger("api.config")

_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(_SCRIPT_DIR, "config.json")
DEFAULTS_FILE = os.path.join(_SCRIPT_DIR, "config.defaults.json")
SECRETS_FILE = os.path.join(_SCRIPT_DIR, "config.secrets.json")
SCAN_INTERVAL_SEC = 300

_SECRET_KEYS = {"webhook_secret", "telegram_bot_token", "api_key"}

router = APIRouter(tags=["config"])


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursive merge: dicts merge, other types replace. Used to layer config files."""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _load_json_file(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_config() -> dict:
    """Load configuration as a layered merge.

    Precedence (lowest → highest, later wins):
      1. Hardcoded safety net (this function)
      2. config.defaults.json (committed — symbol_overrides + tuned defaults)
      3. config.secrets.json (gitignored — telegram/webhook creds)
      4. config.json (legacy single-file; backward-compat for Simon prod)
      5. TRADING_* environment variables
    """
    # 1. Hardcoded safety net — used if defaults file is missing. Keeps the system
    # functional but WITHOUT symbol_overrides, which means backtests and the scanner
    # fall back to generic ATR multipliers. This is the state the repo was in when
    # config.json had always been gitignored — see drawer "config-json-gitignored-elephant".
    hardcoded = {
        "webhook_url":        "",
        "webhook_secret":     "",
        "notify_setup_only":  False,
        "scan_interval_sec":  SCAN_INTERVAL_SEC,
        "num_symbols":        10,
        "telegram_chat_id":   "",
        "telegram_bot_token": "",
        "signal_filters": {
            "min_score":            0,
            "require_macro_ok":     False,
            "notify_setup":         False,
            "dedup_window_minutes": 30,
        },
        "kill_switch": {
            "enabled":                   True,
            "min_trades_for_eval":       20,
            "alert_win_rate_threshold":  0.15,
            "reduce_pnl_window_days":    30,
            "reduce_size_factor":        0.5,
            "pause_months_consecutive":  3,
            "auto_recovery_enabled":     True,
        },
    }

    cfg = hardcoded
    if os.path.exists(DEFAULTS_FILE):
        cfg = _deep_merge(cfg, _load_json_file(DEFAULTS_FILE))
    else:
        log.warning("config.defaults.json missing — backtests will run without symbol_overrides")
    if os.path.exists(SECRETS_FILE):
        cfg = _deep_merge(cfg, _load_json_file(SECRETS_FILE))
    if os.path.exists(CONFIG_FILE):
        cfg = _deep_merge(cfg, _load_json_file(CONFIG_FILE))

    _env_map = {
        "TRADING_WEBHOOK_URL":       "webhook_url",
        "TRADING_TELEGRAM_CHAT_ID":  "telegram_chat_id",
        "TRADING_TELEGRAM_BOT_TOKEN": "telegram_bot_token",
        "TRADING_WEBHOOK_SECRET":    "webhook_secret",
        "TRADING_API_KEY":           "api_key",
        "TRADING_PROXY":             "proxy",
    }
    _env_map_int = {
        "TRADING_SCAN_INTERVAL": "scan_interval_sec",
        "TRADING_NUM_SYMBOLS":   "num_symbols",
    }

    for env_key, cfg_key in _env_map.items():
        val = os.environ.get(env_key)
        if val is not None:
            cfg[cfg_key] = val

    for env_key, cfg_key in _env_map_int.items():
        val = os.environ.get(env_key)
        if val is not None:
            try:
                cfg[cfg_key] = int(val)
            except ValueError:
                pass

    return cfg


def _strip_secrets(cfg: dict) -> dict:
    """Remove sensitive fields from a config dict before returning to clients."""
    return {k: v for k, v in cfg.items() if k not in _SECRET_KEYS}


# ─────────────────────────────────────────────────────────────────────────────
#  CONFIG VALIDATION (Pydantic)
# ─────────────────────────────────────────────────────────────────────────────

class SignalFiltersUpdate(BaseModel):
    min_score: Optional[int] = Field(None, ge=0, le=10)
    require_macro_ok: Optional[bool] = None
    notify_setup: Optional[bool] = None


class ConfigUpdate(BaseModel):
    webhook_url: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    telegram_bot_token: Optional[str] = None
    scan_interval_sec: Optional[int] = Field(None, ge=60, le=3600)
    num_symbols: Optional[int] = Field(None, ge=1, le=50)
    proxy: Optional[str] = None
    signal_filters: Optional[SignalFiltersUpdate] = None
    api_key: Optional[str] = None
    auto_approve_tune: Optional[bool] = None


def save_config(updates: dict) -> dict:
    """Actualiza config.json con los campos recibidos y retorna la config resultante."""
    cfg = load_config()
    # signal_filters se fusiona, no reemplaza
    if "signal_filters" in updates:
        sf = cfg.get("signal_filters", {}).copy()
        sf.update(updates.pop("signal_filters"))
        cfg["signal_filters"] = sf
    # kill_switch se fusiona, no reemplaza (mismo patrón que signal_filters)
    if "kill_switch" in updates:
        ks = cfg.get("kill_switch", {}).copy()
        ks.update(updates.pop("kill_switch"))
        cfg["kill_switch"] = ks
    cfg.update(updates)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    log.info("config.json actualizado.")
    return cfg


@router.get("/config", summary="Leer configuracion actual",
            dependencies=[Depends(verify_api_key)])
def get_config():
    cfg = load_config()
    result = _strip_secrets(cfg)
    result["auto_approve_tune"] = cfg.get("auto_approve_tune", True)
    return result


@router.post("/config", summary="Actualizar configuracion", dependencies=[Depends(verify_api_key)])
def update_config(body: ConfigUpdate):
    # Convert Pydantic model to dict, excluding unset fields
    updates = body.model_dump(exclude_unset=True)
    # Convert nested Pydantic model to dict
    if "signal_filters" in updates and updates["signal_filters"] is not None:
        updates["signal_filters"] = {
            k: v for k, v in updates["signal_filters"].items() if v is not None
        }
    try:
        updated = save_config(updates)
        return {"ok": True, "config": _strip_secrets(updated)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
