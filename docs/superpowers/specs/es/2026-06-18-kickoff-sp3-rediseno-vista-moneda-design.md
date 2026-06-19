# Brief de diseño — SP3: rediseño de la vista "idea de moneda" (Valles)

Fecha: 2026-06-18 · Para: equipo de diseño · Modo: handoff (mockups → otro equipo implementa 1:1)

---

## 1. Cómo usar este brief

Hola. Este documento existe para que puedas diseñar esta pantalla **sin abrir el código, sin hablar con ingeniería y sin investigar nada por fuera**. Todo lo que necesitas — la historia del producto, quién lo va a usar, lo que está prohibido, cómo se ve hoy, cada número que vas a poner en pantalla, la paleta exacta, los estados a cubrir y los entregables — está aquí adentro.

Léelo en orden. La estructura es:

1. **Cómo usar este brief** (esto).
2. **La historia** — por qué existe el feature. La honestidad es el alma; tienes que sentirla antes de diseñar.
3. **El usuario** — para quién diseñas (un lector mayor que quiere la vista rica).
4. **La doctrina anti-veredicto** — lo NO-NEGOCIABLE. Léelo dos veces.
5. **El encargo** — las 3 piezas concretas que tienes que entregar.
6. **Cómo se ve hoy** — los wireframes y el texto actual, palabra por palabra. Tu punto de partida.
7. **Los datos que el diseño mostrará** — cada campo que aparece en pantalla, qué significa, su rango y un ejemplo real.
8. **Guía de estilo** — paleta, tipografía, espaciado, radios, sombras.
9. **Estados a mockear** — la lista cerrada de pantallas/variantes que tienes que dibujar.
10. **Restricciones técnicas** — qué NO puedes pedir (cero backend nuevo), y qué tecnología se usa.
11. **Entregables** — qué mockups entregas, en qué formato, y qué decisiones visuales son tuyas.
12. **Fuera de alcance** — lo que este encargo NO toca.

Un aviso de tono que vas a entender mejor en §2 y §4: este producto **no recomienda comprar nada**. Muestra hechos y deja la decisión en manos de quien mira. Eso no es un detalle legal — es la tesis del producto. Si tu diseño hace que algo *parezca* un consejo de compra (un verde que dice "luz verde", una flecha que sube, un botón que dice "esta es la buena"), rompiste el feature. Más sobre esto abajo.

Cuando termines, vuelve a §11 para confirmar la lista de entregables.

---

## 2. La historia (por qué existe este feature)

### Qué es Valles

Valles es una vista dentro de una app de trading de criptomonedas (Bitcoin y "altcoins" — el resto de las monedas). **No es un bot que compra.** Es una pantalla que le *muestra hechos* a una persona sobre una moneda, para que esa persona decida sola si entra o no.

La regla de oro de todo el proyecto es vieja y brutal: la app **exhibe hechos, nunca firma un veredicto**. Nunca dice "compra esto". Nunca dice "esto va a subir". Nunca le pone un puntaje de "qué tan buena" es una moneda. Lee datos y se calla la opinión.

### Qué era "el canal de 2019" (musikito)

En 2019 existió un canal de Telegram — el del dueño del proyecto — que recomendaba monedas. Valles nació para *replicar el criterio* de ese canal: qué monedas miraba, en qué momento. La idea romántica era: "el canal tenía un ojo; vamos a codificarlo".

### El hallazgo incómodo (el corazón del feature)

Aquí está el corazón emocional, y tienes que sentirlo para diseñar bien.

El equipo no adivinó el criterio del canal: lo **midió estadísticamente**. Sacaron las características técnicas de cada moneda que el canal recomendó (89 llamadas con datos) y las compararon contra una muestra de monedas al azar del mismo período. Dos resultados:

- **La firma de selección es real.** El canal compraba debilidad técnica en corrección: monedas en el cuarto inferior de su rango de 30 días (posición ≈0.165 vs 0.256 al azar), sobrevendidas (RSI ≈38.7), por debajo de sus medias móviles. Eso existe, es coherente, se puede describir.
- **Pero esa selección NO sirve para ganar.** Las monedas recomendadas **no le ganaron al azar** — fueron incluso un poco *peores*. A 14 días: **9.92% (las recomendadas) vs 12.54% (el azar de alts)**. A 7 días: 6.67% vs 7.56%. Un estudio más grande (2020–2025, 455 mil filas) lo confirmó en *todos* los climas de mercado: elegir la moneda con ese criterio no paga en ninguno.

La conclusión honesta, textual del documento de evidencia:

> "el retorno de 2019 fue beta del bull market de alts + timing de ciclo, no selección de coin individual. El edge real de musikito estaba en cuándo estaba operando (alt-season), no en cuál coin elegía esa semana."

Traducido para diseño: **elegir la moneda no sirve. Lo único que mueve el resultado es el clima del mercado** — si es "temporada de alts" o no. El feature original (un "elige ganadores") está construido sobre una premisa que la evidencia niega.

### Por qué la reorientación (SP1 → SP2 → SP3)

En vez de esconder esto, el proyecto giró sobre el hallazgo. Tres subproyectos:

- **SP1 — la pieza de régimen ("¿es alt-season?").** Ya está en producción. Es una cabecera que dice el *clima* del mercado (inclinado a alts / mixto / inclinado a BTC) como un **hecho de mercado**, no un consejo. Es el único eje donde se puede exhibir dirección sin firmar sobre ningún símbolo. Aquí vive la única señal con valor real.
- **SP2 — el detector honesto.** El detector viejo estaba roto: medía amplitud lateral "ciega al orden" — marcaba igual una moneda en el *techo* de su rango (cara) que en el *piso* (la zona del canal). SP2 lo arregló: filtra por "parte baja del rango de 30 días" y, clave, **se nombra por su procedencia** ("la réplica del filtro que usaba el canal de 2019"), no por una tesis de mercado. Y exhibe el hecho medido de que ese filtro no le ganó al azar.
- **SP3 — este rediseño.** La UI profunda: jerarquía, layout, microcopy, y la subordinación visual de la moneda a la cabecera de régimen.

### La moraleja para ti

La honestidad **no es un disclaimer al pie**. Es la tesis del producto. El feature dice: "te muestro lo que el canal miraba, y te digo de frente que mirar eso no te hace ganar — lo que manda es el clima". Un diseño que esconda eso traiciona el feature. Tu trabajo es hacer que esa honestidad sea legible, cálida y digna — no un castigo, no una letra chica.

---

## 3. El usuario

El usuario real es **el papá del dueño del proyecto**. Perfil:

- **Lector mayor.** Requisitos concretos y NO opcionales: tipografía **≥18px** para cuerpo, contraste **AA**. Targets táctiles grandes (mano que puede temblar).
- **No es trader profesional.** No quiere un terminal con 40 indicadores. No lee jerga. Necesita prosa digerible, hechos en lenguaje natural ("está viva y en la parte baja de su rango", **no** "RSI14=38.7, pos_in_30d_range=0.165" a secas).
- **Quiere la vista RICA — pero NO un firehose.** Este matiz es crítico y un brief anterior lo entendió mal. Él **no** es un "lector pasivo" que quiere un resumen mínimo. Quiere la vista completa, rica, narrativa — la "idea de la moneda" con sus capas y su contexto. La distinción: **rica ≠ abrumadora**. Quiere profundidad legible, no un torrente de datos crudos. Riqueza con jerarquía, no densidad sin orden.
- **Necesita honestidad sin susto.** El reto central de diseño: comunicar "esto no te hace ganar, lo que manda es el clima" de una forma que **informe sin regañar y sin matar el interés**. La doctrina lo exige textual: la frase honesta se dice "una vez, sin regañar".

**En una línea:** una vista cálida, legible para ojos mayores, que cuente la idea de cada moneda como una historia de hechos — con el clima del mercado arriba como contexto honesto, la procedencia de la lista clara, y la decisión siempre, explícitamente, en sus manos.

---

## 4. Doctrina anti-veredicto (NO-NEGOCIABLE)

Esto es un **contrato**, no un estilo. En el backend se hace cumplir en tres capas (un prompt de sistema, una lista negra de palabras prohibidas, y un juez automático que rechaza la respuesta si recomienda/rankea/predice/dimensiona). El diseño nunca es la última línea de defensa, pero el diseño **puede romper la doctrina visualmente** aunque el texto sea correcto. Por eso te toca conocerla.

### Qué está PROHIBIDO

**4.1 — Lenguaje de veredicto o valencia.**
Nada de "compra", "vende", "va a subir", "señal de compra", "te conviene", "vale la pena", "la mejor opción". En la cabecera de régimen, además, prohibido el lenguaje de mando con carga: `fuertes`, `manda`, `débil`. Las etiquetas del régimen (`alts` / `mixto` / `btc`) son **etiquetas de inclinación sin carga, como "invierno" / "primavera", NO verbos de mando**.
Hay un test automático que falla si aparece cualquiera de estas palabras (regex literal): `compra | cómpr | buena | score | recomend | veredicto | potencial | dónde operar`. Diséñalo de modo que tu copy y tus etiquetas nunca caigan ahí.

**4.2 — Modular la moneda por el régimen (la más sutil y la más importante para ti).**
Aunque el clima esté "a favor de alts", el régimen **NO puede alterar color, orden, énfasis ni texto** de la lista de monedas ni de la moneda individual. Textual del spec:

> "el estado del régimen NO altera color/orden/énfasis de la lista de coins."

En cristiano: la cabecera de régimen vive arriba, una sola vez. Las tarjetas/secciones de moneda de abajo **no se ponen verdes** porque el mercado esté caliente. El marco de régimen *enmarca* (Pieza 1, §5), pero no *tiñe* la moneda. Cuidado especial: en la Pieza 1 vas a hacer que el régimen "domine" visualmente y que la moneda viva "dentro" del clima — eso es marco, contexto, jerarquía. Lo que NO puedes hacer es que un régimen alts pinte la moneda de color de éxito, suba su tamaño, le ponga un badge positivo, o cambie su orden. El marco es permanente y neutral respecto al veredicto de la moneda.

**4.3 — Prometer retorno.**
Cero afirmaciones de rendimiento. El objeto no se llama "setup de corrección" ni dice que el canal "cazaba" monedas — esas palabras contrabandean éxito. Se nombra por procedencia: "el filtro que usaba el canal de 2019", y **siempre** acompañado del resultado medido.

**4.4 — Sin score, sin ranking, sin "cuarta línea de síntesis".**
Nada de un puntaje 0–100, nada de un ranking oculto (la lista se ordena por liquidez, orden neutral — ver §7), y nada de una "línea de síntesis" que junte los hechos de las tres lentes en un veredicto implícito. Tres lentes que se quedan tres lentes; nunca una conclusión.

### La "costura" honesta (frases textuales que YA existen — respétalas)

La "costura" es la frase visible, obligatoria, que mantiene todo del lado de la observabilidad: recuerda de dónde salen los números y de quién es la decisión. Está shippeada en el código. **Estas frases son verbatim — no las reescribas, no las suavices, no las muevas al pie en letra chica. Diséñalas para que respiren.**

Costura per-coin (bloque "jugada"):
> "Esto sale de tus niveles · la decisión es tuya."

Mensaje fijo del copiloto cuando alguien pide un veredicto:
> "No te digo si comprar ni cuál es mejor — te leo los hechos de las tres lentes y la decisión es tuya."

Costura del chrome del flujo (tagline de marca):
> "los hechos, lente por lente — la decisión es tuya."

### AC7 — el criterio de honestidad NO-NEGOCIABLE

AC7 obliga a que la costura per-coin **exhiba el hecho medido de que el filtro no le ganó al azar ni siquiera en su mejor clima**. Ya está shippeado, textual:

> "Esto es la réplica del filtro que usaba el canal de 2019. Medido, no le ganó al azar de alts ni en su mejor régimen (alt-bull 2019: 14d 9.92% vs 12.54%). Lo que movió el retorno fue el régimen, no esta selección. La decisión es tuya."

Esos dos números — **9.92% vs 12.54%** — son parte del criterio de aceptación. No son decorativos: son la prueba, en pantalla, de que una lista curada + un régimen "verde" **no se debe leer como señal de compra**. Tu diseño tiene que darles un lugar legible y digno, no enterrarlos.

### La frase que ata todo

> "la cabecera de régimen NO valida el setup per-coin."

Es decir: que arriba diga "el mercado se inclina a alts" **no convierte ninguna moneda en una buena compra**. El régimen es clima; la moneda es procedencia documental; ninguno de los dos es un veredicto. La frase honesta de la cabecera lo dice en voz alta, sin regañar, y es de presencia requerida (puedes refinar la redacción, no eliminarla):

> "Lo que más mueve el resultado es el régimen del mercado, no la moneda que elijas."

---

## 5. El encargo (las 3 piezas)

SP3 es un rediseño profundo de la vista per-coin ("idea de moneda") de Valles. Tres piezas. Las tres se entregan como mockups; otro equipo las implementa 1:1.

### Pieza 1 — El régimen como MARCO PERSISTENTE

Hoy la cabecera de régimen (la pieza "¿es alt-season?") es una banda más, apilada arriba del contenido, fácil de pasar por alto (ver §6.2). El encargo: convertirla en un **marco persistente** que **domina y enmarca** la idea-de-moneda. La moneda vive "dentro" del clima de mercado, y ese clima está **siempre presente** mientras miras la moneda.

Qué tiene que lograr el diseño:

- El régimen domina visualmente: es lo primero que el ojo registra, y queda claro que **el clima manda sobre la moneda**. (Esto encarna la tesis del producto: el clima es lo único que mueve el resultado.)
- El régimen es un **marco**, no un encabezado que se desplaza y desaparece. Tiene que sentirse presente mientras lees la moneda. Tú decides el mecanismo (banda sticky, marco lateral, contenedor envolvente, etc.) — pero la sensación de "estoy mirando esta moneda *dentro* de este clima" tiene que estar.
- **La moneda SIGUE RICA.** No recortes la idea-de-moneda para hacerle espacio al marco. Las ocho secciones actuales (§6.4) siguen ahí, completas. El marco las *enmarca*, no las *reemplaza*.
- **Respeta 4.2:** el marco no tiñe la moneda. Un régimen alts no pone la moneda en color de éxito ni la sube de jerarquía. El marco es contexto neutral.

Los datos del marco están en §7.1 (endpoint `/alt-season`): la inclinación (`alts`/`mixto`/`btc`), los tres componentes (breadth, outperf 30d, dominancia BTC), la frase doctrinal fija, y la frescura de la foto del régimen.

### Pieza 2 — La banda del gráfico (rango de 30 días + marcador de posición)

El gráfico de la moneda es de **velas** (lightweight-charts — ver §10). Hoy tiene capas de "vida", "paredes" y "jugada" (ver §6.6). Una banda vieja —"el valle"— fue eliminada. El encargo: dibujar en su lugar **el rango de 30 días como una banda**, más **un marcador de dónde está el precio dentro de ese rango**.

El dato que manda esto es `pos_in_30d_range` (§7.2): un número de 0.0 a 1.0 donde **0 = el precio está en el piso de su rango de 30 días** y **1 = está en el techo**. Es el único "gate" del filtro: una moneda es candidata solo si está ≤ 0.25 (cuartil inferior).

Qué tiene que lograr el diseño:

- Una **banda** que represente visualmente el rango de los últimos 30 días (el piso y el techo de ese rango) sobre el gráfico de velas.
- Un **marcador** de posición que muestre dónde cae el precio actual dentro de esa banda (cerca del piso = abajo, cerca del techo = arriba).
- **Tratamiento cálido** (paleta de §8 — papel, arcilla, pizarra; nada de neón, nada de verde=sube/rojo=baja como semáforo de valencia).
- Tiene que leerse bien sobre el gráfico de velas sin tapar las velas ni competir con las otras capas (vida / paredes / jugada).
- **Respeta la doctrina:** la banda muestra un hecho geométrico ("el precio está en la parte baja de su rango"). No es "zona de compra". No uses verde/rojo de valencia. El color es para frescura y para marca/dato neutro, no para juicio (ver §8, doctrina de color).

Ojo a la diferencia conceptual: el rango de 30 días (Pieza 2) **no es lo mismo** que las "paredes" de soporte/resistencia (§7.3, capa existente). El rango de 30 días es simplemente el mínimo y el máximo de los últimos 30 días; las paredes son zonas donde el precio rebotó varias veces históricamente. Ambas viven en el gráfico; tienen que distinguirse visualmente.

### Pieza 3 — Pulido de jerarquía + microcopy

Densidad, tipografía y respiración. El encargo: que la **costura honesta** (las frases de §4) y los **hechos** (los números de §7) **respiren**. Que la jerarquía visual de las ocho secciones (§6.4) sea clara: qué es título, qué es contexto, qué es dato, qué es costura. Que el lector mayor pueda recorrer la vista de arriba a abajo sin perderse y sin sentir un muro de texto.

Qué tiene que lograr el diseño:

- Jerarquía tipográfica limpia entre secciones, sub-bloques, hechos y costura (escala de §8).
- Densidad cómoda para ojos mayores: ≥18px de cuerpo, line-height generoso, medida de lectura controlada (≤60–66 caracteres por línea en prosa larga).
- Que la costura no se sienta letra chica ni regaño, pero tampoco grito. Es una verdad serena.
- Microcopy: puedes proponer mejoras de redacción a las etiquetas y micro-textos, **respetando la doctrina (§4) y las frases verbatim que no se tocan (la costura, AC7, la frase del régimen)**. Si propones cambios de texto, márcalos claramente como propuestas.

---

## 6. Cómo se ve HOY

Esto es el estado actual (2026-06-19) que tienes que rediseñar. Wireframes en ASCII + el texto actual citado palabra por palabra. **El texto entre comillas es verbatim del producto vivo** — es tu materia prima, no invención.

Dato estructural que importa: el flujo real son **dos pantallas** dentro de un contenedor (`ValleysFlow`): la **lista** (PickScreen) y la **idea-de-moneda** (IdeaView). SP3 rediseña la idea-de-moneda; la lista se muestra solo como contexto. (No existe una "pantalla de fondo" separada — lo que antes era "FundScreen" hoy vive embebido como la sección "Quién está detrás" dentro de la idea-de-moneda.)

### 6.1 — Contenedor (ValleysFlow) — chrome

- Marca: un solo carácter `V` + el nombre `Valles`.
- Tagline de marca (costura del chrome): `los hechos, lente por lente — la decisión es tuya`
- Estado de carga de la lista: `Cargando la foto…`
- Botón flotante del copiloto (FAB): glifo `◈`, aria `Preguntar al copiloto`. Solo aparece cuando ya hay una moneda elegida.

Orden vertical: barra superior (marca + tagline) → cabecera de régimen (siempre) → escenario (lista o idea-de-moneda) → FAB del copiloto.

### 6.2 — Cabecera de régimen (AltSeasonHeader) — HOY (Pieza 1 la rediseña)

Texto verbatim:
- Error: `El régimen de mercado no está disponible ahora.`
- Cargando: `Cargando el régimen del mercado…`
- Etiquetas de inclinación:
  - alts → `Inclinación del mercado: hacia alts`
  - mixto → `Inclinación del mercado: mixta`
  - btc → `Inclinación del mercado: hacia BTC`
- Componente breadth, vivo: `breadth: {pct}` o `breadth: {pct} (n={n})`
- Componente breadth, muerto: `breadth: sin dato ({razon})` (razón por defecto: `cobertura baja`)
- Componente outperf, vivo: `outperf 30d: {pct}`
- Componente outperf, muerto: `outperf 30d: sin dato`
- Componente dominancia, vivo: `dominancia BTC: {pct}`
- Componente dominancia, muerto: `dominancia: sin dato (fuente caída)`
- Frase doctrinal fija: `Lo que más mueve el resultado es el régimen del mercado, no la moneda que elijas.`
- Frescura: `foto del régimen: {estado}` (estado = `fresco` / `rancio` / `muerto`)
- Cuando un porcentaje es nulo, se muestra `—`. Formato de porcentaje: un decimal (`63.0%`).

Orden actual (vertical, apilado): inclinación → fila de tres componentes → frase doctrinal → frescura. (Hoy es una banda apilada; ver Pieza 1.)

### 6.3 — Lista de candidatas (PickScreen) — CONTEXTO (fuera de alcance de rediseño, ver §12)

Wireframe ASCII de la lista + cabecera (para que entiendas de dónde viene el usuario antes de entrar a la moneda):

```
┌──────────────────────────────────────────────────────────────────────┐
│ [V] Valles   los hechos, lente por lente — la decisión es tuya         │  ← barra superior / marca
├──────────────────────────────────────────────────────────────────────┤
│ CABECERA DE RÉGIMEN  (data: /alt-season → RegimeSnapshot)             │
│  Inclinación del mercado: hacia alts        ← regime.estado            │
│  breadth: 41.2% (n=120)   outperf 30d: -3.1%   dominancia BTC: 54.0%   │  ← regime.componentes
│  Lo que más mueve el resultado es el régimen del mercado, no la moneda  │  ← frase fija
│  que elijas.                                                           │
│  foto del régimen: fresco                    ← frescura.estado         │
├──────────────────────────────────────────────────────────────────────┤
│ LISTA (PickScreen)  (data: candidates, coverage, frescura)            │
│                                                                        │
│  Hoy hay 3 monedas en la parte baja de su rango.   ← h1               │
│  [ foto hace 12 min ]                              ← FreshnessTag     │
│                                                                        │
│  En el cuartil inferior de su rango de 30d — la réplica del filtro     │  ← lead (si hay)
│  que usaba el canal de 2019, mecánico, no un consejo. Elige una para…  │
│                                                                        │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │ Cardano                ● parte baja del rango        $0.4521  →  │  │ ← tarjeta candidata
│  │ ADAUSDT                cuartil inferior (pos 12%) ·              │  │   nombre=humano(symbol)
│  │                        RSI 31                                   │  │   pos, rsi, price
│  └────────────────────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │ Stellar / XLMUSDT      ● parte baja… (pos 18% · RSI 38)  $0.10 →  │  │
│  └────────────────────────────────────────────────────────────────┘  │
│   … (una por candidata)                                                │
│                                                                        │
│  Se miraron 120 de 350 monedas del universo. │ Ordenadas por volumen   │ ← metadatos cobertura
│  — no por preferencia. │ La marca no ordena la lista ni la puntúa.     │
│                                                                        │
│  ¿Buscas otra que no está en la lista?                                 │ ← buscador
│  ⌕ [ escribe su símbolo (ej. SOLUSDT) ___________________ ]            │
└──────────────────────────────────────────────────────────────────────┘
```

Texto verbatim de la lista (contexto):
- Titular con candidatas: `Hoy hay {N} {moneda|monedas} en la parte baja de su rango.`
- Sin candidatas, cobertura completa: `Hoy ninguna en la parte baja de su rango.`
- Cobertura incompleta: `El screener todavía no corrió.`
- Lead: `En el cuartil inferior de su rango de 30d — la réplica del filtro que usaba el canal de 2019, mecánico, no un consejo. Elige una para mirarla de cerca.`
- Tag de tarjeta: `● parte baja del rango`
- Hecho de tarjeta: `cuartil inferior (pos {pos}%) · RSI {rsi}`
- Metadatos (3 segmentos): `Se miraron {evaluated} de {universe} monedas del universo.` · `Ordenadas por volumen — no por preferencia.` · `La marca no ordena la lista ni la puntúa.`
- Buscador: label `¿Buscas otra que no está en la lista?`, glifo `⌕`, placeholder `escribe su símbolo (ej. SOLUSDT)`.

### 6.4 — Idea-de-moneda (IdeaView) — LO QUE SP3 REDISEÑA (8 secciones)

Wireframe ASCII completo de la vista (orden top→bottom):

```
┌──────────────────────────────────────────────────────────────────────┐
│ [V] Valles   los hechos, lente por lente — la decisión es tuya         │  ← barra superior
├──────────────────────────────────────────────────────────────────────┤
│ CABECERA DE RÉGIMEN  (igual que arriba — SIEMPRE presente)            │  ← Pieza 1 la convierte en MARCO
├──────────────────────────────────────────────────────────────────────┤
│ IDEAVIEW (moneda = símbolo elegido)                                    │
│                                                                        │
│ ① [ Vida · Paredes · Jugada · Quién · Noticias ]   ← nav sticky (anclas)│
│                                                                        │
│ ② Cardano  ADAUSDT  [foto hace 12 min]              ← eyebrow + título  │
│    Cardano                                          ← h1 (nombre humano)│
│    ──────                                                              │
│                                                                        │
│ ③ ┌────────────────────────────────────────────────────────────────┐ │
│    │ [Vida (¿viva?·posición)] [Paredes] [La jugada]   ← leyenda clic │ │
│    │                                                                  │ │  GRÁFICO (alto 520px)
│    │  viva · pos 14% del rango 30d            ───── techo · $0.55 ·…  │ │  velas + capas:
│    │                              ░░ ZONA DE ENTRADA $0.44–$0.46 ░░   │ │   - vida (ValleyEval)
│    │   ╓ velas cálidas ╖          ── salida 1 $0.55 60% · 3 toques    │ │   - paredes (zonas S/R)
│    │                              ── stop $0.41                       │ │   - jugada (plan)
│    │                              ▸ ahora $0.452                      │ │   - precio vivo
│    │  runner · 20% abierto ↑      ───── piso · $0.42 · 4 toques       │ │  ← PIEZA 2 añade aquí
│    └────────────────────────────────────────────────────────────────┘ │     la banda de 30d + marcador
│    (estados: "Cargando las velas…" / "No se pudieron cargar las velas")│
│                                                                        │
│ ④ NARRATIVA (3 sub-bloques de prosa)                                   │
│    ¿Está viva?                                       ← #idea-vida       │
│      Está viva y en la parte baja de su rango de 30d (pos 14%)…        │
│      [costura: 2019 / no le ganó al azar / régimen / la decisión es tuya]│
│    ¿Dónde está entre sus paredes?                    ← #idea-paredes    │
│      El precio está sobre un piso que ya giró 4 veces…                 │
│      • Techo más cercano: $0.55, queda 18% más arriba. Ya rebotó 3…   │
│      • Piso más cercano: $0.42, queda 4% más abajo. Ya rebotó 4…      │
│    Si decides entrar, la jugada                      ← #idea-jugada     │
│      • Zona de entrada: $0.44–$0.46, rebotó 4 veces.                  │
│      • Stop: $0.41, justo debajo del piso de $0.42.                   │
│      • Escalera de salidas: 2 peldaños (60% a $0.55 / 40% a $0.62).   │
│      • Runner: un 20% queda abierto sin objetivo…                     │
│      Esto sale de tus niveles · la decisión es tuya.   ← costura       │
│                                                                        │
│ ⑤ Tu jugada ahora                                    ← lifecycle CTA    │
│    [una rama según estado]:                                            │
│     • EN CURSO:  "Jugada en curso · [foto …]"  + hechos[]             │
│     • INCIERTO:  "Jugada incierta… revisa en Binance"                 │
│     • PLAN LISTO: "El plan está listo…"  [ Fijar esta jugada ]         │
│     • FIJADA:    "Jugada fijada — se sigue en vivo."                   │
│     • CERRADA:   titular + campos[ ✓/○/· ]                            │
│     • SIN PLAN:  "No hay plan calculado ahora mismo…"                  │
│                                                                        │
│ ⑥ Quién está detrás                                  ← dossier          │
│    [una rama]:                                                         │
│     • cargando: "Buscando quién está detrás…"                         │
│     • error: "No se pudo averiguar ahora" [↻ Intentar…]              │
│     • opaco: "No se encontró quién está detrás"                       │
│     • rastreable: ☻ Se sabe quién está detrás                         │
│        ☻ {nombre · rol}  fuente                                       │
│        ● Sitio web · activo  fuente   ● GitHub · inactivo  fuente     │
│                                                                        │
│ ⑦ Lo último que se dijo                              ← noticias         │
│    "Las noticias de esta moneda aún no están conectadas."  (vacío)    │
│                                                                        │
│ ⑧ [ Mirar otra moneda ]                              ← footer            │
└──────────────────────────────────────────────────────────────────────┘
   FAB ◈ flotante (abajo-derecha) → abre el copiloto (panel lateral)
```

Las 8 secciones, en orden: ① nav sticky (anclas) → ② cabecera de la moneda (eyebrow + título) → ③ gráfico → ④ narrativa (prosa, 3 sub-bloques) → ⑤ "Tu jugada ahora" (lifecycle interactivo) → ⑥ "Quién está detrás" (dossier) → ⑦ noticias (vacío honesto) → ⑧ footer.

Texto verbatim de IdeaView:
- Nav (5 anclas, separadas por `·`): `Vida` · `Paredes` · `Jugada` · `Quién` · `Noticias`. Aria del nav: `Secciones de la moneda`.
- Título: nombre humano del símbolo (ver §6.7 el mapa de nombres).
- Carga del gráfico: `Cargando el gráfico…`
- Footer: botón `Mirar otra moneda`.

### 6.5 — Narrativa (sección ④) — prosa, 3 sub-bloques

**Bloque "¿Está viva?"** (h3, ancla `#idea-vida`):
- no disponible: `No se pudo revisar el estado de la moneda ahora. Puede ser un problema de la herramienta — intenta de nuevo en un momento.`
- no candidata: `No está en la parte baja de su rango ahora.`
- candidata: `Está viva y en la parte baja de su rango de 30d (posición {pos}%), por debajo de su SMA20 ({vsSma}%), RSI {rsi}.` (en negrita: "parte baja de su rango de 30d", pos, vsSma, rsi)
- costura (siempre que es candidata) — **VERBATIM, AC7, no se toca:** `Esto es la réplica del filtro que usaba el canal de 2019. Medido, no le ganó al azar de alts ni en su mejor régimen (alt-bull 2019: 14d 9.92% vs 12.54%). Lo que movió el retorno fue el régimen, no esta selección. La decisión es tuya.`

**Bloque "¿Dónde está entre sus paredes?"** (h3, ancla `#idea-paredes`):
- no disponible: `No se pudieron calcular los niveles en este momento. Prueba de nuevo en un rato.`
- sin zonas: `Todavía no hay paredes claras: el precio no giró suficientes veces en ningún lugar como para marcar una pared.` + (si hay precio) ` Hoy vale ${precio}.`
- sobre un soporte: `El precio está sobre un piso que ya giró {toques} veces — zona histórica de compradores.`
- contra una resistencia: `El precio está contra un techo que ya giró {toques} veces — zona donde el precio suele frenarse.`
- en el medio: `El precio está en el medio — no pegado a ninguna pared todavía.`
- (si hay precio): ` Precio actual: ${precio}.`
- techo/piso: `Techo más cercano: ${centro}, queda {dist}% más arriba.` + (si aplica) ` Ya rebotó {toques} veces ahí.` / `Piso más cercano: ${centro}, queda {dist}% más abajo.` + (si aplica) ` Ya rebotó {toques} veces ahí.`

**Bloque "Si decides entrar, la jugada"** (h3, ancla `#idea-jugada`):
- sin plan: `Todavía no hay un plan calculado para esta moneda. Puede que falten niveles o que la moneda no esté en condición de entrada ahora.` + costura.
- con plan (4 ítems):
  - `Zona de entrada: zona ${bajo}–${alto}, donde el precio ya rebotó {toques} veces.` (o `No se identificó una zona de soporte nítida.`)
  - `Stop: ${sl_plan}` + (si hay piso) `, justo debajo del piso de ${sl_piso}` + `. Es lo máximo que estás dispuesto a perder en esta jugada.`
  - `Escalera de salidas:` → corta (1 peldaño): `Solo hay una pared clara arriba — una salida ({pct}) en ${tp}.` → multi: `{N} peldaños ({pct} a ${tp} / ...). La primera salida es la más grande — sales más donde la pared está más cerca.`
  - `Runner: un {pct} queda abierto sin objetivo. Cuando se llena la primera salida, su stop sube a break-even — a partir de ahí esa parte ya no puede perder.`
- costura obligatoria (siempre) — **VERBATIM, no se toca:** `Esto sale de tus niveles · la decisión es tuya.`

### 6.6 — Gráfico (sección ③) — velas + capas — HOY

- Leyenda (botones clicables, arriba-izquierda):
  - `vida` → `Vida (¿viva? · posición)`
  - `paredes` → `Paredes`
  - `jugada` → `La jugada` (deshabilitado si no hay plan)
  - Aria del grupo: `capas del gráfico`.
- Cargando: `Cargando las velas…`
- Sin datos: `No se pudieron cargar las velas de esta moneda.`
- Sello de vida (sobre el gráfico): `viva · pos {N}% del rango 30d` (o `sin actividad` si no viva/candidata).
- Tag por pared: `{techo|piso} · ${centro} · {toques} toques`
- Overlays de jugada: runner `runner · {pct}% abierto ↑`; zona `ZONA DE ENTRADA` + `${bajo}–${alto}`; peldaño `llena`/`salida {i+1}` + `${precio}` + `{pct}%` + (si hay) ` · {toques} toques`; stop `break-even`/`stop` + `${precio}`; precio vivo `precio de ahora ${precio}` (fuera de zona) o `ahora ${precio}`.
- Hueco honesto (escalera corta): `Arriba del primer techo no hay más paredes claras. La escalera queda corta — no se inventan techos.`
- Regla de anti-colisión: si dos etiquetas quedan a menos de 16px, se suprime la etiqueta (la línea siempre se dibuja).

Nota para Pieza 2: el sello de vida ya muestra `pos {N}% del rango 30d`. La Pieza 2 le da forma gráfica a ese rango (la banda + marcador), conviviendo con las capas de vida/paredes/jugada.

### 6.7 — "Tu jugada ahora" (sección ⑤) — lifecycle interactivo — texto verbatim

- En curso: `Jugada en curso` o `Jugada incierta` ("en curso"/"incierta" en negrita) + sello de frescura precedido de ` · `.
- Si incierto: `El sistema no está seguro de dónde está la jugada — revisa en Binance.`
- Lista de hechos del plan (uno por línea — ver §7.4 textos posibles).
- Plan listo, sin fijar: `El plan está listo. Si decides entrar, fija la jugada y el sistema la sigue en vivo.` + botón CTA.
- Botón CTA: `Fijar esta jugada` (reposo) / `Fijando…` (enviando).
- Error CTA: `No se pudo fijar la jugada. Intenta de nuevo en un momento.`
- Fijada OK: `Jugada fijada — se sigue en vivo.`
- Cerrada: titular (texto backend) + lista de campos, cada uno con icono `✓` (sí) / `○` (no) / `·` (dato) seguido de `{etiqueta}: {valor}`.
- Sin plan: `No hay plan calculado ahora mismo. Puedes revisar los bloques de arriba para ver el estado actual.`

### 6.8 — "Quién está detrás" (sección ⑥) — dossier — texto verbatim

- Cargando: `Buscando quién está detrás…`
- Error / no disponible: título `No se pudo averiguar ahora`, sub `Falló la búsqueda. Es un problema de la herramienta, no del proyecto.` + botón `↻ Intentar de nuevo`. (icono `×`, tono mute)
- Opaco: título `No se encontró quién está detrás`, sub `Se buscó equipo, presencia y actividad pública, y no apareció nada. Eso es un dato sobre el proyecto, no una falla de la herramienta.` + (si hay) ` No se halló en: {lista}.` (icono `◍`, tono ocre)
- Rastreable: lead `Se sabe quién está detrás`, say `Hay nombres y canales públicos, y cada dato se puede comprobar en su fuente.` (icono `☻`)
  - Personas: cara `☻` + `{nombre} · {rol}` (rol solo si hay) + enlace `fuente`.
  - Canales: punto de color según actividad + `{ETIQUETA} · {activo|inactivo|sin confirmar}` + enlace `fuente`. Etiquetas: `sitio_web`→`Sitio web`, `github`→`GitHub`, `twitter`→`Redes (X)`, `telegram_discord`→`Telegram`, `whitepaper`→`Documento técnico`.

### 6.9 — Noticias (sección ⑦) — vacío honesto

- h3: `Lo último que se dijo`
- Vacío: `Las noticias de esta moneda aún no están conectadas.`

### 6.10 — Copiloto (panel lateral) — texto verbatim

- Header: avatar `◈`, nombre `Copiloto · Valles`, subtítulo `exhibe los hechos · no decide`, cierre `×`.
- Saludo inicial: tag `fact` + `Te leo hechos: si está viva, dónde está el precio respecto a sus paredes, y quién está detrás con su fuente. Pregúntame por cualquiera.`
- Chips de sugerencia: `¿Qué quiere decir "parte baja del rango"?` · `¿Está vieja la información?` · `¿Cuál conviene comprar?` · `¿Cuánto pongo?`
- Placeholder: `pregunta en tus palabras…`
- Enviar: `↑`.
- Tags de respuesta: `fact` (normal) o `no decide` (cuando rechaza un veredicto).

(El copiloto no es foco de SP3 pero es parte del chrome; manténlo coherente con el rediseño.)

### 6.11 — Frescura (FreshnessTag) — átomo compartido — texto verbatim

- muerto: `sin foto — el screener aún no ha completado un ciclo`
- rancio: `foto {hace X} · rancia`
- fresco: `foto {hace X}`
- Edad: `hace {N} min` (<1h) / `hace {N} h` (<48h) / `hace {N} dias` (≥48h — sin tilde en "dias").
- Si no hay frescura → no se muestra nada.

### 6.12 — Mapa de nombres humanos (cómo se muestra cada símbolo)

ADAUSDT→Cardano · XLMUSDT→Stellar · RUNEUSDT→THORChain · PENDLEUSDT→Pendle · JUPUSDT→Jupiter · UNIUSDT→Uniswap · INJUSDT→Injective · GMXUSDT→GMX · BTCUSDT→Bitcoin · PYTHUSDT→Pyth · ZBCUSDT→Zebec. Si no está en el mapa: el símbolo sin "USDT".

---

## 7. Los datos que el diseño mostrará

Esta es la referencia completa de cada número que va a aparecer en pantalla: qué significa en palabras simples, su rango y un ejemplo real. Cinco fuentes de datos alimentan la vista. **Todos los datos del servidor traen un bloque `frescura`** (salud del dato, §7.6).

Regla transversal de doctrina (repetida porque importa): estos son **hechos descriptivos**, nunca un "compra / no compra". Que una moneda sea "candidata" NO es una recomendación — es la réplica de un filtro de 2019 que el propio sistema documenta que no tiene ventaja probada.

### 7.1 — Régimen ("¿es alt-season?") — `/alt-season` (alimenta la Pieza 1)

Es un **hecho de mercado, no un consejo**. Dice hacia dónde se inclina el mercado entero: hacia las alts, mixto, o hacia BTC. Se decide por votación de 3 componentes. Es global (igual para todos).

Respuesta completa (RegimeSnapshot):

| Campo | Tipo | Qué significa | Ejemplo |
|---|---|---|---|
| `generated_at` | fecha ISO o nulo | Cuándo se calculó la foto. Nulo = nunca corrió | `"2026-06-19T08:30:00+00:00"` |
| `coverage.universe` | entero | Cuántas monedas hay en el universo a evaluar | `218` |
| `coverage.evaluated` | entero | Cuántas se lograron evaluar (la red puede fallar) | `214` |
| `coverage.complete` | booleano | ¿Se evaluaron todas? | `false` |
| `dominancia_fetch.ok` | booleano | ¿Se pudo traer la dominancia de BTC? | `true` |
| `dominancia_fetch.fetched_at` | fecha o nulo | Cuándo se trajo la dominancia | `"2026-06-19T08:30:12+00:00"` |
| `dominancia_fetch.source` | texto | De dónde salió | `"coingecko/global"` |
| `regime` | objeto | El veredicto del régimen (abajo) | — |
| `frescura` | objeto | Salud del dato (§7.6) | — |

El veredicto (`regime`):

| Campo | Tipo | Qué significa | Rango | Ejemplo |
|---|---|---|---|---|
| `estado` | texto | El régimen ganador por votos | `alts` \| `mixto` \| `btc` | `"alts"` |
| `componentes` | mapa de 3 | Los 3 jueces (abajo). Llaves: `breadth50`, `outperf_30d`, `dominancia_btc` | — | — |
| `votos.alts` | entero | Cuántos componentes votaron "alts" | 0–3 | `2` |
| `votos.neutral` | entero | Cuántos votaron neutral | 0–3 | `0` |
| `votos.btc` | entero | Cuántos votaron "btc" | 0–3 | `1` |
| `votos.vivos` | entero | Cuántos componentes votaron (los muertos no votan) | 0–3 | `3` |
| `n_alts_evaluadas` | entero | Cuántas altcoins entraron en el cálculo | conteo | `213` |

Regla de decisión (para que entiendas la lógica): si votan menos de 2 componentes vivos → `mixto`. Si "alts" gana por mayoría → `alts`. Igual para `btc`. Empate → `mixto`.

Cada juez (RegimeComponent):

| Campo | Tipo | Qué significa | Ejemplo |
|---|---|---|---|
| `valor` | número o nulo | El número crudo. Nulo si está muerto | `0.63` |
| `lean` | texto o nulo | Hacia dónde inclina este juez | `"alts"` \| `"neutral"` \| `"btc"` \| nulo |
| `estado` | texto | ¿Vivo o muerto? | `"fresco"` \| `"muerto"` |
| `n` | entero (opcional) | Cuántas monedas alimentaron este juez (solo breadth) | `213` |
| `razon` | texto (opcional) | Por qué está muerto | `"cobertura_baja"` \| `"sin_datos"` |

Los 3 jueces en palabras:
- **breadth50** (*amplitud*): qué fracción de las altcoins está por encima de su media de 50 días. Rango 0.0–1.0. Inclina a alts si ≥ 0.60, a btc si ≤ 0.40. Ej. `0.63` → "63% de las alts están por encima de su media de 50d".
- **outperf_30d** (*alt vs BTC*): la mediana de cuánto le sacaron las alts a BTC en 30 días. Fracción decimal: `0.08` = 8 puntos. Inclina a alts si ≥ 0.05, a btc si ≤ −0.05.
- **dominancia_btc** (*dominancia de BTC*): fracción del mercado total que es BTC. Rango ~0.40–0.65. **Ojo: aquí MENOR = alts.** Inclina a alts si ≤ 0.50, a btc si ≥ 0.58. Ej. `0.485`.

Payload de ejemplo (régimen sano, inclinación a alts):
```json
{
  "generated_at": "2026-06-19T08:30:00+00:00",
  "coverage": { "universe": 218, "evaluated": 214, "complete": false },
  "dominancia_fetch": { "ok": true, "fetched_at": "2026-06-19T08:30:12+00:00", "source": "coingecko/global" },
  "regime": {
    "estado": "alts",
    "componentes": {
      "breadth50":      { "valor": 0.63,  "lean": "alts",    "estado": "fresco", "n": 213 },
      "outperf_30d":    { "valor": 0.082, "lean": "alts",    "estado": "fresco" },
      "dominancia_btc": { "valor": 0.555, "lean": "neutral", "estado": "fresco" }
    },
    "votos": { "alts": 2, "neutral": 1, "btc": 0, "vivos": 3 },
    "n_alts_evaluadas": 213
  },
  "frescura": { "estado": "fresco", "edad_seg": 1820.4, "generated_at": "2026-06-19T08:30:00+00:00", "umbral_seg": 43200 }
}
```
Componente muerto (CoinGecko cayó): `"dominancia_btc": { "valor": null, "lean": null, "estado": "muerto" }`.

### 7.2 — Estado de una moneda (vida + posición en el rango) — `/valley-eval/{sym}` (alimenta vida + Pieza 2)

Evalúa una moneda en vivo: ¿está viva y operable? ¿está en la parte baja de su rango? Hechos descriptivos, nunca "compra/no compra".

| Campo | Tipo | Qué significa | Rango / unidad | Ejemplo |
|---|---|---|---|---|
| `symbol` | texto | El par evaluado | mayúsculas | `"INJUSDT"` |
| `estado` | texto | ¿Se pudo evaluar? | `"ok"` \| `"no_disponible"` | `"ok"` |
| `candidata` | booleano | ¿Pasó el filtro (viva + parte baja del rango)? | true/false | `true` |
| `vivo` | booleano | ¿Está viva mecánicamente? (solo cuando NO es candidata) | true/false | `false` |
| `razones_muerte` | lista de textos | Por qué está muerta (solo si no candidata) | ver abajo | `["volumen_bajo_piso"]` |
| `price` | número | Último precio de cierre diario | USD | `21.34` |
| `pos_in_30d_range` | número | **El único gate y el dato de la Pieza 2.** Dónde está el precio en su rango de 30d: 0.0 = piso, 1.0 = techo. Candidata solo si ≤ 0.25 | 0.0–1.0 | `0.18` |
| `rsi14` | número | RSI de 14 días (fuerza/agotamiento). <30 sobreventa, >70 sobrecompra. Hecho mostrado, no gate | 0–100 | `38.6` |
| `pct_vs_sma20` | número | % por encima/debajo de la media de 20 días. Negativo = bajo la media | % | `-4.2` |
| `pct_vs_sma50` | número | % respecto a la media de 50 días | % | `-11.7` |
| `consol_30d` | número | Ancho del rango de 30d como % de su precio mediano. Más bajo = más apretado | % | `22.5` |
| `vol_ratio` | número | Volumen de los últimos 3 días vs los últimos 30. 1.0 = normal | razón (×) | `1.34` |
| `drawdown_from_90h` | número | Cuánto cayó desde el máximo de 90 días. Siempre ≤ 0 | % (negativo) | `-28.4` |
| `volumen_usd_dia` | número | Liquidez: mediana del volumen diario en USD (30d) | USD | `4850000.0` |
| `distancia_ath_pct` | número | Qué tan lejos del máximo histórico. Fracción 0–1 | 0.0–1.0 | `0.74` |
| `razones_vida` | lista de textos | Señales de muerte que NO dispararon (normalmente vacía) | — | `[]` |
| `frescura` | objeto | Salud del dato. Umbral 60 s | — | — |

Valores de `razones_muerte` (cada uno un hecho mecánico): `historia_insuficiente` (menos de 120 días de velas), `volumen_bajo_piso` (mediana 30d < $500k/día), `volumen_agonizante` (el volumen reciente cayó a menos de la mitad), `velas_planas` (más de la mitad de las velas recientes casi sin movimiento — libro muerto).

Ejemplo — candidata:
```json
{
  "symbol": "INJUSDT", "estado": "ok", "candidata": true,
  "generated_at": "2026-06-19T08:42:10+00:00",
  "price": 21.34, "pos_in_30d_range": 0.18, "rsi14": 38.6,
  "pct_vs_sma20": -4.2, "pct_vs_sma50": -11.7, "consol_30d": 22.5,
  "vol_ratio": 1.34, "drawdown_from_90h": -28.4, "volumen_usd_dia": 4850000.0,
  "distancia_ath_pct": 0.74, "razones_vida": [],
  "frescura": { "estado": "fresco", "edad_seg": 0.4, "generated_at": "2026-06-19T08:42:10+00:00", "umbral_seg": 60 }
}
```
Ejemplo — viva pero NO candidata (está arriba en su rango): `{ "symbol": "XYZUSDT", "estado": "ok", "candidata": false, "vivo": true, "razones_muerte": [], ... }`.
Ejemplo — muerta: `{ "symbol": "DEADUSDT", "estado": "ok", "candidata": false, "vivo": false, "razones_muerte": ["volumen_bajo_piso", "velas_planas"], ... }`.
Ejemplo — Binance no respondió: `{ "symbol": "FOOUSDT", "estado": "no_disponible", "frescura": { "estado": "muerto", "edad_seg": null, "generated_at": null, "umbral_seg": 60 } }`.

**Nota de diseño:** cuando `estado` es `no_disponible`, todos los números (price, rsi14, etc.) están **ausentes** — no vienen como cero ni nulo, simplemente no están. La UI necesita un estado vacío honesto, no campos en blanco.

### 7.3 — Las paredes (soporte y resistencia) — `/levels/{sym}` (capa "paredes" + las velas del gráfico)

Detecta dónde el precio rebotó en el pasado y los agrupa en **zonas** (paredes). Dice además dónde está el precio ahora: dentro de una zona, con techo arriba, con piso abajo. Hechos geométricos, sin consejo. Usa un año de velas diarias.

Respuesta (SrLevels):

| Campo | Tipo | Qué significa | Ejemplo |
|---|---|---|---|
| `symbol` | texto | El par | `"INJUSDT"` |
| `estado` | texto | ¿Se pudo calcular? | `"ok"` \| `"no_disponible"` |
| `generated_at` | fecha o nulo | Cuándo se calculó | `"2026-06-19T08:45:00+00:00"` |
| `price_live` | número o nulo | Precio vivo en el momento del request | `21.34` |
| `zonas` | lista de zonas | Las paredes, ordenadas por centro ascendente | — |
| `ubicacion` | objeto | Dónde está el precio respecto a las zonas | — |
| `candles` | lista (opcional) | Velas diarias para el gráfico (`time` en segundos UTC, `open`/`high`/`low`/`close`) | — |
| `frescura` | objeto | Salud del dato. Umbral 60 s | — |

Cada pared (SrZona): `tipo` (`"resistencia"`=techo \| `"soporte"`=piso), `precio_bajo`/`precio_alto` (los bordes de la banda en USD), `centro` (precio central), `toques` (cuántas veces el precio defendió la zona, mínimo 2), `confluencia_redondo` (números redondos notables dentro de la banda, ej. `[20.5]`).

Ubicación (`ubicacion`): `dentro_de` (la zona que contiene el precio ahora, o nulo), `techo` (la pared inmediata arriba: `{ centro, dist_pct }`), `piso` (la pared inmediata abajo). `dist_pct` = % de distancia desde el precio vivo (positivo si arriba, negativo si abajo).

Ejemplo:
```json
{
  "symbol": "INJUSDT", "estado": "ok", "generated_at": "2026-06-19T08:45:00+00:00", "price_live": 21.34,
  "zonas": [
    { "tipo": "soporte",     "precio_bajo": 18.90, "precio_alto": 19.40, "centro": 19.15, "toques": 3, "confluencia_redondo": [19.0] },
    { "tipo": "soporte",     "precio_bajo": 20.10, "precio_alto": 20.85, "centro": 20.40, "toques": 4, "confluencia_redondo": [20.5] },
    { "tipo": "resistencia", "precio_bajo": 24.00, "precio_alto": 24.70, "centro": 24.35, "toques": 5, "confluencia_redondo": [24.0] },
    { "tipo": "resistencia", "precio_bajo": 28.50, "precio_alto": 29.20, "centro": 28.80, "toques": 2, "confluencia_redondo": [29.0] }
  ],
  "ubicacion": { "dentro_de": null, "techo": { "centro": 24.35, "dist_pct": 14.10 }, "piso": { "centro": 20.40, "dist_pct": -4.41 } },
  "candles": [ { "time": 1718668800, "open": 22.10, "high": 22.45, "low": 21.05, "close": 21.34 } ],
  "frescura": { "estado": "fresco", "edad_seg": 0.5, "generated_at": "2026-06-19T08:45:00+00:00", "umbral_seg": 60 }
}
```
No disponible: `zonas` es `[]`, `price_live` es `null`, y `ubicacion` trae las tres claves en `null`.

### 7.4 — La jugada (el plan vivo) — `/plan/{sym}` (sección ⑤ + capa "jugada")

Convierte las paredes en un plan: dónde entrar, dónde poner el stop, una escalera de salidas (peldaños) en las resistencias, y un "runner" (fracción que se deja correr sin objetivo). Luego compara el plan con lo observado en la cuenta. Es por-usuario.

Respuesta (PlanLive):

| Campo | Tipo | Qué significa | Ejemplo |
|---|---|---|---|
| `symbol` | texto | El par | `"INJUSDT"` |
| `estado_vivo` | texto o nulo | **Nulo = no hay plan activo** (la UI muestra "sin plan") | `"activo"` \| `"incierto"` \| `"cerrado"` \| `null` |
| `plan` | objeto | El plan derivado (abajo) | — |
| `realidad` | objeto | Cómo va el plan contra lo observado (abajo) | — |
| `hechos` | lista de textos | Frases-hecho en español, nunca instrucciones | — |
| `frescura` | objeto | Salud del dato. Umbral 900 s (15 min) | — |

El plan (`plan`): `entry` (precio de entrada, USD), `sl_plan` (stop-loss, puesto 1% bajo el borde del soporte inmediato), `sl_piso` (la zona de soporte que fija el stop), `rungs` (la escalera de tomas de ganancia, máx 4, en resistencias sobre la entrada), `runner_frac` (fracción que se deja correr sin objetivo, 0.0–1.0), `entry_zone` (la zona de soporte donde cae la entrada).
Cada peldaño (`rungs[i]`): `tp_price` (precio objetivo = centro de una resistencia), `size_frac` (qué fracción se cierra ahí; el primero siempre ≥ 0.50; reparto base 0.50 / 0.20 / 0.15 / 0.10), `zona` (la resistencia de donde sale).

La realidad (`realidad`): `fase` (`"PLANNED"` \| `"CONFIRMED"` \| `"RUNNING"` \| `"CLOSED"`), `rungs_llenos` (índices de peldaños ya tocados, 0 = el primero), `sl_actual` (dónde está el stop ahora; puede haber subido a break-even), `be_movido` (¿ya se movió a break-even?), `size_restante_frac` (qué fracción sigue abierta).

`hechos` — frases posibles (verbatim, hechos nunca órdenes): `"transición sin confirmar — revisa en Binance"`, `"TP1 se llenó"` / `"TP2 se llenó"`…, `"tu SL está en break-even"`, `"tu SL sigue debajo de la zona"`, `"tu SL está por encima del nivel del plan"`.

Ejemplo — plan activo, primer peldaño tocado, stop en break-even:
```json
{
  "symbol": "INJUSDT", "estado_vivo": "activo",
  "plan": {
    "entry": 21.34, "sl_plan": 18.71,
    "sl_piso": { "centro": 19.15, "precio_bajo": 18.90, "precio_alto": 19.40, "toques": 3 },
    "rungs": [
      { "tp_price": 24.35, "size_frac": 0.523, "zona": { "centro": 24.35, "precio_bajo": 24.00, "precio_alto": 24.70, "toques": 5 } },
      { "tp_price": 28.80, "size_frac": 0.209, "zona": { "centro": 28.80, "precio_bajo": 28.50, "precio_alto": 29.20, "toques": 2 } }
    ],
    "runner_frac": 0.05, "entry_zone": null
  },
  "realidad": { "fase": "RUNNING", "rungs_llenos": [0], "sl_actual": 21.34, "be_movido": true, "size_restante_frac": 0.477 },
  "hechos": ["TP1 se llenó", "tu SL está en break-even"],
  "frescura": { "estado": "fresco", "edad_seg": 320.0, "generated_at": "2026-06-19T08:39:40+00:00", "umbral_seg": 900 }
}
```
Sin plan: `{ "symbol": "INJUSDT", "estado_vivo": null }` — y **nada más** (no hay `plan`, `realidad`, `hechos` ni `frescura`). Estado vacío puro: "sin plan".

**Espejo de conducta** (tras un cierre, `/plan/{sym}/conducta`): **sin PnL** (sin ganancia/pérdida). Devuelve `{ symbol, estado_vivo: "cerrado" | null, titular, campos[] }`. Cada campo es `{ k: etiqueta, ok: "si"|"no"|"dato", v?: valor }`. Etiquetas: "Entraste en la zona", "Respetaste el stop", "Moviste a break-even", "Honraste los peldaños", "Cerraste según el plan", "Cuánto aguantaste" (con `v` tipo `"36 h"`). Titular si todo bien: *"Honraste el plan que aprobaste."* Si no: *"Esta vez te saliste del plan. Sin reproche — solo el espejo."*

### 7.5 — El dossier (quién está detrás) — `/dossier/{sym}` (sección ⑥)

Due-diligence de un proyecto: equipo, presencia en canales, actividad, financiación, hitos. **Hechos citados con fuente, sin veredicto.** Global, cacheado 7 días.

| Campo | Tipo | Qué significa | Ejemplo |
|---|---|---|---|
| `symbol` | texto | El proyecto | `"INJUSDT"` |
| `equipo` | lista | Personas identificadas | — |
| `equipo_identificado` | booleano | ¿Se logró identificar al equipo? | `true` |
| `presencia` | mapa | Canales del proyecto por nombre (`web`, `twitter`, `github`, `telegram`, `discord`…) | — |
| `actividad` | mapa | Indicadores de actividad citados | — |
| `financiacion` | lista | Rondas/inversiones con fuente | — |
| `hitos` | lista | Hitos del proyecto | — |
| `estado_general` | texto | Qué tan rastreable es | `"rastreable"` \| `"opaco"` \| `"no_disponible"` |
| `no_encontrado_en` | lista | Dónde se buscó y no se halló nada | `["telegram"]` |
| `generated_at` | fecha o nulo | Cuándo se armó | `"2026-06-19T07:00:00+00:00"` |
| `frescura` | objeto | Salud del dato. Umbral 7 días | — |

Sub-formas: persona `{ nombre, rol, enlaces:[urls], fuente }`; canal `{ url, activo: "si"|"no"|"desconocido", fuente }`; actividad `{ valor, fuente }`; hito `{ descripcion, fecha, fuente }`.

Ejemplo:
```json
{
  "symbol": "INJUSDT",
  "equipo": [ { "nombre": "Eric Chen", "rol": "Co-fundador / CEO", "enlaces": ["https://twitter.com/erichchen"], "fuente": "https://injective.com/team" } ],
  "equipo_identificado": true,
  "presencia": {
    "web":     { "url": "https://injective.com",           "activo": "si",          "fuente": "exa" },
    "twitter": { "url": "https://twitter.com/injective",    "activo": "si",          "fuente": "exa" },
    "github":  { "url": "https://github.com/InjectiveLabs", "activo": "si",          "fuente": "exa" },
    "discord": { "url": null,                               "activo": "desconocido", "fuente": null }
  },
  "actividad": { "github_commits": { "valor": "commits recientes en los últimos 30 días", "fuente": "github.com/InjectiveLabs" } },
  "financiacion": [ { "descripcion": "Ronda de $40M liderada por Jump Crypto", "fecha": "2022-01-01", "fuente": "techcrunch.com/..." } ],
  "hitos": [ { "descripcion": "Mainnet launch", "fecha": "2021-11-08", "fuente": "injective.com/blog" } ],
  "estado_general": "rastreable", "no_encontrado_en": ["telegram"],
  "generated_at": "2026-06-19T07:00:00+00:00",
  "frescura": { "estado": "fresco", "edad_seg": 5400.0, "generated_at": "2026-06-19T07:00:00+00:00", "umbral_seg": 604800 }
}
```
Proyecto opaco: `equipo: []`, `equipo_identificado: false`, `estado_general: "opaco"`.

### 7.6 — Frescura (salud del dato) — en TODOS los endpoints

Es el campo `frescura` en cada respuesta. **No habla del mercado, habla del dato:** ¿el sistema que lo produce sigue vivo y produciendo? `fresco` significa "el cálculo es reciente", NO "la afirmación sigue siendo cierta".

| Campo | Tipo | Qué significa | Ejemplo |
|---|---|---|---|
| `estado` | texto | El semáforo | `"fresco"` \| `"rancio"` \| `"muerto"` |
| `edad_seg` | número o nulo | Cuántos segundos tiene el dato. Nulo si nunca se generó | `1820.4` |
| `generated_at` | fecha o nulo | Cuándo se generó | `"2026-06-19T08:30:00+00:00"` |
| `umbral_seg` | número | El límite: por encima pasa de fresco a rancio | `43200` |

Lógica del semáforo:
- **muerto** — nunca se generó o no se puede leer la marca. El productor está operacionalmente caído. Es distinto de "rancio".
- **rancio** — la edad superó el umbral. Existe pero está viejo.
- **fresco** — la edad está dentro del umbral.

Umbrales por fuente:

| Fuente | umbral | En palabras |
|---|---|---|
| `/alt-season` (régimen) | 43200 | 12 horas |
| `/valley-eval/{sym}` (vida) | 60 | 60 s (vivo cada request) |
| `/levels/{sym}` (paredes) | 60 | 60 s (precio vivo cada request) |
| `/plan/{sym}` (jugada) | 900 | 15 minutos |
| lista de candidatas | 43200 | 12 horas |
| `/dossier/{sym}` | 604800 | 7 días |

**Para ti:** los tres estados de frescura necesitan tratamiento visual distinto. `muerto` no es un error de red ni un vacío silencioso — es "esto debería estar vivo y no lo está", y merece una señal honesta, **no un spinner infinito ni una tabla vacía muda**.

---

## 8. Guía de estilo (tema cálido "papel/editorial")

### Doctrina de color (la regla que ordena toda la paleta)

**El color NUNCA juzga.** Verde/ámbar como semáforo bueno/malo está prohibido. Salvia (sage) y ocre (ochre) se reservan EXCLUSIVAMENTE para frescura temporal (fresco/rancio/muerto). Arcilla (clay) es el único acento de marca; pizarra (slate) es dato neutro. Las tres ramas de cada lente van en neutro (arcilla/pizarra), **nunca en verde=compra**.

### Paleta (tokens CSS + hex exactos — los hex ya están corregidos para AA)

**Superficies (papel cálido):**

| Token | Hex | Uso |
|---|---|---|
| `--paper` | `#F4F0E8` | fondo base de la app |
| `--paper-2` | `#EFE9DD` | fondo de bloques/burbujas/iconos neutros, chips inactivos |
| `--card` | `#FBF9F4` | tarjetas, paneles, dock, nav sticky |
| `--card-2` | `#F6F1E7` | superficie alterna/elevada |
| `--card-edge` | `#E4DCCC` | borde de tarjeta suave |
| `--card-edge-2` | `#D8CDB8` | borde de tarjeta más marcado, separadores |

**Tinta (texto):**

| Token | Hex | Contraste | Uso |
|---|---|---|---|
| `--ink` | `#2A2722` | ~15:1 | texto principal, títulos, números |
| `--ink-2` | `#5C564A` | AA | cuerpo secundario |
| `--ink-3` | `#6E6757` | ~4.6:1 | metadata, claves, frescura corta |
| `--ink-4` | `#6F6856` | ~4.6:1 | placeholders, notas mínimas. Idealmente NO para texto leíble |

**Acento de marca — ARCILLA (clay):**

| Token | Hex | Uso |
|---|---|---|
| `--clay` | `#B8542E` | acento de marca; SOLO fondo/borde/icono (como texto reprueba AA) |
| `--clay-deep` | `#9A4424` | ~5.9:1; la versión TEXTO de arcilla (eyebrow, enlaces, botón outline, foco) |
| `--clay-soft` | `#EDD9CC` | borde de outline, fondo hover de botón outline |
| `--clay-tint` | `#F3E6DC` | fondo de avatar/icono persona, halo de "estás acá" |

**Frescura / temporal (SOLO frescura, no juicio):**

| Token | Hex | Significado |
|---|---|---|
| `--sage` | `#5E7048` | al día (fresco) |
| `--sage-soft` | `#E2E6D4` | — |
| `--sage-tint` | `#ECEFE2` | fondo de chip "fresco" |
| `--ochre` | `#8A5E1C` | atención (rancio) |
| `--ochre-soft` | `#EFE0C4` | — |
| `--ochre-tint` | `#F2E8D4` | fondo de callout de estado viejo |

**Dato neutro frío + extra:**

| Token | Hex | Uso |
|---|---|---|
| `--slate` | `#4C5A66` | dato neutro frío; icono de respuesta, borde-izq de nota neutral |
| `--down` | `#AE4334` | baja / riesgo (uso global) |
| `--down-soft` | `#EFD6CF` | — |
| `--down-tint` | `#F3E3DD` | — |

**Colores literales fuera de token (úsalos tal cual):** borde de callout ocre / burbuja-rechazo `#E4CF9E`; borde de tag/chip salvia "candidata" `#C9D2B2`; texto sobre clay/sage/botón primario/FAB `#fff`; scrim del panel `rgba(42,39,34,0.28)`; halos y sombras con `rgba(42,39,34,*)` y `rgba(184,84,46,*)`.

### Tipografía

Dos familias:
- `--serif`: `'Source Serif 4', Georgia, 'Times New Roman', serif` — preguntas, respuestas, números, nombres.
- `--sans`: `'Instrument Sans', ui-sans-serif, system-ui, sans-serif` — todo lo demás (UI, body, labels).

Tamaños vigentes (de menor a mayor):

| px | Dónde | Familia |
|---|---|---|
| 10.5px | tag candidata, sello de vida | sans |
| 11px | tag de pared, separador de nav | sans |
| 12px | claves de números, conteos de peldaño, tags (mínimo permitido), nav | sans |
| 12.5px | tagline de marca, símbolo del eyebrow, estado de canal, fuente, subtítulo dock, sugerencia | sans |
| 13–13.5px | peldaño, conteo, distancia, frescura del dossier, estado del gráfico, "mirar otra" | sans |
| 14–14.5px | etiqueta de piso, marca, canal, interior de botones, item de leyenda, input del dock | mixto |
| 15–15.5px | nota neutral, hecho de candidata, botón, frescura del header | sans |
| 16px | **mínimo secundario AA**: rol de persona, sub de callout, recap, burbuja, input del buscador, vacío de noticias, costura de narrativa | mixto |
| 18px | **PISO DE CUERPO (lector ~70 años)**: respuestas, hechos, lead de la lista, cuerpo de cierre, nombre de persona, título de callout, cuerpo de narrativa, items de lista, estado y CTA de la jugada. La idea-view declara 18px de base | mixto |
| 19px | nombre de marca, valor de número, nombre de candidata, cara de persona, header de componente | mixto |
| 22–23px | distancia (valor), estado del régimen (22px), iconos | serif/mixto |
| 26px | glifo de icono de respuesta | — |

Títulos con clamp (responsive, ya respetan ≥18px de cuerpo):
- pregunta: `clamp(26px, 4.2vw, 38px)`, serif 500, lh 1.16, letter-spacing −0.012em.
- lead de respuesta: `clamp(22px, 3vw, 28px)`, serif 600, lh 1.22.
- titular de lista: `clamp(28px, 4.6vw, 42px)`, serif 500, lh 1.12.
- título de cierre: `clamp(27px, 4.4vw, 38px)`, serif 500, lh 1.18.
- título de la idea: `clamp(28px, 5vw, 42px)`, serif 500, lh 1.15.

Pesos: títulos serif 500; leads/nombres/números serif 600; labels sans 500–700. Line-height de cuerpo: 1.5–1.65. Labels en mayúsculas con `letter-spacing` 0.04em–0.14em (eyebrow de la moneda = 0.14em). Números con `tabular-nums`.

### Espaciado, anchos, clamps

- Ancho de lectura principal: **660px** (la columna del recorrido, nav interna).
- Medida de texto bajo gráfico ancho: **760px**, centrada.
- Contenedor idea-view: `max-width: min(1240px, 100%)`; barra superior `max-width: 1100px`.
- Párrafos largos: `max-width: 32em` (el spec pide `60ch` en prosa larga).
- Padding horizontal del shell: `clamp(20px, 5vw, 56px)`.
- Padding vertical del escenario: `clamp(16px, 4vh, 48px)` arriba, 130px de cola (deja aire para la nav fija).
- idea-view padding: `clamp(16px, 4vw, 48px) clamp(16px, 3vw, 36px) 80px`.
- Gaps típicos: bloques de narrativa 28px; hechos 13px; recaps/candidatas 10px; lista de narrativa 8px.
- Curva de animación: `cubic-bezier(0.22, 1, 0.36, 1)`; duraciones 140–200ms (micro), 320–620ms (entradas/transiciones de capa).

### Border-radius

- `999px` — pills: botones, chips/tags, buscador, retry, send.
- `50%` — círculos: avatares, dots, marca, número de paso, FAB, cierre.
- `16px` — burbujas de chat (esquina "cola" a 5px).
- `14px` — iconos grandes, cards de candidata/callout.
- `12px` — recaps, nota neutral (floors a 11px).
- `10px` — paneles, bloques de narrativa, placeholders.
- `8px` — botón CTA / "mirar otra", radios chicos (tag de pared, sello de vida a 6px).
- `6px` — sellos pequeños.

### Sombras (todas cálidas, sin glow neón)

Basadas en `rgba(42,39,34,*)` (tinta) o `rgba(184,84,46,*)` (arcilla).
- Card hover candidata: `0 10px 24px -18px rgba(42,39,34,0.45)`.
- FAB: `0 12px 30px -10px rgba(184,84,46,0.6)`.
- Panel lateral: `-24px 0 60px -30px rgba(42,39,34,0.5)`.
- Sombra suave: `0 1px 2px rgba(42,39,34,0.05)`; sombra grande: `0 18px 36px -24px rgba(42,39,34,0.5)`; hover de botón: `0 8px 18px -12px rgba(42,39,34,0.45)`.

### Convenciones

- Tema cálido confinado a la raíz de Valles. Nada de neón, fondos oscuros ni glow. Es papel cálido editorial.
- Glifos Unicode en uso (consistencia): marca `V`, FAB `◈`, buscador `⌕`, retry `↻`, loading `⧖`, caret `▸`, persona `☻`, opaco `◍`, flecha de tarjeta `→`, enviar `↑`, ok `✓`, no `○`, dato `·`.

---

## 9. Estados a mockear

Para cada uno: qué tiene que comunicar. Estos son los estados que tus mockups deben cubrir (no inventes otros; estos son los reales del sistema).

**Por moneda (sección vida / Pieza 2):**
1. **Candidata** (`candidata: true`) — viva y en la parte baja de su rango. Comunica: el hecho descriptivo (posición, RSI), la banda de 30d con el marcador abajo (Pieza 2), y la costura AC7 visible y digna. **No** lo comuniques como "buena compra".
2. **Viva pero NO candidata** (`candidata: false, vivo: true`) — está viva pero arriba en su rango. Comunica: "no está en la parte baja de su rango ahora", neutral, sin drama. La banda de 30d con el marcador alto.
3. **Cargando** — los datos vienen en camino. Comunica espera honesta (no un esqueleto que finja contenido).
4. **No disponible** (`estado: "no_disponible"`) — Binance no respondió; los números están ausentes. Comunica vacío honesto: "no se pudo revisar ahora, es un problema de la herramienta", sin campos en blanco fingiendo datos.

**Frescura (aplica al marco de régimen, a la moneda, a cada dato):**
5. **fresco** — el dato es reciente. Señal discreta (salvia/sage), no celebración.
6. **rancio** — el dato existe pero está viejo (superó el umbral). Señal de atención (ocre), legible, sin alarma.
7. **muerto** — el productor está caído / nunca corrió. Señal honesta de "esto debería estar vivo y no lo está". **No** un spinner infinito ni una tabla vacía muda. (Texto del régimen: "foto del régimen: muerto"; del átomo: "sin foto — el screener aún no ha completado un ciclo".)

**Marco de régimen (Pieza 1):**
8. **Régimen fresco/rancio/muerto** — combina con 5/6/7. El marco siempre presente; cuando la foto está muerta o rancia, dilo en el marco sin esconderlo.
9. **Dominancia muerta** (un componente caído) — `dominancia_btc` muerto mientras los otros dos viven. Comunica: "dominancia: sin dato (fuente caída)" como un hecho neutral; el régimen sigue funcionando con los componentes vivos. (Cada uno de los 3 componentes puede morir independiente; mockea al menos este caso.)

**La jugada (sección ⑤):**
10. **En curso** (`estado_vivo: "activo"`) — hay una jugada viva; muestra los hechos del plan + frescura.
11. **Incierta** (`estado_vivo: "incierto"`) — el sistema no está seguro; "revisa en Binance".
12. **Plan listo, sin fijar** — hay plan calculado pero la persona no ha entrado; CTA "Fijar esta jugada".
13. **Fijada** — recién fijada; "se sigue en vivo".
14. **Cerrada** (`estado_vivo: "cerrado"`) — espejo de conducta, sin PnL; titular + campos con ✓/○/·.
15. **Sin plan** (`estado_vivo: null`) — no hay plan calculado; vacío honesto "No hay plan calculado ahora mismo".

(Opcional, si te sobra tiempo: estados del dossier — rastreable / opaco / error — y el vacío de noticias. No son foco de SP3 pero conviven en la vista.)

---

## 10. Restricciones técnicas

- **Cero backend nuevo.** Todos los datos que tu diseño puede mostrar ya existen y están listados en §7. No pidas campos nuevos, no inventes un "score", no asumas datos que no estén en esas tablas. Si tu diseño necesita un dato que no aparece en §7, no existe — replantea.
- **El gráfico se monta con lightweight-charts** (librería de velas). Es velas, no líneas. La banda de 30d + marcador (Pieza 2) se superpone como capa sobre las velas, igual que las capas actuales (vida / paredes / jugada). La librería dibuja las velas; las anotaciones (bandas, líneas, etiquetas) van como overlay sincronizado al eje de precio.
- **Reusa los componentes y el flujo existentes.** El flujo de dos pantallas (lista → idea-de-moneda), el átomo de frescura, el panel del copiloto, el sistema de tokens cálidos — todo eso existe (§6). Diseña *dentro* de ese marco, no uno paralelo. El marco de régimen (Pieza 1) es un rediseño de la cabecera que ya existe, no una pieza nueva.
- **Accesibilidad (obligatoria, el usuario es un lector mayor):**
  - Cuerpo de texto **≥18px**; secundarios ≥16px; mínimos ≥14px; tags ≥12px.
  - Targets táctiles **≥48×48px** (mano que puede temblar).
  - Contraste **AA** (los hex de §8 ya están corregidos para esto; respétalos).
  - **No-solo-color** (WCAG 1.4.1): nunca comuniques un estado solo con color. Los estados llevan microcopy textual ("activo/inactivo/sin confirmar", "fresco/rancio/muerto"). Glifos con significado en el texto adyacente.
  - Foco visible: borde de 3px en arcilla profunda (`--clay-deep`) con offset de 2px.
  - Respeta `prefers-reduced-motion`: sin animaciones agresivas; tus transiciones deben poder apagarse.

---

## 11. Entregables

Entrega mockups (no código). Lo que pedimos:

**Mockups requeridos:**
1. **La idea-de-moneda enmarcada** (Pieza 1): la vista completa con el marco de régimen dominante/persistente envolviendo la idea-de-moneda rica. Muestra cómo se siente "la moneda dentro del clima". Incluye el estado normal (régimen fresco, moneda candidata).
2. **El gráfico con banda + marcador** (Pieza 2): un detalle/zoom del gráfico de velas con la banda de rango de 30 días y el marcador de posición, conviviendo con las capas de vida/paredes/jugada. Muestra al menos dos posiciones: precio en la parte baja (candidata, marcador abajo) y precio alto (no candidata, marcador arriba).
3. **La jerarquía pulida** (Pieza 3): la vista completa con la tipografía, densidad y respiración finales — que se vea cómo la costura y los hechos respiran. Puede ser el mismo mockup que el #1, en su versión refinada.
4. **Todos los estados de §9**: candidata / no-candidata-pero-viva / cargando / no-disponible / frescura fresco·rancio·muerto / dominancia muerta / jugada en-curso·incierta·plan-listo·fijada·cerrada·sin-plan. Pueden ser variantes/frames de las pantallas base, no pantallas completas independientes para cada uno.

**Formato:** lo que use el equipo de diseño (Figma preferido). Entrega los frames organizados por pieza y por estado, con anotaciones de qué dato alimenta cada elemento (referencia a los campos de §7) y qué texto es verbatim vs. propuesto.

**Decisiones visuales que TÚ resuelves (son tuyas, no las prescribimos):**
- El **mecanismo del marco** de régimen (Pieza 1): sticky, lateral, envolvente, etc. — lo que mejor comunique "el clima manda y está siempre presente" sin teñir la moneda.
- La **forma gráfica de la banda + marcador** (Pieza 2): cómo dibujar el rango de 30d y el marcador de posición de modo cálido, legible, distinguible de las paredes S/R, sin tapar las velas.
- La **jerarquía tipográfica y la densidad** dentro de la escala dada (§8): qué respira, qué se agrupa, qué es título/contexto/dato/costura.
- **Microcopy** (dentro de la doctrina §4 y respetando las frases verbatim): puedes proponer mejoras de etiquetas y micro-textos; márcalas como propuestas.

---

## 12. Fuera de alcance

- **No toca el backend, la doctrina ni el contrato de datos.** Los datos son los de §7, fijos. La doctrina anti-veredicto (§4) no se negocia ni se "suaviza". Las frases verbatim (la costura, AC7, la frase del régimen) no se reescriben.
- **No es el rediseño de la lista ni del "Pick".** La pantalla de lista (PickScreen, §6.3) se incluye aquí solo como contexto de dónde viene el usuario. SP3 rediseña la **idea-de-moneda** (IdeaView), no la lista.
- **El "lente-momentum" y la calibración de umbrales son POST-SHIP.** No diseñes para una cuarta lente ni para umbrales ajustables; eso viene después y no es parte de este encargo.
- **El copiloto no es foco.** Existe (§6.10) y debe quedar coherente con el rediseño, pero no se re-arquitecta en SP3.

---

*Fin del brief. Todo lo necesario para diseñar SP3 está arriba. Si algo te falta, no lo inventes: vuelve a §7 (datos), §6 (estado actual) y §4 (doctrina) — la respuesta está ahí, o el dato no existe.*
