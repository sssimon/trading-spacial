"""Ensemble de portafolios paper de la cura random-curada. Avanza un día a la vez
(escalera viva acumulada + kill_switch_v2 pico-rodante-180d + picks random reproducibles).
PAPER: sizing equal-weight cap/M, NO toca RISK_PER_TRADE (#4). Puro: sin red, sin DB."""
from __future__ import annotations

import hashlib
import statistics

from scanner.baseline.ladder import ladder_return, HORIZON
# NB: `strategy.kill_switch_v2` se importa LAZY dentro de _tier() para respetar las
# reglas de frontera scanner/ (mismo motivo que los lazy imports en scanner/runtime.py).

N_SEEDS = 30
M = 20
PEAK_WIN = 180
BASELINE_AGGRESSIVENESS = 100
# cfg mínimo para kill_switch_v2: usa los rangos DD por defecto + aggressiveness fija
_KS_CFG = {"kill_switch": {"v2": {"aggressiveness": BASELINE_AGGRESSIVENESS}}}
SF = {"NORMAL": 1.0, "WARNED": 1.0, "REDUCED": 0.5, "FROZEN": 0.0}


def seed_pick(universe: list[str], date: str, seed: int, k: int) -> list[str]:
    """k símbolos reproducibles por (date, seed): rotación determinista del universo."""
    if not universe:
        return []
    uni = sorted(universe)
    off = int(hashlib.sha256(f"{date}|{seed}".encode()).hexdigest(), 16) % len(uni)
    rot = uni[off:] + uni[:off]
    return rot[:k]


class PaperPortfolio:
    """Un portafolio paper (una semilla). Estado serializable vía to_dict/from_dict."""

    def __init__(self) -> None:
        self.cap: float = 1.0
        self.eq: list[float] = []
        # cada posición: {"symbol","entry","notional","hi_max","lo_min","bars_left"}
        self.open_pos: list[dict] = []

    def _tier(self) -> str:
        from strategy.kill_switch_v2 import evaluate_portfolio_tier  # lazy: frontera scanner/
        window = self.eq[-(PEAK_WIN - 1):] + [self.cap]
        peak = max(window) if window else self.cap
        dd = -(1.0 - self.cap / peak) if peak > 0 else 0.0  # negativo en drawdown
        return evaluate_portfolio_tier(dd, 0, _KS_CFG)["tier"]

    def advance_day(self, date: str, bars: dict[str, dict],
                    universe: list[str], seed: int) -> None:
        # 1) marcar posiciones abiertas con la barra de hoy + realizar las que maduran
        still = []
        for pp in self.open_pos:
            bar = bars.get(pp["symbol"])
            if bar is not None:
                pp["hi_max"] = max(pp["hi_max"], bar["high"])
                pp["lo_min"] = min(pp["lo_min"], bar["low"])
                pp["bars_left"] -= 1
                if pp["bars_left"] <= 0:
                    r = ladder_return(pp["entry"], pp["hi_max"], pp["lo_min"], bar["close"])
                    self.cap += pp["notional"] * (r if r is not None else 0.0)
                    continue
            still.append(pp)
        self.open_pos = still
        # 2) tier del kill-switch (pico rodante)
        sf = SF[self._tier()]
        # 3) abrir picks random si hay slots libres y no está FROZEN
        free = M - len(self.open_pos)
        if free > 0 and sf > 0.0:
            held = {pp["symbol"] for pp in self.open_pos}
            alive = [s for s in universe if s in bars and s not in held]
            for sym in seed_pick(alive, date, seed, free):
                bar = bars[sym]
                self.open_pos.append({
                    "symbol": sym, "entry": bar["open"],
                    "notional": (self.cap / M) * sf,
                    "hi_max": bar["high"], "lo_min": bar["low"], "bars_left": HORIZON,
                })
        self.eq.append(self.cap)
