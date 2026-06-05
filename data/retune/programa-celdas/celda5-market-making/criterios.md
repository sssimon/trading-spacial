# Celda 5 — market-making · Criterios de descarte pre-registrados

**Verbo:** R (realizabilidad-acotada) — pre-asignado en el catálogo congelado 2026-06-04.
**Pre-registro:** este documento se commitea ANTES de ejecutar el survey. Los criterios
de abajo no se editan después de iniciada la búsqueda de fuentes (fósil desde el commit).
**Fecha de pre-registro:** 2026-06-05.

## Perfil del operador (el "mundo" del dictamen — spec §3-R)

Capital 5 cifras (USD), retail, Windows local en residencia (latencia a matching
engines de exchanges cripto ≥50 ms típica, sin colo, sin cross-connect), un solo
operador humano, API pública de exchange (REST + websocket), sin acuerdos de
market-making con el exchange.

## Pregunta del dictamen

¿Es el market-making en cripto (spot o perps, exchanges centralizados líquidos)
realizable con expectativa positiva PARA ESTE OPERADOR, y si no, cuál es exactamente
la infraestructura faltante?

## Criterios de descarte (declarados ANTES del survey)

La celda se cierra **INVIABLE-RETAIL** si el survey confirma CUALQUIERA de:

- **D1 — Latencia/cola:** la rentabilidad del MM pasivo depende de posición de cola
  (queue position) y de cancelación rápida ante toxic flow, y la ventaja la deciden
  latencias en el orden de ≤10 ms hacia el matching engine — inalcanzables desde el
  perfil declarado (≥50 ms residencial) Y no comprables por <100 USD/mes (cláusula
  puente con D-infra abajo).
- **D2 — Tier de fees:** los maker rebates o fee tiers que hacen el spread capturable
  neto-positivo requieren volumen mensual o saldo (VIP/BNB-holdings/MM-program) fuera
  del alcance de capital 5 cifras.
- **D3 — Selección adversa:** la evidencia muestra que para un maker lento (sin
  cancel sub-10ms) el costo de selección adversa esperado excede el spread medio
  capturable en los pares líquidos accesibles — es decir, el MM lento es
  estructuralmente el lado que pierde del flujo informado.

La celda se cierra **REQUIERE-INFRA-\<x\>** si el bloqueo dominante identificado es
**adquirible** (ejemplos a evaluar en el survey: VPS en la región del matching engine
~AWS Tokio para Binance; software de MM open-source mantenido tipo Hummingbot;
programa formal de MM del exchange) y el survey acota su costo y lo que NO resuelve.
El dictamen debe nombrar la x concreta y su costo aproximado con fuente.

## Lo que el survey NO puede hacer

- Emitir un cardinal de retorno esperado en el Veredicto (regla del atlas).
- Citar `walk_book`/simulaciones taker del repo como evidencia de MM (miden
  taker-cross, lo opuesto a maker — hallazgo Halberg 2026-06-04, pre-survey).
- Racionalizar un criterio nuevo post-hoc: si ninguno de D1-D3 ni la rama
  REQUIERE-INFRA aplica limpiamente, la celda NO se cierra con este verbo — se
  reabre la asignación contra el spec (§2) en el INDEX.

## Requisitos del survey

3-6 fuentes fechadas (papers de microestructura cripto, documentación de fee
schedules del exchange con fecha de consulta, post-mortems/escritos de practicantes
de MM retail, docs de programas de MM). Cada afirmación que active D1-D3 debe citar
fuente + fecha.

## Condición de reapertura (a refinar en findings.md)

Se pre-declara la forma: "reabre si cambia X estructural del perfil del operador o
del mercado" — la x concreta la fija el findings con base en el bloqueo dominante
encontrado.
