# Celda 6 — mev-latencia · Criterios de descarte pre-registrados

**Verbo:** C (cerrada estructural) — pre-asignado en el catálogo congelado 2026-06-04.
**Pre-registro:** este documento se commitea ANTES de ejecutar el survey. Los criterios
de abajo no se editan después de iniciada la búsqueda de fuentes.
**Fecha de pre-registro:** 2026-06-05.

## Perfil del operador

El mismo de la celda 5: capital 5 cifras, retail, Windows local residencial, sin
nodo propio, sin relaciones con builders/relays, sin equipo de ingeniería.

## Pregunta del teorema

¿Existe una capacidad estructural P que el operador categóricamente no posee ni
puede adquirir desde su posición, tal que sin P la extracción de MEV/edge-de-latencia
es inaccesible? El artefacto es un **teorema de exclusión**: "desde retail, este
edge es inaccesible porque P", con P estructural y fuentes.

## Qué cuenta como P válida (declarado ANTES del survey)

P debe ser **estructural** (de posición, no de esfuerzo): cada candidata debe pasar
el filtro "no se resuelve con tiempo ni con <10k USD". Candidatas a evaluar:

- **P1 — Acceso al orderflow:** post-MEV-Boost/private-orderflow, la extracción
  competitiva requiere acceso a flujo privado o relaciones con builders — acceso
  por relación/escala, no por compra retail.
- **P2 — Subasta de latencia/prioridad:** la competencia entre searchers se decide
  en infraestructura (colo con relays, optimización de bundles, gas auctions) donde
  el costo marginal de competir excede el capital total del operador.
- **P3 — Concentración del mercado de builders/searchers:** la cuota de extracción
  está concentrada en actores especializados con economías de escala; el entrante
  retail captura residuo negativo tras costos (gas de bundles fallidos, RPC
  premium, desarrollo).

## Criterio de cierre

- La celda se cierra **EXCLUIDA** si el survey documenta, con fuentes fechadas, AL
  MENOS UNA P que pase el filtro estructural de arriba, vigente a la fecha del survey.
- **Cláusula de escape (pre-registrada):** si el survey encuentra una vía de
  extracción MEV accesible a retail con expectativa positiva documentada y vigente
  (no anécdotas pre-2022), entonces NINGUNA P se sostiene y la celda NO se cierra
  como C — se reabre la asignación de verbo contra el spec (§2). No se fuerza el
  teorema.

## Lo que el survey NO puede hacer

- Confundir "difícil" con "estructuralmente cerrado": P debe ser de posición.
- Emitir cardinales de retorno en el Veredicto (regla del atlas).
- Tratar la celda cerrada como backlog: una C cerrada es conocimiento terminal
  (spec §3-C); la condición de reapertura debe ser un cambio estructural del
  mundo, no "revisar el año que viene".

## Requisitos del survey

3-6 fuentes fechadas (investigación sobre MEV supply chain — p.ej. Flashbots/
mev-boost docs y datos de concentración de builders, papers académicos de MEV,
datos públicos de dominancia de relays/builders). Cada P afirmada cita fuente + fecha.

## Condición de reapertura (forma pre-declarada)

"Reabre si P deja de ser cierta" — la negación concreta de la(s) P documentada(s),
p.ej. democratización verificable del acceso al orderflow. La fija el findings.
