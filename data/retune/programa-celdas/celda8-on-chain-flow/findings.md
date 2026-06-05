# VEREDICTO: DEGRADADA — necrología forense de la celda 8 (on-chain-flow)

**Coordenada:** E1/D/n_F=2 · **Fecha:** 2026-06-05 · **Pre-registro:** `criterios.md`
(commit `faa81dd`, ANTES del survey).

## La necrología

**Qué murió:** el edge informacional de las señales on-chain PÚBLICAS (exchange
flows, SOPR/MVRV/NUPL, whale tracking) para un consumidor retail de la señal.

**Cuándo:** ventana de degradación **2018–2021**, con punto medio defendible en
**2020–2021**. Borde temprano: fundación simultánea de Glassnode, CryptoQuant y
Whale Alert (2018) sobre el mercado que Santiment abrió (2017) — la señal deja
de ser privada [1][2][3][5]. Borde de saturación: difusión de masa en el ciclo
alcista 2020–2021 (tiers gratuitos, bots públicos de alertas en tiempo real,
~800K usuarios de Glassnode) [2][5].

**Qué la mató:** la comercialización de la propia señal. No un cambio del
mercado subyacente sino la commoditización del observable: cuando la alerta de
ballena llega simultáneamente a todos por bot público, la ventaja del consumidor
es no-positiva por construcción (los rápidos primero — y el operador no es de
los rápidos, ver celdas 5–6).

## Aplicación del criterio pre-registrado (≥2 de 3 líneas — llegaron las 3)

### E1 — Arbitraje/comercialización de la señal (sostenida, fechada)

Timeline documentado: Santiment 2016/ICO jul-2017 [1]; Glassnode 2018, research
pública desde 2020 [2]; CryptoQuant 2018, posicionamiento retail explícito [3];
Whale Alert ~sep-2018, alertas multi-cadena en tiempo real como commodity
público [5]; Nansen 2020 (ya en la capa propietaria) [4].

### E2 — Degradación medida (sostenida, corroborativa)

- Palazzi, Raimundo Júnior & Klotzle (SSRN, 2026-02-08): la predictibilidad de
  BTC es evolutiva/migrante; las variables robustas son de valuación y
  apalancamiento (MVRV como valuación, funding, OI) — no las métricas de
  flujo/holder de difusión masiva [6]. Académico, sin conflicto vendor.
- Bysik & Ślepaczuk (arXiv:2606.00060, 2026-05-19): estrategias naive sign-based
  colapsan al imponer 10 bp/trade de costos — el principio que mata las señales
  intradía mal filtradas, régimen donde viven los flujos on-chain de alta
  frecuencia [7]. (No testea features on-chain directamente — corroborativo vía
  principio de costos, declarado como tal.)
- Mecanismo cualitativo del whale-tracking diluido: front-running de la alerta,
  falsos positivos (transferencias internas/custodia), ocultamiento OTC [10].
  Fuentes de calidad media, usadas solo como corroboración de mecanismo.
- Los materiales que afirman poder predictivo persistente son vendor (Nansen,
  Glassnode) o carecen de modelado de costos — descalificados como E2-a-favor
  bajo la regla pre-registrada de conflictos.

### E3 — Migración del alpha (sostenida)

Lo que sigue vivo exige capacidades fuera del perfil: labeling propietario de
entidades (Nansen >500M wallets etiquetadas, set "Smart Money" derivado de
pipeline PnL propietario [4]; Arkham >300M labels, >150K entity pages, sistema
propietario de síntesis [9]). Es decir: el alpha on-chain superviviente vive en
la capa de etiquetado a nivel entidad — que ya no es la celda accesible, es
exactamente lo excluido del perfil (suscripciones de pago + infra de las celdas
5–6).

## Cláusula de escape: candidato hallado, NO califica (reportado honesto)

Chi, Chu & Hao (arXiv:2411.06327, ~2025): entradas netas de USDT a exchanges
predicen retornos BTC/ETH a 1–6h [8]. Es independiente y reciente — cumple la
FORMA de la cláusula. Pero no hay evidencia de modelado de costos retail netos,
y es forecasting intradía — exactamente el régimen donde [7] muestra colapso a
10 bp. Bajo la regla pre-registrada ("no backtests sin costos"), NO activa el
escape. Se convierte en la hipótesis de reapertura (abajo) — que es como el
verbo D quiere que se procese: hipótesis específica, no "revisitar a ver".

## Qué significa DEGRADADA

La celda murió por difusión, no por imposibilidad física. NO debe releerse como
"pendiente de medir" ni como invitación a data-mining propio (restricción
pre-registrada: esta celda no corre falsificadores — la junta 2026-06-04 la
marcó como jardín de p-hacking). Regla del atlas: sin comparación cardinal con
celdas F.

## Condición de reapertura (hipótesis específica + fuente de data, spec §3-D)

Reabre ÚNICAMENTE con este par concreto (u otro de igual especificidad aprobado
contra el spec):

- **Hipótesis:** los flujos netos de USDT hacia exchanges predicen retornos
  BTC/ETH a horizonte 1–6h con expectativa positiva NETA de costos retail
  (cost-model v3 o sucesor) — replicación de Chi, Chu & Hao [8] fuera de su
  muestra y neta de costos.
- **Fuente de data:** feed de exchange-flows de stablecoins verificable e
  ingestable por el operador (no señal vendor pre-agregada sin metodología).

Si se reabre, entra como estudio F nuevo con namespace propio en el registry —
no como extensión de esta necrología.

## Fuentes

1. Santiment — fundación 2016, ICO/plataforma jul-2017 (CoinCentral / Santiment
   about).
2. Glassnode — fundación 2018; "The Week On-Chain" Week 48, 2020; tier gratuito.
3. CryptoQuant — fundación 2018, posicionamiento retail (Bitget Academy 2026).
4. Nansen — fundación 2020; >500M wallets etiquetadas, "Smart Money".
5. Whale Alert — activo desde ~sep-2018; alertas en tiempo real multi-cadena.
6. Palazzi, Raimundo Júnior & Klotzle — "The Evolving Predictability of Bitcoin
   Returns", SSRN 6199098 (2026-02-08). [Caveat: 403 al fetch directo; hallazgos
   vía indexación de búsqueda, abstract por verificar.]
7. Bysik & Ślepaczuk — "ML-Based Bitcoin Trading Under Transaction Costs",
   arXiv:2606.00060 (2026-05-19).
8. Chi, Chu & Hao — "Return and Volatility Forecasting Using On-Chain Flows",
   arXiv:2411.06327 (~2025).
9. Arkham Intelligence / Blockworks (2025) — labeling a nivel entidad.
10. AInvest / Blofin / Ledger (2025) — dilución del whale-tracking retail
    (corroboración de mecanismo, calidad media declarada).
