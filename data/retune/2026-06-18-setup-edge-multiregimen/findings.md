# Findings — ¿el setup de musikito tiene edge fuera de su régimen?

_Generado 2026-06-18. Período de señales 2020-01-01 → 2025-12-31. Universo: 386 símbolos spot USDT con ≥250 barras diarias (de 430 candidatos tras excluir stables y apalancados; 44 quedaron fuera por historia insuficiente). Panel: 455.068 filas símbolo-día con forward 14d evaluable. Hits regla-mínima: 100.359. Hits regla-conjunta: 12.220._

## Veredicto

**Regla-conjunta vs B1 (control de estado matcheado por fecha; métrica = mediana de `max_fwd_14d`; Mann-Whitney one-sided "setup > baseline"):**

- (a) **Global**: **NO.** Δmediana = −1.2 pp (setup 8.8% vs B1 10.1%), p≈1. La regla-conjunta es PEOR que tomar un par vivo cualquiera con `pos>0.25` el mismo día.
- (b) **En alt-bull** (breadth≥0.6): **NO**, y de forma marcada. Δmediana = −3.3 pp (setup 9.0% vs B1 12.3%), win15 27.5% vs 46.2%. n=240 setups, chico pero consistente con el resto.
- (c) **Fuera de alt-bull** (neutral+bear): **NO** en ambos buckets. Bear Δ=−1.1 pp (p≈1, n=11.462), neutral Δ=−2.8 pp (p≈1).

La regla-conjunta **no tiene edge en ningún régimen** sobre este universo y período: pierde contra su propio control de estado de forma uniforme, y el p-value one-sided es ≈1 en todas las celdas (lo que implica que la cola SIGNIFICATIVA va en la dirección contraria — B1 supera al setup). Esto no es "edge condicional al régimen": es ausencia de edge en todos los regímenes. La sobre-restricción del AND de seis condiciones (rsi<40 + bajo SMA20/50 + consol≤45 + vol_ratio≤0.85 además de pos≤0.25) está seleccionando coins que **siguen cayendo**, no coins en zona de rebote: agarra cuchillos. El único matiz positivo aparece en la **regla-mínima** (solo `vivo AND pos≤0.25`), que sí supera a B1 globalmente (Δ=+0.9 pp, p=1.06e-29) y en **bear** (Δ=+1.6 pp, p=1.62e-104, n=88.670) — pero NO en alt-bull (Δ=−3.0 pp) ni neutral (Δ=−1.5 pp). Es decir: lo poco que funciona es el gate de posición SOLO, y funciona mejor en bear que en alt-bull. Los filtros de momentum/RSI que componen el "setup completo" destruyen ese pequeño edge en vez de afinarlo. La predicción de §1.2 (edge condicional al régimen, fuerte en alt-bull) **se refuta**: el patrón es el opuesto — el único delta positivo está en bear, y el setup conjunto no rescata ningún régimen.

## Regla-conjunta vs B1 por régimen

| bucket | setup n | setup med max_fwd_14d | setup win15 | B1 n | B1 med | B1 win15 | Δ mediana | p (one-sided) |
|---|---|---|---|---|---|---|---|---|
| Global | 12.220 | 8.8% | 31.3% | 21.216 | 10.1% | 36.2% | −1.2 pp | ≈1 |
| alt-bull | 240 | 9.0% | 27.5% | 720 | 12.3% | 46.2% | −3.3 pp | ≈1 |
| neutral | 518 | 7.0% | 25.9% | 1.535 | 9.8% | 36.1% | −2.8 pp | ≈1 |
| bear | 11.462 | 8.9% | 31.6% | 18.961 | 10.0% | 35.9% | −1.1 pp | ≈1 |

(p one-sided ≈1 ⇒ el setup NO supera a B1; el efecto significativo va en sentido contrario.)

## Regla-mínima vs B1 (contraste: ¿el gate de posición solo ya basta?)

| bucket | setup n | setup med max_fwd_14d | setup win15 | B1 n | B1 med | B1 win15 | Δ mediana | p (one-sided) |
|---|---|---|---|---|---|---|---|---|
| Global | 100.359 | 11.3% | 39.2% | 91.272 | 10.4% | 37.4% | **+0.9 pp** | **1.06e-29** |
| alt-bull | 5.382 | 10.1% | 36.5% | 15.654 | 13.1% | 45.8% | −3.0 pp | ≈1 |
| neutral | 6.307 | 8.7% | 30.8% | 12.815 | 10.1% | 36.3% | −1.5 pp | ≈1 |
| bear | 88.670 | 11.6% | 40.0% | 62.803 | 10.0% | 35.5% | **+1.6 pp** | **1.62e-104** |

El único edge real y significativo del estudio: `vivo AND pos≤0.25` en **bear** (Δ=+1.6 pp con n grande). Pero es pequeño en magnitud, vive solo en bear, y desaparece al añadirle los filtros del setup conjunto. Nota: en alt-bull el gate de posición es contraproducente (−3.0 pp) — comprar lo más golpeado cuando el mercado amplio sube es peor que comprar cualquier par vivo.

## Para B2 (universo vivo, continuidad con 2019)

B2 global (cualquier par vivo en `t`): mediana max_fwd_14d 11.9%, win15 42.0%, n=286.719. B2 en alt-bull sube a 15.5% / 51.1%. Ni la regla-mínima ni la conjunta superan a B2 globalmente — el universo vivo entero rinde más que cualquiera de las dos reglas. Esto refuerza que el "setup" no está seleccionando ganadores; está seleccionando un subconjunto que rinde por debajo del promedio del mercado vivo.

## Caveats (honestos)

- **SESGO DE SUPERVIVENCIA (crítico):** `exchangeInfo` solo lista símbolos que cotizan HOY. Los delistados — que típicamente colapsaron — no aparecen. Por eso los niveles ABSOLUTOS de retorno están inflados para setup Y baseline por igual. El **delta setup−baseline** sigue siendo informativo (ambos sufren el mismo sesgo), y el delta dice que el setup conjunto NO tiene edge. Pero las medianas absolutas (8–16%) son optimistas; en un universo point-in-time con delistados serían menores. Específicamente, "comprar coins muy golpeadas" (`pos≤0.25`) es justo el bucket donde el sesgo de supervivencia más infla: los que NO se recuperaron se delistaron y no están en la muestra. El pequeño edge de +1.6 pp en bear de la regla-mínima podría desvanecerse o invertirse con datos point-in-time. Tratarlo como techo, no como piso.
- **Retorno en USDT incluye el movimiento de BTC** (beta del mercado): un `max_fwd_14d` de +10% en bear puede ser en parte rebote de BTC, no alfa idiosincrático de la coin.
- **`rule_return` conservador:** si en el mismo día se tocan TP (+20%) y SL (−12%), se asume SL primero. Esto deprime `rule_return` frente a `max_fwd_14d` (que solo mira el máximo). Las medianas de `rule_return` rondan −4% a −12% en casi todas las celdas: la escalera TP/SL del canal, aplicada mecánicamente, pierde plata en este universo — el primer target de +15–20% rara vez se alcanza antes del −12%.
- **B1 matcheado por fecha**, ratio usado {mínima: 0.91, conjunta: 1.74} sobre target hasta 1:3 (limitado por el tamaño del pool de candidatos `pos>0.25` algunos días), muestreo determinista (semilla = hash SHA-256 del string de fecha % tamaño-del-pool). Misma exposición de régimen que el setup por construcción.
