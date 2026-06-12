"""Block-bootstrap de la diferencia de medias valle − no_valle (determinista).

Unidad de remuestreo = la entrada (una por episodio → "block por episodio"
del pre-registro). Resample con reemplazo de cada grupo por separado,
BOOTSTRAP_ITERS veces, seed fijo. CI95 percentil de la diferencia de medias.
Pre-registro §"Métrica"."""
from __future__ import annotations

import numpy as np

from .constants import BOOTSTRAP_ITERS, SEED


def bootstrap_diff(valle: list[dict], no_valle: list[dict]) -> dict:
    """Devuelve {diff, ci_low, ci_high, n_valle, n_no_valle}. diff = diferencia
    de medias puntual; ci_* = percentiles 2.5/97.5 de la distribución bootstrap."""
    v = np.array([e["pnl_usd"] for e in valle], dtype=float)
    n = np.array([e["pnl_usd"] for e in no_valle], dtype=float)
    diff = float(v.mean() - n.mean()) if len(v) and len(n) else 0.0

    rng = np.random.default_rng(SEED)
    diffs = np.empty(BOOTSTRAP_ITERS, dtype=float)
    nv, nn = len(v), len(n)
    for i in range(BOOTSTRAP_ITERS):
        bv = v[rng.integers(0, nv, nv)].mean() if nv else 0.0
        bn = n[rng.integers(0, nn, nn)].mean() if nn else 0.0
        diffs[i] = bv - bn
    ci_low, ci_high = np.percentile(diffs, [2.5, 97.5])
    return {"diff": diff, "ci_low": float(ci_low), "ci_high": float(ci_high),
            "n_valle": nv, "n_no_valle": nn}
