# VEREDICTO: INVIABLE-RETAIL — dictamen R de la celda 7 (vrp-opciones)

**Coordenada:** E1/R/n_F=2 · **Fecha:** 2026-06-05 · **Pre-registro:** `criterios.md`
(commit `faa81dd`, ANTES del survey).

## Aplicación de los criterios pre-registrados

### D1 — Acceso al venue: jurisdicción NO gatilla; la cláusula de margen SÍ

- Jurisdicción: Venezuela NO figura en la lista de jurisdicciones restringidas
  de Deribit (consultada 2026-06-05) [1]. KYC estándar (~2 días). El acceso
  formal está abierto. (Caveat de survey: el listado publicado no confirma el
  caso bancario individual.)
- Venue único: Deribit concentra >60% del mercado de opciones cripto y >90% en
  ETH; Binance+OKX juntos ~7% del OI de opciones BTC [15]. No hay segundo venue
  líquido — el dictamen se decide en las condiciones de Deribit.
- **Mínimo de margen (gatilla):** el portfolio margin — el ÚNICO modo con neteo
  entre piernas, sin el cual un short strangle paga margen pierna por pierna —
  exige **net equity mínimo de 0.5 BTC (o 7.5 ETH)** + experiencia demostrable
  [3]. A precios del survey eso es del orden del capital TOTAL del operador o lo
  excede: cae de lleno en la cláusula pre-registrada "mínimos de cuenta/margen
  exceden capital 5 cifras bajas".

### D2 — Riesgo no acotable: GATILLA

- Bajo standard margin (lo accesible sin el mínimo de PM): margen de opción
  corta ≈ 7.5% del subyacente + mark (MM), IM ~10–15% del subyacente POR PIERNA,
  **sin neteo** entre piernas [4]. El contrato es 1 BTC: una sola pierna corta
  consume margen del orden de la mitad de un capital 5-cifras-bajas; un strangle
  lo consume entero. No queda colchón para la cola.
- La cola gamma es real y está fechada: 5-ago-2024, >$1B liquidado en cripto con
  porción significativa de liquidaciones gamma-related en Deribit/OKX; cuando
  los dealers están short gamma el hedging amplifica el movimiento [13]. La
  iliquidez de las OTM (donde vive el strangle) EMPEORA exactamente en estrés:
  el spread efectivo sube cuando el gamma inventory agregado es negativo [14].
- Conclusión D2 textual del pre-registro: "no hay tamaño viable que sea a la vez
  significativo y sobrevivible" — confirmada. El neteo que haría sobrevivible el
  tamaño significativo está gated detrás del mínimo de PM (D1).

### Capas adicionales documentadas (la pregunta era "estudiable Y ejecutable")

Aunque D1+D2 cierran el dictamen, el survey acotó las x de la rama
REQUIERE-INFRA — relevantes para la condición de reapertura:

- **X1 — DATA:** un falsificador honesto de VRP exige histórico strike-level
  (la literatura lo confirma: Almeida et al. 2024 usó data transaccional
  strike-level diaria con superficies SVI [6]; Alexander & Imeraj 2020, >7M
  precios a 15 min [7]). DVOL gratis NO basta: es forward-looking, no contiene
  los marks con los que se realiza P&L $-denominado de posiciones concretas
  [11]. La data de pago es orden de cientos de USD/mes (Tardis; estimado de
  tercero ~$599/mes tier Pro, no oficial) [9][10].
- **X2 — MOTOR:** las opciones de Deribit son inversas (coin-settled); el
  pricing correcto NO es Black-Scholes estándar (fórmulas propias, Alexander &
  Imeraj 2021/2024 [8]). El repo no tiene motor de pricing/griegas/márgenes y
  las libs open-source cubren pricing puntual, no backtest de cartera con
  márgenes inversos. Construcción: orden de semanas-persona.
- **X3 — EJECUCIÓN:** fee de opciones 0.03% del subyacente maker Y taker, capped
  al 12.5% del premio — proporcionalmente caro justo en las OTM baratas de un
  short strangle [5]; spreads OTM anchos que se ensanchan en estrés [14]. El
  cost-model v3 del repo NO aplica a opciones (hallazgo pre-survey, junta
  2026-06-04).

## Evidencia contraria (obligación pre-registrada)

DVOL histórico es gratuito [11] y existen muestras strike-level dispersas gratis
("primer día de cada mes" vía Tardis [12]) — suficiente para prototipar, no para
falsificar. Si la celda se re-tipara a una hipótesis que solo necesite el nivel/
term-structure de IV (sin P&L $-denominado), parte sería estudiable con data
gratis — pero esa NO es la pregunta tipada, y el pre-registro prohíbe re-tipar
post-survey sin reabrir contra el spec.

## Qué significa INVIABLE-RETAIL

Para ESTE operador, hoy: el único venue líquido pone el modo de margen viable
detrás de un mínimo que es ~todo su capital (D1-margen), y sin ese modo la
estructura mínima no es sobrevivible (D2). No es "pendiente de medir": es la
combinación margen-mínimo × cola-gamma × capital. Regla del atlas: este dictamen
no se compara cardinalmente con veredictos de celdas F.

## Condición de reapertura (conjunción, no disyunción)

Reabre ÚNICAMENTE si se cumplen TODAS:

1. **Capital/venue:** capital del operador alcanza 6 cifras (PM cubrible sin
   comprometer la totalidad) O aparece un venue líquido real (>20% share) con
   neteo accesible a retail sin mínimo equivalente, Y
2. **X1:** se adquiere histórico strike-level (Tardis o equivalente) cubriendo
   ≥2 años incluyendo un evento de cola, Y
3. **X2:** se construye/valida el motor de opciones inversas (pricing + griegas
   + modelo de margen de Deribit).

La sola compra de data o el solo motor NO reabren: sin (1) el estudio sería
in-silico sin ejecutabilidad — exactamente lo que el verbo R existe para
distinguir.

## Fuentes

1. Deribit Support — Restricted Jurisdictions / KYC (consultado 2026-06-05).
2. bitbullnews — Deribit Review 2026 (consultado 2026-06-05).
3. Deribit Support — Portfolio Margin: mínimo 0.5 BTC / 7.5 ETH, IM=MM×1.3
   (consultado 2026-06-05).
4. Deribit Support — Standard Margin: MM short = 0.075 + mark, IM por pierna
   sin neteo (consultado 2026-06-05).
5. Deribit Support — Fees: 0.03% maker y taker, cap 12.5% del premio
   (consultado 2026-06-05).
6. Almeida, Grith & Miftachov — "Risk Premia in the Bitcoin Market",
   arXiv:2410.15195 (2024).
7. Alexander & Imeraj — "The Bitcoin VIX and Its Variance Risk Premium",
   SSRN 3383734 (2020).
8. Alexander & Imeraj — "Inverse and Quanto Inverse Options in a Black-Scholes
   World", arXiv:2107.12041 (2021; Quantitative Finance 2024).
9. Tardis.dev — Billing & Subscriptions (consultado 2026-06-05).
10. HolySheep AI — comparativa pricing tick data (2026-04-30; estimado no
    oficial).
11. CryptoDataDownload — Deribit + DVOL histórico gratis (consultado 2026-06-05).
12. Deribit Insights — partnership Tardis.dev (trial 2022; muestra mensual
    gratuita).
13. BIS Bulletin No 90 — turbulencia ago-2024 y liquidaciones gamma (ago-2024).
14. ScienceDirect — "Aggregate illiquidity and crypto option returns" (2025).
15. CoinGlass 2025 Annual / CoinLaw 2026 — concentración de venues de opciones.
16. GitHub — dgcar/crypto-options-pricing, ArturSepp/StochVolModels (consultado
    2026-06-05).
