"""Selection-world provenance fingerprint.

The unit relative to which a frozen selection claim is frozen: a sha256 over the
COMPLETE world-state under which a deflated-selection metric is computed. Two
artifacts with different fingerprints are NOT comparable. New world-coordinates
are added INSIDE this digest (bump _DIGEST_VERSION) — never as new schema columns,
frozen fields, or trigger clauses (that would be accretion by enumeration).

LEAF MODULE: module-level imports are backtest_costs + deflation only (both leaves).
The A03 constants are lazy-imported from db.trials INSIDE _build to keep this module
acyclic (db.trials / db.hypotheses import this module at their module level).

See docs/superpowers/specs/2026-06-02-cost-model-provenance-design.md.
"""
from __future__ import annotations

import hashlib
import json

from backtest_costs import active_cost_model_id, calibration_identity_hash, load_calibration
import deflation

# Bump when the SET of ingredients changes. Adding a coordinate re-versions every
# fingerprint: previously-conflated worlds become distinguished (honest, auditable).
_DIGEST_VERSION = 1

_cache: "tuple[str, dict] | None" = None


def _clear_cache() -> None:
    """Reset the per-process memo (tests; or if the active calibration is reloaded)."""
    global _cache
    _cache = None


def _build(active_model: str, calibration_hash: str) -> tuple[str, dict]:
    """Assemble + hash the selection world for a given cost-model identity. The
    non-cost-model coordinates (deflation params) come from the CURRENT process —
    correct for stamping the active world and for backfilling the v2 era (the A03
    params and deflation algo did not change across the v2->v3 transition)."""
    from db.trials import A03_DECAY_DATE, A03_N_FLOOR  # lazy: keep this module a leaf
    components = {
        "_digest_version": _DIGEST_VERSION,
        "cost_model": {"active_model": active_model, "calibration_hash": calibration_hash},
        "deflation": {
            "a03_decay_date": A03_DECAY_DATE.isoformat(),
            "a03_n_floor": A03_N_FLOOR,
            "algo_version": deflation.ALGO_VERSION,
        },
    }
    digest = hashlib.sha256(
        json.dumps(components, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return digest, components


def selection_fingerprint() -> tuple[str, dict]:
    """(fingerprint_hash, components) of the ACTIVE selection world. Memoized."""
    global _cache
    if _cache is None:
        active_model, cal_hash = active_cost_model_id()
        _cache = _build(active_model, cal_hash)
    return _cache


def fingerprint_for_v2_sibling() -> str:
    """The selection fingerprint of the v2-era world (cost-model = the frozen
    costs_calibration.v2.json + current deflation params). Used to backfill
    pre-v3 trials/hypotheses. NOT memoized (called once at migration)."""
    cal = load_calibration(path="costs_calibration.v2.json")
    return _build("v2", calibration_identity_hash(cal))[0]
