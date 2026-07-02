# Filtro momentum/breakout — Veredicto: **MUERTO (beta de bull, no edge)**

El motor de falsación `filter_search.py` (train 2021-23 / validate 2024→2025-04, sin look-ahead,
panel anti-survivorship `program_ohlcv.db`) descubrió que **el edge en la escalera vive en momentum/
breakout** (`vol_ratio` alto + `rsi14` alto + `pos_in_30d_range` alto), NO en el valle. 11 filtros
single-feature sobreviven todos en la misma dirección. El filtro apilado `vol_ratio>2 and rsi14>55`
dio **train +2.6% / validate +7.3% por trade** — y coincide con lo que musikito de verdad hacía.

Se sometió a 6 verificadores adversariales (workflow `momentum-filter-kill`, opus, 7 agentes). Veredicto:

| Test | Resultado | Número clave |
|---|---|---|
| **Secuencia** (equity curve) | **MUERTO** | M=10 → **0.31x, 79% DD** en validate. Peor que el contrarian. |
| **Alfa-vs-beta** (day-matched) | **MUERTO** | Alts que el filtro RECHAZA rinden **+7.04%** los mismos días (vs +7.32%). Pareado: momentum PIERDE (mediana −2.22pp, p<1e-4). |
| **Regímen** | solo-bull | bull +7.7% (n=5212) vs bear **−1.95%** (n=218). 96% de disparos en bull. |
| **Walk-forward** | debilitado | 2023 +3.6pp, 2024 +7.0pp vs baseline; pero 2022 **−5.4pp** (dañino), 2021 +0.2pp. |
| **Costo** | debilitado | breakeven ~9.3%, pero es artefacto bull (edge-vs-baseline invariante al costo). |
| **Cola gorda** | sobrevive | mediana +10.1% > media +7.3%, %win 60% — pero eso es lo que predice la beta (en pump days todo gana). |

## Por qué murió

1. **No compone.** Las candidatas momentum disparan en **clusters de pump correlacionados**. Un libro
   real de 5-20 slots compra el cluster cerca del tope y come el dump correlacionado → 0.31x / 79% DD.
   El +7% del pool solo se recupera con ~1000 slots (diversificación, no edge). Robusto a 5 semillas.
2. **No es selección, es detección de día.** El control day-matched es la prueba hermética: en los 433
   días que dispara, las alts que RECHAZA rinden igual (+7.04% vs +7.32%). El filtro no escoge ganadoras
   — flaggea días de pump, y en esos días **la escalera le da +7% a cualquier alt**. El +9.7pp sobre el
   baseline global es beta de timing (96% bull), no alfa.
3. **Solo-bull.** En bear no dispara y hace daño. No protege del próximo 2022.

## Conclusión

Cierra la caza mecánica de edge con la evidencia más fuerte posible (OOS + adversarial + day-matched +
anti-survivorship). **No existe edge de selección de entrada** — ni valle, ni régimen, ni momentum. Lo
único con valor es **la escalera (exit) aplicada a las alts correctas en los días correctos**, y saber
cuáles son "los días correctos" es la discreción/timing del operador, que ninguna regla mecánica captura.
Coincide con el ground-truth de musikito: el edge era el exit + el timing de ciclo, nunca la entrada.

**Único camino de medición que queda:** forward-log del operador (papá) cuando opere Valles. No hay más
que exprimirle al backtest mecánico.
