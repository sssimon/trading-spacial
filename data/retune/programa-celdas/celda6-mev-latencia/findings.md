# VEREDICTO: EXCLUIDA — teorema de exclusión de la celda 6 (mev-latencia)

**Coordenada:** E1/C/n_F=2 · **Fecha:** 2026-06-05 · **Pre-registro:** `criterios.md`
(commit `faa81dd`, ANTES del survey).

## El teorema

**Desde retail (capital 5 cifras, sin nodo, sin relaciones con builders/relays, sin
equipo), la extracción competitiva de MEV/edge-de-latencia es inaccesible porque
el acceso al insumo dominante es posicional, no comprable.** Las tres P candidatas
pre-registradas quedaron sostenidas con fuentes fechadas; bastaba una.

### P1 — Acceso al orderflow privado (sostenida)

El orderflow privado representa ~54.6% del valor total de los bloques siendo solo
~12% de las transacciones [1]. El acceso es relacional y auto-reforzante: searchers
y usuarios rutean a los builders dominantes porque dominan, y los builders chicos
no consiguen flujo [1]. Flashbots lanzó BuilderNet (nov 2024) explícitamente para
"neutralizar los acuerdos de exclusive orderflow" — confirmación a nivel protocolo
de que la exclusividad es estructural, no una fricción comprable [5]. Pasa el
filtro pre-registrado: no se resuelve con tiempo ni con <10k USD.

### P2 — Subasta de latencia/prioridad (sostenida)

Los searchers competitivos entregan ~90% de su revenue al builder integrado para
asegurar inclusión — la subasta moderna de prioridad es privada y relacional, no
una PGA pública donde un retail pueda pujar [2]. En CEX-DEX arb la ejecución óptima
vive en 0.5–1.5 s post-slot con infraestructura especializada [2]. El costo
marginal de competir por cadena: RPC $200–500/mes (Ethereum) a $1,800–3,800/mes +
colo (Solana) — y el practicante documentado concluye que "el desarrollador solo
está siendo exprimido por firmas capitalizadas" [3][4].

### P3 — Concentración de builders/searchers (sostenida)

Mercado de builders con HHI ~3,892 (umbral "altamente concentrado" del DOJ: 1,800);
top-3 >90% de bloques [6]. En CEX-DEX: $233.8M extraídos por solo 19 searchers,
top-3 ~90% del valor en Q1-2025, integración vertical searcher↔builder
(SCP/beaverbuild, Wintermute/rsync, Kayle/Titan) [2]. Expulsión documentada del
entrante: searchers activos 23 → 14 → 11 entre 2024 y Q1-2025; los pequeños
"cesaron operaciones y se retiraron" [2].

## Cláusula de escape: buscada y no hallada

Se buscó deliberadamente una vía retail vigente (2024–2026) con expectativa
positiva documentada. Lo más cercano: (a) long-tail MEV — el practicante que lo
intentó reporta que las oportunidades "se evaporaban tras unos días" [3];
(b) L2s — técnicamente accesibles pero con el margen "comprimido al punto de que
el profit del arb apenas excede el costo del spam" [4]. Ninguna fuente documenta
el contraejemplo que la cláusula exigía. Matiz honesto: en L2s la barrera muta de
posicional a edge-comprimido-a-cero; el teorema es nítido en L1/CEX-DEX y se
sostiene por agotamiento en L2s (accesible ≠ extraíble).

## Qué significa EXCLUIDA

Conocimiento terminal (spec §3-C), no backlog. Esta celda NO debe releerse como
"pendiente de medir": no hay falsificador que correr porque el insumo del edge
(orderflow/prioridad) no es observable ni adquirible desde la posición del
operador. La regla del atlas aplica: este veredicto no se compara cardinalmente
con ningún PASS/FAIL de celdas F.

## Condición de reapertura (negación de las P)

Reabre ÚNICAMENTE si se documenta, con fuentes independientes y fechadas, al menos
una de:

1. **¬P1:** el acceso al orderflow se democratiza de facto (p.ej. BuilderNet u
   OFAs posteriores demuestran que un searcher sin relación ni escala recibe flujo
   competitivo verificable), o
2. **¬P3:** la concentración de builders/searchers colapsa (HHI < 1,800 sostenido
   y entrada neta de searchers pequeños con permanencia > 1 año).

Un cambio de cadena/venue NO reabre por sí solo (el patrón L2 documentado: la
barrera muta, no desaparece). "Revisar el año que viene" no es condición.

## Fuentes

1. arXiv:2410.12352v3 — "Private Order Flows and Builder Bidding Dynamics: The
   Road to Monopoly in Ethereum's Block Building Market", 2025 (data ene-2023–may-2024).
2. arXiv:2507.13023v1 — "Measuring CEX-DEX Extracted Value and Searcher
   Profitability", jun-2025 (data ago-2023–mar-2025).
3. Pawel Urbanek — "How I've built an unprofitable Crypto MEV Bot in Rust",
   actualizado 2025-02-18. pawelurbanek.com/rust-mev-bot
4. Extropy.io Academy — "An Analysis of Arbitrage Markets Across Ethereum,
   Solana, and L2s", 2025.
5. The Block — "Flashbots unveils BuilderNet", nov-2024; Flashbots Docs
   BuilderNet v1.2, feb-2025.
6. Gate Learn / Ainvest — concentración de builders mar-2025 (HHI ~3,892,
   top-3 >90%).
