# data/holdout/ — locked validation snapshot

This directory is the **intact holdout** for strategy validation (epic #246, ticket #247).
It is **read-only** at the filesystem level and gated by:

- `data/holdout_access.py` — `open_holdout(rel_path, *, evaluation_mode=True)` wrapper
- `tests/test_holdout_isolation.py` — AST scanner (whitelist-based)

**Do not read files in this directory directly from scanner / auto_tune / backtest tuning code.**
The AST scanner will fail CI if you do.

For full context (corte, fuentes cubiertas, caveats, justificación), see:

- `docs/superpowers/specs/es/2026-04-30-a1-holdout-dataset-provenance.md`
- `MANIFEST.json` in this directory.
