# Findings — Calibración del gate de régimen

_Período de señales: 2021-01-01 → 2025-04-29._

## Veredicto

**INVERTIDO** — El gate está INVERTIDO: en régimen btc el retorno forward supera a alts (delta=-6.1pp, p_btc_gt_alts=2.236305100385723e-22). Encender el gate causaría daño.

## Resultados por estado (umbrales de producción)

- **alts** n=468 mediana max_fwd_14d=6.4% win15=25%
- **btc** n=23826 mediana max_fwd_14d=12.6% win15=43%
- **mixto** n=34092 mediana max_fwd_14d=12.2% win15=42%

## Grid exploratorio (COTA SUPERIOR — no usar para decisión)

- BREADTH_ALT=0.6 OUTPERF_ALT=0.0: delta=-3.1pp (n_alts=872, n_btc=23826)
- BREADTH_ALT=0.55 OUTPERF_ALT=0.0: delta=-3.2pp (n_alts=905, n_btc=23826)
- BREADTH_ALT=0.65 OUTPERF_ALT=0.0: delta=-3.3pp (n_alts=845, n_btc=23826)
- BREADTH_ALT=0.7 OUTPERF_ALT=0.0: delta=-3.4pp (n_alts=811, n_btc=23826)
- BREADTH_ALT=0.55 OUTPERF_ALT=0.05: delta=-6.1pp (n_alts=468, n_btc=23826)

## Caveats

- Survivorship: panel retiene delistadas pero su cobertura no es total (187 símbolos del ingest 2026-06-05).
- quote_vol derivado = volume × close (≈ quote-vol de Binance).
- BTC.D de fuente externa congelada (ver METODOLOGIA §Procedencia).
- Retorno en USDT incluye beta de BTC.
- Grid-search es EXPLORATORIO (overfitting); la decisión es a umbrales de producción.