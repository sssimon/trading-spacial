# Documentation Index

> Navigation surface over the research artifacts in this repo. Start here.

## Start here

If you're new to the project and want to understand what it's actually about:
1. Read [`/METHODOLOGY.md`](../METHODOLOGY.md) — the moat, in 10 sections
2. Read [`/CLAUDE.md`](../CLAUDE.md) — current-state truth (architecture, configs, known limitations)
3. Skim the canonical specs below by topic

## Canonical specs by topic

> Specs are written in Spanish (language of operator thinking) — see "Convention notes" below. The dates in filenames are the pre-registration commit dates.

### Project framing + methodology
- [`specs/es/2026-04-30-a1-holdout-dataset-provenance.md`](superpowers/specs/es/2026-04-30-a1-holdout-dataset-provenance.md) — holdout dataset provenance
- [`specs/es/2026-05-01-operational-model-manual-gating.md`](superpowers/specs/es/2026-05-01-operational-model-manual-gating.md) — why this isn't auto-trading
- [`specs/es/2026-05-03-asunciones-tecnicas-pre-holdout.md`](superpowers/specs/es/2026-05-03-asunciones-tecnicas-pre-holdout.md) — assumptions audit
- [`specs/es/2026-05-11-strategy-structural-audit.md`](superpowers/specs/es/2026-05-11-strategy-structural-audit.md) — strategy components audit

### Inflection points (where prior numbers were re-baselined)
- [`specs/es/2026-05-11-a4-hallazgo-inflexion-metodologica.md`](superpowers/specs/es/2026-05-11-a4-hallazgo-inflexion-metodologica.md) — the inflection itself
- [`research/2026-05-02-structural-fix-parameter-study.md`](superpowers/research/2026-05-02-structural-fix-parameter-study.md) — K-cap parameter study
- [`specs/es/2026-04-18-documento-completo-sistema-trading.md`](superpowers/specs/es/2026-04-18-documento-completo-sistema-trading.md) — full system doc (⚠️ numbers are pre-#223, see METHODOLOGY § Structural fixes)

### Strategy research
- [`specs/es/2026-04-15-analisis-estrategia-spot-v6.md`](superpowers/specs/es/2026-04-15-analisis-estrategia-spot-v6.md) — initial V6 analysis
- [`specs/es/2026-04-16-detector-regimen-multi-signal.md`](superpowers/specs/es/2026-04-16-detector-regimen-multi-signal.md) — regime detector design
- [`specs/es/2026-04-17-formula-ganadora-resultados-finales.md`](superpowers/specs/es/2026-04-17-formula-ganadora-resultados-finales.md) — winning formula (⚠️ pre-#223)
- [`specs/es/2026-04-18-vol-normalized-resultados.md`](superpowers/specs/es/2026-04-18-vol-normalized-resultados.md) — vol normalization
- [`specs/es/2026-05-13-epic-regime-allocation-strategy-pivot.md`](superpowers/specs/es/2026-05-13-epic-regime-allocation-strategy-pivot.md) — closed 2026-05-15 with PHASE_3_INSUFFICIENT_DATA verdict

### Kill-switch + risk
- [`specs/es/2026-04-21-kill-switch-design.md`](superpowers/specs/es/2026-04-21-kill-switch-design.md) — kill-switch v1
- [`specs/es/2026-04-23-kill-switch-v2-design.md`](superpowers/specs/es/2026-04-23-kill-switch-v2-design.md) — kill-switch v2

### Production + multi-tenant
- [`specs/es/2026-04-29-trading-sdar-dev-deploy-design.md`](superpowers/specs/es/2026-04-29-trading-sdar-dev-deploy-design.md) — deploy design
- [`specs/es/2026-05-16-multi-tenant-threat-model.md`](superpowers/specs/es/2026-05-16-multi-tenant-threat-model.md) — multi-tenant threat model
- [`specs/es/2026-05-21-telegram-per-user-config-pre-reg.md`](superpowers/specs/es/2026-05-21-telegram-per-user-config-pre-reg.md) — per-user Telegram

### LLM copilot
- [`specs/es/2026-05-19-trading-copilot-production-grade-pre-reg.md`](superpowers/specs/es/2026-05-19-trading-copilot-production-grade-pre-reg.md) — copilot prod-grade
- [`specs/es/2026-05-20-multi-provider-copilot-pre-reg.md`](superpowers/specs/es/2026-05-20-multi-provider-copilot-pre-reg.md) — multi-provider (DeepSeek)

## Active implementation plans

Plans for current work are in `superpowers/plans/`. Recent (2026-05-*):
- `2026-05-21-telegram-per-user-config.md` (shipped #421)
- `2026-05-21-telegram-multitenant-phase-a.md` (shipped epic #253)
- `2026-05-22-readme-methodology-public-translation.md` (this plan)

Archived plans: `superpowers/plans/archive/`

## Other documentation

- [`CHANGELOG-2026-04-17-18.md`](CHANGELOG-2026-04-17-18.md) — historical changelog snippet (April inflection week)
- [`atomic-deploy-migration.md`](atomic-deploy-migration.md) — deploy migration notes
- [`strategy-backtest-report.md`](strategy-backtest-report.md) — historical backtest report (⚠️ check dates against #223)
- `rollouts/` — operational rollout playbooks

## Convention notes

**Why are most specs in Spanish (`specs/es/`)?** The operator works primarily in Spanish; pre-regs are written in the language of thinking. Public-facing docs (`README.md`, `METHODOLOGY.md`, this file) are in English so the moat is legible to a broader audience. The double-surface is intentional: research depth in the native language, public framing in English.

**Pre-#223 caveat marker**: docs that contain backtest numbers from before the PR #223 sign-error fix are marked ⚠️ above. Read them for methodology and reasoning; do not cite their numbers.
