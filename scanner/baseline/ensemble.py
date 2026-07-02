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

    def to_dict(self) -> dict:
        return {"cap": self.cap, "eq": self.eq, "open_pos": self.open_pos}

    @classmethod
    def from_dict(cls, d: dict) -> "PaperPortfolio":
        p = cls()
        p.cap = d["cap"]
        p.eq = list(d["eq"])
        p.open_pos = [dict(x) for x in d["open_pos"]]
        return p


def _percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


class BaselineEnsemble:
    """N portafolios paper independientes; emite la DISTRIBUCIÓN, no un camino."""

    def __init__(self, n_seeds: int = N_SEEDS) -> None:
        self.seeds = list(range(n_seeds))
        self.portfolios = [PaperPortfolio() for _ in self.seeds]
        self.last_date: str | None = None

    def advance_day(self, date: str, bars: dict[str, dict], universe: list[str]) -> None:
        if self.last_date is not None and date <= self.last_date:
            return  # idempotente / monotónico por fecha
        for seed, p in zip(self.seeds, self.portfolios):
            p.advance_day(date, bars, universe, seed)
        self.last_date = date

    def snapshot(self) -> dict:
        caps = sorted(p.cap for p in self.portfolios)
        tiers = sorted(p._tier() for p in self.portfolios)
        return {
            "mediana": statistics.median(caps) if caps else 1.0,
            "banda_p10": _percentile(caps, 0.10),
            "banda_p90": _percentile(caps, 0.90),
            "n_seeds": len(self.portfolios),
            "tier_mediana": tiers[len(tiers) // 2] if tiers else "NORMAL",
            "last_date": self.last_date,
        }

    def to_dict(self) -> dict:
        return {"seeds": self.seeds, "last_date": self.last_date,
                "portfolios": [p.to_dict() for p in self.portfolios]}

    @classmethod
    def from_dict(cls, d: dict) -> "BaselineEnsemble":
        e = cls(n_seeds=len(d["seeds"]))
        e.seeds = list(d["seeds"])
        e.last_date = d["last_date"]
        e.portfolios = [PaperPortfolio.from_dict(x) for x in d["portfolios"]]
        return e
