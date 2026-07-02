"""Persistencia del ensemble para sobrevivir reinicios (replay determinista).
Escritura atómica (tmp + rename). El generated_at persistido alimenta la frescura."""
from __future__ import annotations

import json
import os

from scanner.baseline.ensemble import BaselineEnsemble

_DEFAULT_PATH = os.path.join("data", "baseline_state.json")


def persist(ensemble: BaselineEnsemble, generated_at: str, path: str = _DEFAULT_PATH) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    payload = {"generated_at": generated_at, "ensemble": ensemble.to_dict()}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    os.replace(tmp, path)  # atómico


def load(path: str = _DEFAULT_PATH) -> tuple[BaselineEnsemble | None, str | None]:
    if not os.path.exists(path):
        return None, None
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    return BaselineEnsemble.from_dict(payload["ensemble"]), payload.get("generated_at")
