# Valles — rediseño cálido guiado (integración del handoff de diseño)

**Fecha:** 2026-06-14 · **Estado:** DISEÑO (pre-plan) · **Alcance:** frontend-only
**Decisión suficiente:** Samuel. Este spec es condición necesaria, no autoriza el build por sí solo.

---

## 0. Qué es esto (y qué NO es)

El equipo de diseño entregó un handoff (`E:\Valles.zip` → `Valles-handoff/`): un **recorrido
guiado cálido** (tema paper/editorial, Source Serif + Instrument Sans) de 5 pantallas, pensado
para el usuario final no técnico — el papá de Samuel, ~70 años. Filosofía: *una sola cosa por
pantalla*.

**Esto NO es un build desde cero.** El feature Valles ya está vivo en `main` (PRs #584–#594),
wired a 4 endpoints reales, con tipos TS, fetchers, tests y montaje en `App.tsx`. La versión
actual es una **vista experta densa** (una `<table>` de 8 columnas). El handoff es el
**rediseño visual** de ese feature ya construido.

El trabajo real, entonces, es **portar el flujo cálido a componentes React/TS montados sobre
los endpoints que YA existen**, demoliendo dos supuestos load-bearing del prototipo que son
falsos en runtime real (datos síncronos · tema claro global), y respetando la doctrina que el
backend ya enforcea.

### Endpoints reales (ya wired — `api.ts:414-434`, tipos `types.ts:507-589`)

| Lente | Endpoint | Fetcher | Tipo | Notas |
|---|---|---|---|---|
| Foto/screener | `GET /valley-candidates` | `getValleyCandidates()` | `ValleySnapshot` | cargado on-demand en `App.tsx` |
| Vida | `GET /valley-eval/:sym` | `getValleyEval()` | `ValleyEval` | computa en vivo, sin caché, sin frescura propia |
| Niveles | `GET /levels/:sym` | `getLevels()` | `SrLevels` | velas+spot en vivo, sin caché, `estado: ok\|no_disponible` |
| Fundamentales | `GET /dossier/:sym?refresh` | `getDossier()` | `Dossier` | caché 7d, `estado_general: rastreable\|opaco\|no_disponible`, **lleva frescura** (umbral 604800s) |

**Verificación de contrato (clave):** el mock `data-valles.jsx` se construyó casi exacto sobre
los tipos reales. **CERO campos inventados.** No hace falta ningún adapter de transformación de
*shape*; solo desenvoltura de envoltura, borrado de fallbacks, null-guards y una rama de carga.

---

## 1. La doctrina que el rediseño DEBE preservar

Valles existe bajo una ley inviolable, ya enforced en el código y en los specs #584/#589/#586
(A / D.1 / C) y F3b: **exhibe hechos, nunca un veredicto.** En concreto:

1. **Sin score, sin ranking de calidad.** Orden neutral por liquidez (`volumen_usd_dia` desc);
   nunca reordenar por "atractivo" (eso es claim de edge → celda B, diferida).
2. **Tres lentes SEPARADAS** (Vida · Niveles · Quién). Yuxtaponerlas en una conclusión = la
   "cuarta línea" prohibida por F3b (§0/§3/§6): tres hechos no autorizan un cuarto objeto.
3. **`opaco` ≠ `no_disponible`.** `opaco` = "se buscó, no hay rastro" (hallazgo legítimo, misma
   fuerza que un hecho positivo). `no_disponible` = "falló la herramienta". Nunca colapsarlos.
4. **Frescura honesta como contrato** (no-negociable #8 de `CLAUDE.md`). Todo dato vivo que cruza
   frontera emite su frescura (`fresco`/`rancio`/`muerto`) vía `LiveSnapshot`; nunca un vacío mudo
   que enmascare muerte operacional. El UI **hereda** `frescura.estado` del backend; jamás
   reclasifica con umbral propio.
5. **Cada hecho del dossier anclado a su `fuente` verificable** (candado anti-alucinación
   `_anchor_ok` de C: una cita sin fuente en el set de Exa se descarta). La fuente citable ES el
   mecanismo que distingue hecho de alucinación.
6. **El color no juzga.** Verde/ámbar como semáforo bueno/malo está prohibido. El color solo
   codifica un hecho temporal (frescura) o geométrico, nunca un juicio.

El **riesgo central de este rediseño no es técnico** (los shapes calzan): es que **la calidez
reintroduzca el veredicto** que todo el sistema gasta su diseño en NO emitir. Un tono que se
desliza de "aquí están los tres hechos" a "mira qué bien se ve esto" es un veredicto implícito.

---

## 2. Decisiones cerradas con Samuel (2026-06-14)

| # | Decisión | Elección |
|---|---|---|
| D1 | Encuadre del tema | **B — Valles cálido AISLADO**, scopeado, conviviendo con el chrome dark del trading. |
| D2 | Fidelidad | **Pixel-perfect literal** en geometría/tipografía/layout, **EXCEPTO** copy (voseo→tuteo) y los 4 puntos doctrinales de §4. |
| D3 | Alcance de la vista | **Solo el recorrido guiado simple.** |
| D4 | Tabla densa actual | **Eliminar.** Sin toggle, sin vista experta. El recorrido cálido la reemplaza por completo. |
| D5 | Móvil | **En v1.** Añadir `valles` al `BottomNav` + verificar responsive por pantalla. |
| D6 | Colisiones doctrina↔literal | **Corregir los 4** (§4); mantener todo lo demás pixel-perfect. |
| D7 | Copiloto | **Mock en v1** (defer del LLM real a v1.1). |

---

## 3. Arquitectura

Todo nuevo vive en `frontend/src/components/valles/`. El trading app en vivo no se toca (salvo
3 líneas de montaje en `App.tsx`).

```
App.tsx ──(mainTab==='valles')──> <ValleysFlow snapshot={valleys} loading={valleysLoading} />
                                        │
   ┌────────────────────────────────────┼─────────────────────────────────────┐
   │ ValleysFlow (orquestador + chrome cálido .vwRoot + stepper + nav + copiloto)
   │   estado: step ('pick'|'vida'|'niveles'|'fund'|'cierre'), sym, dockOpen
   │   persistencia: localStorage vw_sym / vw_step (rehidrata → dispara fetch real, NO bundle)
   │
   ├─ PickScreen     ← snapshot (prop, ya cargado en App)
   ├─ VidaScreen     ← useValleyBundle(sym).vida      (getValleyEval)
   ├─ NivelesScreen  ← useValleyBundle(sym).niveles   (getLevels)   [reusa LevelsPanel restyled]
   ├─ FundScreen     ← useValleyBundle(sym).dossier   (getDossier)  [reusa ProjectDossier restyled]
   ├─ ClosingScreen  ← deriva 3 recaps de los 3 estados ya cargados (3 columnas SEPARADAS)
   └─ Copilot        ← mock canned (cero datos, cero backend)
```

### 3.1 `useValleyBundle(symbol)` — la capa de datos honesta (reemplaza `bundle()`)

`bundle()` del prototipo (`valles-flow.jsx:31-46`) **se elimina, no se porta**. Fabrica datos
(dossier `opaco`+frescura `fresco` inventada; niveles sintéticos "sin paredes"; vida derivada de
la fila de 12h) — en el prototipo "ausente = no escribí el mock"; en runtime real "ausente =
fetch en vuelo / 429 de Binance / fallo de red". Portarlo = violación directa de #8.

En su lugar, un hook que dispara **3 fetches independientes** (patrón del `CoinCard` real:
loadings/frescuras separados, keyed en `[symbol]`, reset on-symbol):

- Cada lente expone su `estado`/`estado_general` **crudo**; cero fallback fabricado.
- **4º estado `cargando`** por lente (el prototipo nunca carga — es síncrono). Sin esto, cada
  navegación parpadea por la rama `no_disponible` mintiendo "la herramienta falló" cuando solo
  está en vuelo.
- **Caché por-símbolo** + **`AbortController`/symbol-guard** contra el rate-ban de Binance
  (`/valley-eval` y `/levels` golpean Binance sin caché en cada request) y contra la stale-response
  race (Pick A → fetch → Pick B antes de que A resuelva → la respuesta tardía de A escribe bajo el
  header de B).
- **Frescura:** reusar el `FreshnessTag` real (consume el `Frescura` completo). **NO** portar el
  átomo `Fresh`/`fmtEdad` del prototipo, que re-deriva la frescura ad hoc = fork del contrato #8.

### 3.2 Montaje en `App.tsx` (las únicas 3 líneas que cambian del shell)

- `import { ValleysView }` (línea 81) → `import { ValleysFlow } from './components/valles/ValleysFlow'`.
- Render (762-764): `<ValleysView snapshot={valleys} loading={false} />` →
  `<ValleysFlow snapshot={valleys} loading={valleysLoading} />`, **cableando `loading` real**
  (hoy `loading={false}` está hardcodeado; añadir un `valleysLoading` state alrededor del
  `getValleyCandidates()` del `useEffect` 223-224).
- `ValleysView.tsx` y su `<table>` densa **se eliminan** (D4). `CoinCard.tsx` (prototipo de
  composición F3b) se desarma; su patrón async-por-símbolo es el modelo de `useValleyBundle`.
  `LevelsPanel`, `ProjectDossier`, `FreshnessTag` **se conservan y se re-estilizan** bajo `.vwRoot`.

---

## 4. Las 4 correcciones doctrinales (D6 — pixel-perfect en todo lo demás)

Copiar el diseño literal regresaría la doctrina que YA está enforced en el código mergeado. Se
corrigen estos 4 puntos; el resto del tema cálido se porta fiel.

1. **Tagline `vw-brand__tag` "dónde operar y dónde no" (`valles-flow.jsx:514`).** Es un veredicto
   de operabilidad, persistente en cada pantalla, que contradice la cabecera del propio archivo
   ("exhibe hechos, no decide"). → **Reescribir a una promesa de solo-hechos** (el salto a
   "oportunidad" lo da el humano). Texto final vía `solace-wren`.
2. **Color de juicio (sage/ochre como semáforo).** sage-verde para "viva"/"se sabe quién"
   (`:186,337`) y ochre para "muy quieta"/"opaco" (`:172`), más `vw-floor--piso` sage /
   `vw-floor--techo` ochre (CSS `:271-272`). Verde=bueno/ámbar=malo es el semáforo prohibido;
   un lector de 70 años lo lee como "compra". → **Iconos y bandas en neutro** (arcilla/pizarra)
   para las 3 ramas. **sage/ochre se reservan EXCLUSIVAMENTE para frescura** (fresco/rancio/muerto),
   donde el color codifica un hecho temporal real.
3. **Copy de continuidad en Niveles ("rebotó" `:247`, "apoyada en un piso" `:245-246`).** Presupone
   que el piso aguantará — predice la continuidad de la multitud, error de tipo que D.1 §1 rechaza
   (solo hay 1 snapshot vivo, no histórico futuro). → **Hablar solo del pasado de las velas**
   ("el precio ya giró ahí N veces — hecho del gráfico"), nunca del futuro.
4. **FundScreen sin `fuente`.** Muestra equipo `{nombre,rol}` (`:345`) y canales `href={p.url}`
   (`:353`) pero **nunca `m.fuente`** — regresión vs el `ProjectDossier` real, que SÍ renderiza
   `<Fuente url={m.fuente}/>` por hecho. El propio `say` promete "compruébalo en su fuente". →
   **Anclar cada hecho a su `fuente`** (`DossierMiembro/Canal/Cita.fuente`).

---

## 5. Mapeo pantalla → componente → datos

### 5.0 Stepper + chrome (`ValleysFlow`)
Nuevo orquestador. Stepper de 3 lentes con **labels siempre visibles** + **"Paso X de Y" literal**
(resolver el desfase 3-lentes vs 5-pasos: `pick`/`cierre` quedan fuera del indicador). Nav por
teclado (ArrowLeft/Right) + persistencia `localStorage`. Al rehidratar en step 3 → **dispara fetch
real + estado pending**, no resucita contexto con datos fabricados.

### 5.1 PickScreen — "la foto de hoy" ← `snapshot` (prop)
- Render de `snapshot.candidates` **reales** en orden de liquidez (`volumen_usd_dia` desc); nunca
  reordenar. Quitar `PICK`/`NAMES` hardcodeados.
- Leer `coverage{universe,evaluated,complete}` + `frescura` **juntos** para 3 estados ortogonales:
  *"el screener nunca corrió"* (`complete=false` + `candidates=[]` + frescura `muerto`) vs *"corrió
  y no halló nada"* (`complete=true` + `candidates=[]` + frescura `fresco`) vs *"hay candidatas"*.
- Titular "Hoy hay N monedas en valle" **por encima** del semáforo de frescura (hoy invertido
  `:112-113`). Frescura vía `FreshnessTag`.
- Buscador de símbolo arbitrario: **rama "símbolo no reconocido"** (hoy `bundle()` lo disfraza de `opaco`).

### 5.2 VidaScreen — "¿Está viva?" ← `getValleyEval` (`ValleyEval`)
4 ramas: `cargando` (nueva) · `estado==='no_disponible'` (callout "problema de la herramienta") ·
`candidata===false` (`(razones_muerte ?? []).map()` — enum real: `volumen_bajo_piso`,
`volumen_agonizante`, `velas_planas`, `historia_insuficiente`) · `candidata===true`
(`pct_rango`, `semanas_consolidando`, `vol_percentil` 0..1 → quietud=`100−round(vol_percentil*100)`).
`ValleyEval` cambia de shape por rama (campos opcionales) → **narrowing por rama antes de leer**.
Frescura **heredada de la foto** (`photo.frescura`, hasta 12h): dos relojes en el Eyebrow (la foto
es vieja, la lectura de Vida es spot) — el copy no debe implicar que Vida es "de hace 5 min". Icono neutral.

### 5.3 NivelesScreen — "¿Dónde está el precio?" ← `getLevels` (`SrLevels`) — **reusa `LevelsPanel`**
4 ramas: `cargando` · `estado==='no_disponible'` ("falló", NO colapsar en "sin paredes") ·
`zonas.length===0` ("todavía no hay paredes claras" + `price_live`) · con zonas
(`ubicacion{dentro_de{tipo,toques}, techo{centro,dist_pct}, piso{centro,dist_pct}}` + `price_live`).
`dist_pct` ya viene **firmado y a 2 decimales del backend** (techo +, piso −) — NO recalcular en
cliente. `price_live` es `number|null` → pintar "—". Floors en neutro (corrección §4.2). Copy de
continuidad corregido (§4.3).

### 5.4 FundScreen — "¿Quién está detrás?" ← `getDossier` (`Dossier`) — **reusa `ProjectDossier`**
4 ramas: `cargando` · `no_disponible` ("falló la búsqueda") · `opaco` ("no se encontró quién está
detrás" — misma fuerza que un hecho, no suavizar) · `rastreable` (`equipo[]{nombre,rol}` +
`presencia{}` iterando **solo keys presentes** — `Record` no garantizado). Anclar cada hecho a su
`fuente` (§4.4). `href={p.url ?? undefined}` (url puede ser null). **Frescura exhibida también en
rama `opaco`** (hoy solo en `rastreable`, `ProjectDossier:369` — un opaco de 9 días puede haberse
vuelto rastreable). Botón refresh (`getDossier(sym,true)`) para `no_disponible`. Heredar
`frescura.estado` (umbral real 604800; el mock usaba 86400 errado).

### 5.5 ClosingScreen — "cierre honesto" ← deriva (cero datos nuevos)
**Riesgo doctrinal alto.** Recap de los 3 estados-resumen en **3 columnas SEPARADAS**, cero
frase-puente que sintetice (la "cuarta línea" prohibida). El texto "no suma las tres en una nota"
debe estar respaldado por el layout. El **test anti-veredicto** corre sobre este componente.

### 5.6 Copilot — dock + FAB (mock en v1)
Portado literal como **mock canned** (cero datos, cero backend, no activa #8). FAB visible cuando
`step>=1`. **Ampliar el set de keywords del rechazo**: hoy es `includes()` sobre la pregunta del
usuario y "¿en cuál entro?", "¿vale la pena ADA?", "¿qué harías tú?", "should I buy" caen al
return genérico **sin** `refusal` — el MAPA 3 exige `{refusal:true}` para esas. (El LLM real es
v1.1: el guardrail anti-veredicto no puede vivir en `includes()` del cliente, y un LLM que
sintetice los 3 bloques es el "compositor en el ojo" que F3b prohíbe.)

---

## 6. Tema cálido scopeado (`.vwRoot`)

`valles-warm.css` declara tokens en `:root` y aplica `html, body { background: var(--paper) }` +
estiliza `html, body, *` globales. **Soltarlo tal cual repinta TODA la app dark** (la colisión no
es por nombres — los prefijos difieren `--nbc-*` vs `--paper`/`--clay` — sino por la propiedad
`background`/`color` en `:root` y los selectores bare-element).

- Reescribir a `.module.css` con **todos los tokens re-declarados bajo `.vwRoot`** (no `:root`).
  Borrar las reglas `html, body, *` globales. Las clases `vw-*` se conservan confinadas bajo `.vwRoot`.
- El contenedor raíz de `ValleysFlow` lleva `.vwRoot`; el tema cálido vive solo en ese árbol.
- Pixel-perfect se conserva en: escala tipográfica, `--maxw 660px`, `--ease`, y los átomos
  `vw-band`/`vw-building`/`vw-floor`/`vw-here`/`vw-people`/`vw-channels`/`vw-callout`/`vw-cand`/
  `vw-recap`/`vw-fab`/`vw-dock`. Lo que cambia es el **scoping** (`:root`→`.vwRoot`) y el
  **color de juicio** (§4.2), no la geometría.
- `LevelsPanel`/`ProjectDossier`/`FreshnessTag` reusados: decidir si heredan tokens cálidos o
  mantienen neutros — **no mezclar a medias** (hoy usan `var(--border,#2a2a2a)` y hex literales).

---

## 7. Accesibilidad geriátrica (~70 años) — no-negociable

Ningún fix de a11y puede convertir sage en "verde=comprar" ni clay en "rojo=malo" (la corrección
de color es semántica de frescura/geometría, no de juicio).

**Contraste (BLOCKERS, calculados sobre los hex reales):**
- `--ink-4 #ABA08A` (2.27:1) → **`#6F6856`** (~4.6:1). Golpea `vw-eyebrow__sym`, `vw-num__note`,
  placeholders, y `vw-fresh--muerto` (que el estado "muerto" sea el menos legible es fallo de #8
  además de a11y). Idealmente no usar ink-4 para texto leíble.
- `--ink-3 #8A8270` (3.35:1) → **`#6E6757`** (~4.6:1). Golpea `vw-num__k`, `vw-recap__q`,
  `vw-entry__meta` (la cobertura honesta), `vw-dist__k`, `vw-cand__sym`, `vw-band__cap`.
- `--ochre #A9772A` (3.45:1, y es el color de "atención") → **`#8A5E1C`** (~5:1).
- `--clay` como texto (4.25:1) → **`--clay-deep #9A4424`** (~5.9:1); `--clay` queda solo fondo/borde
  (blanco sobre clay sí pasa, botones OK).

**Táctil:** mínimo **48×48px** (mano temblorosa geriátrica). Peores: `vw-dock__close` ~22px,
`vw-sugg` ~27px, `vw-callout__retry` ~30px, `vw-channel` ~35px, `vw-step` ~36px (target real 24px),
`vw-btn--*` ~41-43px. `min-height:48px` en `vw-btn` base, hit-area ampliada en los chips/×.

**Tipografía:** cuerpo ≥**18px** (presbicia). Subir `vw-answer__say`/`vw-pick__lead`/`vw-close__body`
de 17px y `vw-fact` de 16.5px → 18px; secundarios → 16px; mínimos → 14px; `vw-tag` → 12px.
Migrar cuerpo a `rem` (1.125rem) para respetar el tamaño de fuente del SO. `max-width: 60ch` en
párrafos largos.

**Movimiento:** `prefers-reduced-motion` hoy cubre solo `vwIn`. Ampliar a **reset global**
(`*` animation/transition-duration ~0 + `transform:none` en hovers) — `vwDockIn`, `vwFade`, y los
hover-scale quedan vivos y marean.

**Foco/semántica:** añadir `:focus-visible` (outline 3px `--clay-deep`) — hoy no existe. Stepper
con labels siempre visibles + `aria-label "Niveles, paso 2 de 3"`. Dots de canal con microcopy
("activo/inactivo/sin confirmar"), no solo color (WCAG 1.4.1). Glifos Unicode (`∿ ⌖ ☻ ◍ ◈ …`) con
`aria-hidden` + significado en el texto adyacente. Botón "Atrás" en step 1: **visible-inactivo
(opacity .4)**, no `opacity:0` (control fantasma enfocable).

---

## 8. Voz: voseo → tuteo venezolano (obligatorio)

El lector es venezolano de ~70 años; el voseo argentino le grita "esto no es para mí" y viola el
`CLAUDE.md` global de voz. Reemplazos exactos en `valles-flow.jsx`: "Elegí una" (`:116`)→"Elige
una"; "¿Buscás otra…?" (`:140`)→"¿Buscas otra…?"; "escribí su símbolo" (`:144`)→"escribe su
símbolo"; "Probá de nuevo" (`:230`)→"Prueba de nuevo"; "eso lo decidís vos" (`:400-401`)→"eso lo
decides tú"; "Preguntame por cualquiera" (`:430`)→"Pregúntame por cualquiera"; "El tamaño lo
decidís vos" (`:428`)→"El tamaño lo decides tú"; "preguntá en tus palabras" (`:458`)→"pregunta en
tus palabras". **Corregir también voseos preexistentes en los componentes reusados**: `LevelsPanel`
"Probá de nuevo"→"Prueba de nuevo"; `ProjectDossier` "Probá refrescar"→"Prueba a refrescar".

El microcopy final pasa por la skill **`solace-wren`**: calidez que **describe sin alentar** (el
entusiasmo es un veredicto implícito), sin AI slop, sin celebrar la entrada.

---

## 9. Móvil (v1 — D5)

`BottomNav` hoy tiene 4 items y **no** incluye `valles`. Añadir el item `valles` (icono: reusar
`history` como hace `LeftRail`, o extender `RailIconName` + el switch de `RailIcon` con un icono
propio). El recorrido pantalla-única (`--maxw:660px`, 1 columna) es naturalmente portable, pero
**verificar responsive por pantalla**: labels del stepper visibles <720px (hoy `display:none` → 3
círculos mudos), `vw-building` a 240px (recalcular la constante `hereTop = 58 + clamp*(…)`), dock
a ancho completo, táctil ≥48px en pantalla chica.

---

## 10. Plan por fases (estimado ~22-26h)

- **F0 — Andamiaje + capa de datos honesta (~4h).** `components/valles/` con `ValleysFlow` +
  `.module.css` scopeado a `.vwRoot` (sin `:root`, sin `html/body/*`). `useValleyBundle(symbol)`
  (3 fetches independientes, caché por-símbolo, AbortController/symbol-guard, 4º estado `cargando`).
  Reusar `FreshnessTag` real. No tocar `App.tsx` aún.
- **F1 — PickScreen real (~3h).** Snapshot real, orden de liquidez, 3 ejes de estado honestos,
  buscador con rama "no reconocido", titular sobre frescura.
- **F2 — Las 3 lentes (~7h, el grueso).** Vida/Niveles/Fund sobre datos reales, 4 ramas cada una,
  sin color de juicio, fuentes ancladas en Fund, copy de continuidad corregido.
- **F3 — Cierre + navegación + copiloto mock (~4h).** ClosingScreen 3 columnas separadas (test
  anti-veredicto pasa), stepper legible "Paso X de Y", copiloto mock con keywords ampliadas.
- **F4 — Cableado + copy + a11y + corte de doctrina (~5h).** Montar en `App.tsx` (reemplazar
  `ValleysView` por `ValleysFlow`, cablear `loading` real, **eliminar la tabla densa**), tagline
  reescrito, voseo→tuteo + `solace-wren`, a11y geriátrica (§7), gate de doctrina.
- **F5 — Móvil (~3h).** `valles` en `BottomNav` + responsive verificado por pantalla (§9).

**Defer a v1.1 (nombrado, no construido defensivamente):** copiloto real (`/agent`); campos
narrativos del dossier (`actividad`/`financiacion`/`hitos`/`no_encontrado_en` — munición real, no
core); `distancia_ath_pct` (riesgoso: "más lejos = mejor" es claim → celda B); `VerNumeros`
(disclosure progresivo, primer candidato a cortar si F2 se alarga).

---

## 11. Verificación (gate)

- **Test anti-veredicto:** `textContent` de los componentes nuevos NO contiene
  `compra|buena|score|recomend|veredicto|potencial`.
- **Distinciones ortogonales no colapsadas:** frescura ≠ `no_disponible` ≠ `opaco` (tres mensajes
  distintos, tres ramas distintas).
- **Sin color de juicio:** sage/ochre solo en frescura; iconos de lente y bandas en neutro.
- **#8 frescura:** ningún estado vacío/cargando se pinta como dato fresco; el `FreshnessTag` real
  exhibe la frescura heredada del backend en todas las ramas que llevan dato (incluido `opaco`).
- **Fuentes:** cada hecho del dossier renderiza su `<Fuente>`.
- **a11y:** contraste AA (hex de §7), táctil ≥48px, cuerpo ≥18px, reduced-motion global,
  `:focus-visible`, móvil navegable.
- **Sin regresión del trading app:** `tsc` verde (cuidado `noUnusedLocals`/`noUnusedParameters`);
  el chrome dark intacto (tema cálido confinado a `.vwRoot`); gate `pytest -m "not network"` sin tocar.

---

## 12. Riesgos top

1. **Calidez = veredicto encubierto.** El mayor riesgo, y es de tono, no técnico. El copy describe, nunca celebra.
2. **#8 frescura.** El prototipo "oculta su muerte con dignidad" (fallbacks con apariencia fresca).
   `bundle()` se borra, no se porta; el prototipo pelea contra ti inventando datos amables.
3. **CSS blast radius.** Reescritura a `.vwRoot` de alto esfuerzo; mal hecho repinta toda la app.
4. **Loading colapsado en `no_disponible`.** Sin la 4ª rama, cada navegación miente "falló".
5. **Rate-ban de Binance.** `/valley-eval` y `/levels` sin caché; navegación rápida ida-y-vuelta =
   429/418 → `no_disponible`. Caché por-símbolo + abort, ausentes en el prototipo.
6. **Eliminar la tabla densa (D4)** quita el único acceso experto a los datos crudos — confirmado
   por Samuel; queda recuperable vía git si alguna vez se quiere de vuelta.
