# Celda 7 — vrp-opciones · Criterios de descarte pre-registrados

**Verbo:** R (realizabilidad-acotada) — pre-asignado en el catálogo congelado 2026-06-04.
**Pre-registro:** este documento se commitea ANTES de ejecutar el survey. Los criterios
de abajo no se editan después de iniciada la búsqueda de fuentes.
**Fecha de pre-registro:** 2026-06-05.

## Perfil del operador

El mismo de las celdas 5-6: capital 5 cifras, retail, Windows local, stack actual
del repo = OHLCV + funding (NO existe motor de pricing de opciones ni de griegas;
el cost-model v3 NO aplica a opciones — hallazgo Halberg 2026-06-04, pre-survey).

## Pregunta del dictamen

¿Es la cosecha del variance risk premium en opciones cripto (Deribit u otros venues
accesibles) **estudiable y ejecutable** para este operador, y si no, cuál es la
infraestructura faltante exacta (data, motor, venue, capital)?

## Criterios de descarte (declarados ANTES del survey)

La celda se cierra **INVIABLE-RETAIL** si el survey confirma CUALQUIERA de:

- **D1 — Acceso al venue:** el operador no puede operar legalmente/prácticamente
  en ningún venue de opciones cripto líquido desde su jurisdicción y perfil, o los
  mínimos de cuenta/margen del venue líquido exceden capital 5 cifras bajas.
- **D2 — Riesgo no acotable:** la estructura corta-vol mínima viable exige margen
  de portafolio o gestión de cola de riesgo (gamma en crashes) que con capital 5
  cifras implica riesgo de ruina no acotable con los gates del programa — es decir,
  no hay tamaño viable que sea a la vez significativo y sobrevivible.

La celda se cierra **REQUIERE-INFRA-\<x\>** si la realizabilidad falla por una pieza
**adquirible o construible**, evaluando como candidatas (pre-declaradas):

- **X1 — DATA:** falsificar VRP requiere histórico strike-level (superficie IV /
  marks de opciones) que es de pago (p.ej. Tardis, Amberdata); el proxy gratuito
  (índices tipo DVOL) NO basta para un falsificador pre-registrable con P&L
  $-denominado. El dictamen debe acotar costo mensual/one-shot con fuente fechada.
- **X2 — MOTOR:** no existe en el repo motor de pricing/griegas/márgenes; construirlo
  es un proyecto propio (estimar orden de magnitud en semanas-persona, no en líneas).
- **X3 — VENUE/EJECUCIÓN:** la pieza faltante es operativa (cuenta, margen de
  portafolio, conectividad al venue) y comprable/tramitable.

Si más de una x aplica, el dictamen las lista TODAS y nombra la dominante (la que
desbloquea menos sin las otras).

## Lo que el survey NO puede hacer

- Emitir cardinales de retorno esperado del VRP en el Veredicto (regla del atlas).
  Los cardinales de COSTO de infraestructura sí van (son el objeto del dictamen R).
- Concluir "PASS/FAIL" — eso es vocabulario del verbo F. Si el survey sugiere que
  la celda podría ser F-testeable YA con data gratuita suficiente, NO se emite
  verdict: se reabre la asignación de verbo contra el spec (§2).

## Requisitos del survey

3-6 fuentes fechadas (docs de Deribit u otro venue sobre márgenes/mínimos y acceso;
pricing de proveedores de data de opciones con fecha de consulta; literatura sobre
VRP cripto para fijar QUÉ data exige un falsificador honesto).

## Condición de reapertura (forma pre-declarada)

"Reabre si se adquiere la x dominante" (p.ej. se compra la data strike-level o se
construye el motor). La x concreta la fija el findings.
