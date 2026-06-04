# Funding-carry execution-realism v0.2 — U1 findings

- run_ts_utc: 2026-06-04T08:04:38.722000+00:00
- settlement_ts_ms: 1780560000000
- **VERDICT: PASS**
- T_FLOOR_REAL: 0.002012032803658178
- T_FLOOR (v3, frozen): 0.0038575872804181457
- floor_ratio real/v3: 0.5215780376173582
- live rate (v0.1 state): R_pooled=0.035980193650793656 CI=[0.009381564583333335, 0.06057524212301587] decay_state=THIN
- n_ok=9 insufficient=[]
- calibration_identity_hash: 1b616f742ed2eec7c00e09a78d2dd796dad79ba3d771175a39cda512d38c0d4d
- version: v0.2

A PASS here is a same-epoch snapshot (rate vivo vs piso real). It is NOT by
itself a go for #4 — deployability has no joint estimator yet (spec §8).
