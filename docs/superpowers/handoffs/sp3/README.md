# Valles · SP3 — handoff de implementación (idea-de-moneda)

Mockups para implementar **1:1**. Tema cálido papel/editorial. Doctrina anti-veredicto intacta.

## Cómo abrir
Abrí `Valles SP3.html` en un navegador (necesita conexión: carga React, Babel y las fuentes de Google por CDN). Es un **lienzo de diseño** (pan/zoom) con todos los frames organizados por pieza y por estado. Cada frame está rotulado con su tamaño y el dato que lo alimenta.

## Archivos
| Archivo | Qué es |
|---|---|
| `Valles SP3.html` | Host del lienzo + composición de todos los frames/estados. |
| `sp3-warm.css` | **La fuente de verdad visual.** Tokens (§8 del brief, hex corregidos AA), tipografía, marco de régimen, gráfico, secciones, estados. |
| `sp3-ideaview.jsx` | `RegimeFrame` (Pieza 1) + `IdeaView` (8 secciones, Pieza 3) + dossier/jugada/frescura. |
| `sp3-chart.jsx` | Gráfico de velas + capas. La banda de 30d (Pieza 2) y el marcador van como overlay sincronizado al eje de precio. |
| `sp3-data.jsx` | Mocks **1:1 con el contrato §7** (`/alt-season`, `/valley-eval`, `/levels`, `/plan`, `/dossier`). Sirve de referencia de forma y rango de cada campo. |
| `design-canvas.jsx` | Andamiaje del lienzo (sólo para presentar; no es parte del producto). |

## Mapa pieza → dato (todo existe en §7, cero backend nuevo)
- **Marco de régimen** ← `/alt-season` → `RegimeSnapshot` (inclinación, 3 componentes, votos, frescura).
- **Velas + paredes** ← `/levels/{sym}` (`candles`, `zonas`, `ubicacion`).
- **Banda 30d + marcador** ← `pos_in_30d_range` de `/valley-eval/{sym}` + min/max de las últimas 30 velas.
- **Narrativa "vida"** ← `/valley-eval/{sym}` (`candidata`, `pos`, `rsi14`, `pct_vs_sma20`).
- **"Tu jugada ahora" + capa jugada** ← `/plan/{sym}` (y `/plan/{sym}/conducta` para el cierre, sin PnL).
- **Quién está detrás** ← `/dossier/{sym}`.
- **Frescura** ← bloque `frescura` de cada endpoint (umbrales §7.6).

## Implementación del gráfico (importante)
En el mockup las velas se dibujan con SVG por simplicidad. **En producción usá lightweight-charts** para las velas; la banda de 30d, el marcador, las paredes y los overlays de la jugada se montan como **anotaciones sobre el eje de precio** (mismo patrón que las capas actuales). Regla de anti-colisión: si dos etiquetas quedan a <16px, se suprime la etiqueta (la línea siempre se dibuja) — ver `thinLabels()` en `sp3-chart.jsx`.

## Texto VERBATIM — NO se reescribe (marcado `/*VERBATIM*/` en el código)
- Costura per-coin: **"Esto sale de tus niveles · la decisión es tuya."**
- AC7 (bloque vida): **"Esto es la réplica del filtro que usaba el canal de 2019. Medido, no le ganó al azar de alts ni en su mejor régimen (alt-bull 2019: 14d 9.92% vs 12.54%). Lo que movió el retorno fue el régimen, no esta selección. La decisión es tuya."** — los números **9.92% vs 12.54%** son criterio de aceptación; tienen su fila de evidencia propia.
- Frase de régimen: **"Lo que más mueve el resultado es el régimen del mercado, no la moneda que elijas."**

## Propuestas de microcopy (marcadas con el chip `◆ prop`)
Mejoras de etiquetas dentro de la doctrina §4. Son **sugerencias**, no verbatim — confirmá antes de shippear. Ej.: cabecera de componentes en lenguaje natural ("amplitud (alts sobre su media 50d)").

## Decisiones de diseño tomadas (las que el brief dejaba abiertas)
1. **Marco de régimen:** contenedor envolvente + franja `clima` **sticky** que persiste al hacer scroll. En los frames estáticos la franja aparece fija; implementar como `position: sticky`.
2. **Banda 30d:** rectángulo **punteado arcilla** sobre la franja de las últimas 30 velas + marcador (punto + línea + tag "pos N%"). Distinta de las paredes (banda **rellena slate**, ancho completo).
3. Jerarquía/densidad: cuerpo 18px, prosa a 60ch, costura en bloque que respira (no letra chica).

## Accesibilidad (obligatoria — lector mayor)
Cuerpo ≥18px · secundarios ≥16px · targets ≥48px · contraste AA (hex ya corregidos) · no-solo-color (todo estado lleva microcopy) · foco 3px `--clay-deep` offset 2px · respeta `prefers-reduced-motion`.

## Fuera de alcance
No toca backend, doctrina ni contrato de datos. No rediseña la lista (PickScreen). No agrega 4ª lente ni umbrales ajustables (post-ship).
