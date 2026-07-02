# Backtest de equity-curve de la jugada contrarian — Veredicto: **NO PASA**

Regla: comprar alt viva en BTC-bear (BTC<SMA200) + escalera; M slots equal-weight; costo 2%. Panel anti-survivorship hasta 2025-04-29. Barra pre-comprometida (spec 2026-07-01): terminal>1 + maxDD<50% + le gana a always-in, en ≥2 de 3 M.

| M | ESTRATEGIA terminal | maxDD | CAGR | maxDD 2022 | BENCHMARK (always-in) | ¿pasa? |
|---|---|---|---|---|---|---|
| 5 | 1.02x | **58.5%** | 0.5% | 49.1% | 0.32x | NO (DD) |
| 10 | **0.91x** | 62.6% | −2.4% | 52.1% | 0.44x | NO (terminal + DD) |
| 20 | 1.03x | **56.5%** | 0.8% | 46.5% | 0.63x | NO (DD) |

**VEREDICTO: NO PASA (0/3).**

## Lo que dice

- La media por-trade +4.58% NO es una estrategia. Como **secuencia**, la curva de equity termina **plana** (1.0x en ~4 años = ~0.5% CAGR) con un **drawdown de 56-63%** (49% de él en 2022). Nadie sobrevive un −56% para cobrar 0.5% anual.
- La estrategia SÍ le gana al always-in (1.0x vs 0.3-0.6x) — el timing de bear ayuda — pero **ambos pierden/estancan**. La dirección era correcta; la magnitud no alcanza.
- Halberg lo predijo exacto: *"el runtime no cobra la media, cobra la secuencia, y la secuencia empieza en 2022."*

## Conclusión

La versión FALSABLE de la jugada (regla mecánica concreta) falló la barra pre-comprometida. Cierra el hilo de automatización mecánica con una medición real de equity, no con narrativa. Confirma el veredicto de la junta: no hay edge mecánico automatizable — ni selección, ni régimen, ni exit, ni la regla contrarian como estrategia.

Caveat: n=1 bear (2022 in-sample). Pero el fallo es tan claro (DD>56%, equity plana) que no cambia.
