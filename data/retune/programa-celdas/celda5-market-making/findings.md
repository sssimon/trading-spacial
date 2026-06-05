# VEREDICTO: INVIABLE-RETAIL — dictamen R de la celda 5 (market-making)

**Coordenada:** E1/R/n_F=2 · **Fecha:** 2026-06-05 · **Pre-registro:** `criterios.md`
(commit `faa81dd`, ANTES del survey).

## Aplicación de los criterios pre-registrados

### D1 — Latencia/cola: NO gatilla limpio (hallazgo honesto)

La cláusula puente de D1 exigía "inalcanzable Y no comprable por <100 USD/mes".
La latencia cruda a Binance (AWS Tokio) SÍ es comprable: VPS co-regional a
~$4–25/mes alcanza 0.6–1 ms [1][10] — muy por debajo del umbral pre-registrado.
Lo que el VPS NO compra es la posición de cola frente a MMs co-located/
cross-connected (microsegundos, producto institucional separado [2]). D1 por sí
solo no sostiene el descarte; el bloqueo de latencia se desplaza al eje de cola,
que queda absorbido por D3 (direccional) y dominado por D2.

### D2 — Tier de fees: GATILLA (fuente primaria)

- Futuros USDT-M tier base: 0.0200% maker por lado [3]. Un ciclo round-trip
  maker-maker paga 0.04% en fees contra spreads top-of-book de ~1–2 bps en los
  pares líquidos: la captura pasiva de spread es neto-negativa en tier base
  ANTES de selección adversa.
- El maker fee cero aparece en VIP 9 (volumen mensual del orden de miles de
  millones) [3]. Los programas formales de MM/LP de Binance exigen pisos de
  volumen mensual de 8 a 9 cifras [11].
- **El bloqueo dominante es un piso de VOLUMEN, no una pieza comprable:** con
  capital 5 cifras está fuera del alcance por 2–4 órdenes de magnitud. Por eso
  el dictamen NO es REQUIERE-INFRA: no existe una x adquirible que lo resuelva
  (la rama pre-registrada exigía bloqueo "adquirible").

### D3 — Selección adversa: soporte direccional, sin el cardinal exigido

La teoría (VPIN [7], toxicidad en cripto [4]) y la validación empírica en estrés
(maker pierde donde el taker no, flash crash, arXiv 2026 [5]; asimetría de
velocidad como mecanismo [6]) apoyan que el maker lento es el lado que absorbe
el flujo tóxico. PERO el cardinal específico que D3 exigía (costo esperado de
selección adversa > spread capturable en régimen NORMAL, pares líquidos) no
quedó probado numéricamente en las fuentes. Se registra como soporte, no como
gatillo. El veredicto descansa en D2.

## Evidencia contraria (reportada por obligación pre-registrada)

El caso documentado más fuerte de MM retail rentable (programa comunitario de
Hummingbot) es de **2019** y está contaminado por incentivos de token del
programa $ONE, con retención como proxy de PnL [8]. Las reviews 2026 reportan
rentabilidad mixta tras curva de aprendizaje [9]. Nada de esto contradice D2:
ninguna fuente documenta MM pasivo desnudo neto-positivo en pares líquidos a
tier base de fees.

## Qué significa INVIABLE-RETAIL

Para ESTE operador (capital 5 cifras, retail, sin programa MM), el market-making
pasivo en exchanges cripto líquidos tiene economía negativa por construcción de
fees. No es "pendiente de medir" — la aritmética del tier base lo cierra. La
regla del atlas aplica: este dictamen no se compara cardinalmente con veredictos
de celdas F.

## Condición de reapertura

Reabre ÚNICAMENTE si cambia la estructura que gatilla D2:

1. Un exchange líquido accesible introduce **maker rebate (o fee cero) en tier
   base/retail** verificable en su fee schedule oficial, o
2. El capital/volumen del operador alcanza el piso de elegibilidad de un
   programa MM formal (cambio de perfil, no de mercado).

Un VPS más rápido o un software mejor NO reabren (no atacan D2).

## Fuentes

1. MamboServer / QuantVPS / Valebyte — VPS de baja latencia para Binance
   (consultado 2026-06-05).
2. AWS Industries Blog — "Crypto market-making latency and EC2 shared placement
   groups" (consultado 2026-06-05).
3. Binance fee schedule oficial + Bitget Academy "Binance Fees 2026" +
   TradersUnion futures-fees (consultado 2026-06-05).
4. Tiniç & Sensoy — "Adverse Selection in Cryptocurrency Markets" (consultado
   2026-06-05).
5. arXiv:2602.00776 — "Explainable Patterns in Cryptocurrency Microstructure"
   (sometido 2026-01-31).
6. Multicoin Capital — "Adverse Selection Rules Everything Around Me"
   (2026-02-17).
7. Easley, López de Prado, O'Hara — "Flow Toxicity and Liquidity in a High
   Frequency World" (VPIN, fundacional).
8. Hummingbot — "Does Community-Based Market Making Work?" (2019-09-09).
9. Finestel — "Hummingbot Review 2026" (consultado 2026-06-05).
10. bacloud — "Best VPS Servers for Running a Crypto Trading Bot in 2026"
    (consultado 2026-06-05).
11. Binance Futures MM Program FAQ + Binance.US MM Program + Spot LP Program
    (consultado 2026-06-05).
