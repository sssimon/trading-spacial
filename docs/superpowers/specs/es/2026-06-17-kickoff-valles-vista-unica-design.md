# Kickoff de diseño — Valles como una sola vista (la "idea" de la moneda)

Para: equipo de diseño
De: producto
Fecha: 2026-06-17
Fuente funcional: Valles en `main` (recorrido de 4 lentes: Vida → Niveles → La jugada → Quién), `LaJugadaChart` (PR #607, lightweight-charts + overlays), `NivelesScreen`/`VidaScreen`/`FundScreen`, doctrina anti-veredicto del arco Valles.

Entregable: **implementación directa del feature** (decisión de Samuel: sin mockups, se construye ya). Este documento es el spec de diseño que se construye 1:1.

---

## 1. Contexto que define el encargo

Hoy Valles es un **recorrido guiado de 4 pasos** (Vida → Niveles → La jugada → Quién), una pantalla por lente, pensado para un lector mayor (el papá de Samuel, ~70). El encargo lo reimagina como **una sola vista por moneda — la "idea" de esa moneda**, al estilo de las *trading ideas* de TradingView: un gráfico anotado arriba, la explicación que lo guía, quién está detrás, y las noticias recientes. Todo en una página, no en un wizard.

Decisión doctrinal ya tomada con Samuel: la explicación es **descriptiva, no opinada** (opción A). Tiene la *forma* de una trading idea (gráfico + prosa que guía) pero **no firma una dirección** — narra los hechos y la estructura disciplinada, la decisión de entrar es del lector. Valles no firma; exhibe.

Lema de trabajo (copy final pendiente, pasa por solace-wren):

> LA IDEA DE LA MONEDA, EN UN SOLO LIENZO: SUS PAREDES, SU JUGADA, QUIÉN ESTÁ DETRÁS. LOS HECHOS — LA DECISIÓN ES TUYA.

## 2. El encargo en una frase

Fusionar Vida + Niveles + La jugada en **un solo gráfico de velas anotado con su narrativa descriptiva**, y componer con ese gráfico, Quién (dossier) y un resumen de noticias recientes una **única vista de "idea de moneda"** — densa pero leíble para un lector mayor, honesta, sin veredicto.

No es un dashboard de trading. No es una trading idea opinada. Es la página que un operador honesto le mostraría a su papá: "mira, esta moneda está así, estas son sus paredes, así se saldría por partes, esto es quién la hace, y esto es lo último que se dijo de ella — tú decides."

## 3. Decisiones ya tomadas

### Del arco Valles (no se reabren)
1. **Exhibe hechos, nunca un veredicto.** La narrativa describe; no predice, no rankea, no recomienda entrar. La jugada se porta como consecuencia geométrica de las paredes, no se firma.
2. **La jugada es disciplina, no predicción.** Los TPs no afirman "va a subir"; afirman "estas son las paredes; una salida ordenada escalona contra ellas".
3. **El sistema cita, el humano juzga** (dossier y noticias): hechos con su fuente, nunca "esta noticia es alcista".
4. **Lenguaje cálido, lector mayor.** Papel/arcilla/salvia/ocre, Source Serif 4 + Instrument Sans, cuerpo ≥18px, toque ≥48px, contraste AA, tuteo venezolano, frases simples.

### Cerradas con Samuel (2026-06-17)
- **Una sola vista**, no wizard: Vida+Niveles+Jugada fusionadas en el gráfico+explicación, más Quién, más Noticias.
- **Narrativa descriptiva (A)**, no tesis opinada (B).
- **Noticias = track aparte.** Es un subsistema nuevo (backend de noticias recientes por moneda, probablemente vía Exa). En esta vista se diseña su **sección y su hueco reservado**, pero su build va en su propio kickoff. No bloquea la vista.

## 4. Modelo de pantalla pedido

La vista es **una columna leíble** (no un tablero multi-panel). De arriba hacia abajo:

### 4.1 El gráfico unificado (el corazón)
Un solo lienzo de velas (el `LaJugadaChart` existente, extendido) que **absorbe las tres primeras lentes en capas**:
- **Vida** → el contexto de "está viva y en valle": la banda/sombreado de la consolidación (el rango lateral de N semanas) sobre las velas, y un sello discreto de liquidez/vida ("viva · vol diario $X").
- **Niveles** → **todas las paredes** (techos y pisos con sus toques), no solo las que se vuelven peldaños; y la marca de dónde está el precio ahora respecto a ellas. (Reemplaza el diagrama vertical de `NivelesScreen`.)
- **La jugada** → los overlays ya construidos: zona de entrada (banda), stop/break-even, escalera de TPs, runner, precio vivo, hueco honesto.

**Leyenda clicable dentro del gráfico (el mecanismo que hace simple la densidad).** El gráfico lleva una leyenda con una entrada por capa — **Vida (el valle)**, **Paredes**, **La jugada** (y el precio vivo). Cada entrada es un toggle: clic la prende/apaga. El lector ve **todo junto** por defecto, o **aísla** una capa con un clic (ej. solo las paredes, o solo la jugada). Así toda la información está disponible sin abrumar — la densidad la controla el lector, no se esconde. Las entradas activas se ven encendidas (su color cálido); las apagadas, atenuadas. Estado de la leyenda local a la vista (no se persiste entre monedas en v1).

### 4.2 La narrativa que guía (la "tesis", pero honesta)
Debajo (o al lado, en desktop) del gráfico, la explicación en lenguaje simple, en **bloques anclados** que el lector recorre — cada bloque resalta su capa en el gráfico:
- **¿Está viva?** — vida + consolidación, en palabras.
- **¿Dónde está entre sus paredes?** — lo de Niveles: techo arriba a X%, piso abajo a Y%, ya rebotó N veces.
- **Si decides entrar, ¿cómo sería la salida?** — la jugada: zona de entrada (rango), stop, escalera, runner; "esto sale de tus paredes, no es un consejo de comprar".

Esto es lo más cercano a la "tesis" de una trading idea — pero descriptiva. La **costura visible** ("esto sale de tus niveles · la decisión es tuya") es obligatoria y es lo que la mantiene del lado de la observabilidad.

### 4.3 Quién está detrás (dossier)
La sección de fundamentales citados (equipo, presencia, fuentes) — lo que hoy es `FundScreen`/`/dossier`. Hechos con enlaces a su fuente; cero juicio sobre "calidad del proyecto".

### 4.4 Noticias recientes (hueco reservado — track 2)
Una sección con **las 5 noticias más recientes de la moneda, cada una con su enlace y su fecha**. Diseñar la sección (lista sobria, titular + fuente + cuándo, sin "sentimiento"). En esta entrega va como **estado de diseño + placeholder** ("las últimas 5 con su enlace"); el backend llega en el track 2. No inventar sentimiento ni resumen editorial de la noticia: titular + fuente + fecha.

## 5. Gramática visual

Mismo lenguaje cálido. La vista debe sentirse como **una lámina editorial de una sola moneda**, serena, leíble de corrido, no como un terminal.
- Neutros cálidos dominan. El gráfico no se ilumina; las velas en tonos neutros (ya definido en `jugada.module.css`).
- **Salvia** = lo cumplido / lo que ya está. **Ocre** = atención honesta (el stop, dato viejo). **Sin verde/rojo** de "entra/sube". Sin glow, sin números hero, sin badges de compra.
- La narrativa y el gráfico se referencian (enfocar un bloque resalta su capa). Las costuras entre capas (vida/niveles/jugada) quedan visibles — no se funden en un objeto único que firme.

## 6. Candados de honestidad

1. **La forma de "trading idea" amplifica el riesgo de veredicto.** Un gráfico anotado + prosa al lado *grita* "esta es mi jugada, cómprala" aunque ningún campo lo diga (el "cuarto objeto" de F3b). El diseño debe neutralizarlo a propósito: costura visible, lenguaje descriptivo, cero gramática de "señal".
2. **La entrada es un rango, nunca un punto.** Siempre la zona de soporte.
3. **Cada número trae su pared.** Entrada→soporte, stop→piso, cada salida→un techo.
4. **La jugada no promete ganar.** "No puede perder" solo aplica a la porción tras break-even.
5. **El dossier y las noticias citan, no juzgan.** Titular + fuente + fecha; nunca "bullish/bajista", nunca un resumen que opine.
6. **Frescura honesta.** Cada capa con dato vivo muestra su edad (gráfico/precio, niveles, jugada en curso, noticias). Dato viejo se dice.
7. **No hay acción que ejecute.** Sin "comprar/entrar". La entrada la hace el humano por su flujo.

## 7. Lenguaje permitido y prohibido

Prohibido en UI primaria: comprar; entrar ya; oportunidad; va a subir; buena entrada; la mejor; señal/idea de compra; objetivo de precio (como promesa); ganancia esperada; bullish/bajista; noticia alcista; sentimiento; confianza; probabilidad; precio de entrada (puntual).

Preferir: la idea de la moneda; está viva / en valle; sus paredes; dónde está el precio; si decides entrar; la zona de entrada; hasta dónde aguantas; sales por partes; quién está detrás; lo último que se dijo; la fuente; la decisión es tuya.

## 8. Hechos de runtime que diseño debe respetar

- El gráfico reusa `LaJugadaChart` (lightweight-charts) — ya monta velas de `/ohlcv` + overlays vía `priceToCoordinate`. Las paredes salen de `/levels/{symbol}` (todas las zonas, no solo las de la jugada); la jugada de `/plan/derive` y `/plan/{symbol}`; la vida de `/valley-eval/{symbol}`. **Todos esos endpoints ya existen.**
- La frescura de cada capa ya viene en su contrato (la jugada viva por `LiveSnapshot`; niveles/vida con su `generated_at`). Diseño debe reservar el lugar para mostrarla por capa — no un "EN VIVO" global.
- **Noticias: NO existe endpoint hoy.** Diseñar la sección contra un contrato supuesto `{titular, fuente, url, fecha}` × 5; el backend es el track 2.
- El frontend no recalcula: muestra lo que el backend deriva.
- Self-tenant (lo ve el papá). Sin selector de usuario.

## 9. Estados que los mockups deben cubrir

1. Moneda viva, en valle, con paredes claras y jugada completa (el caso "lindo").
2. Viva pero sin techos claros arriba → escalera corta + hueco honesto.
3. Precio fuera de la zona de entrada.
4. Jugada ya fijada / en curso (mostrar el estado vivo en el mismo lienzo).
5. Dossier opaco ("no rastreable").
6. Noticias: con 5, con menos de 5, sin noticias (placeholder honesto).
7. Capa con dato rancio (frescura en ocre).
8. **Variante móvil** — la columna leíble en un teléfono, para el papá.
9. La vista para un lector mayor: ¿cómo se recorre sin abrumar? (ver decisión A abajo).

## 10. Cortes explícitos

No diseñar: tablero multi-panel tipo terminal; el wizard de 4 pasos (se reemplaza); badges de compra/sentimiento; ranking de monedas; resumen editorial opinado de noticias; gráfico con gramática verde/rojo de señal; cálculo en cliente; el backend de noticias (track 2, solo su sección).

## 11. Criterio de éxito

Un lector mayor abre la vista de una moneda y, recorriéndola con calma, entiende: si está viva, dónde está entre sus paredes, cómo se saldría por partes si decidiera entrar, quién la hace, y qué se dijo de ella último — **sin sentir en ningún momento que la app le dice "compra esto"**. Si a los tres segundos se lee como una recomendación, el diseño falló.

## 12. Flujo de trabajo

Diseño entrega: (1) la vista completa desktop; (2) variante móvil; (3) los estados de §9; (4) la sección de noticias (placeholder); (5) nota de lenguaje (términos usados/evitados); (6) propuesta de cómo se recorre para un lector mayor (decisión A).

Producto valida contra los candados de §6 antes de implementación.

---

## Decisiones cerradas con Samuel (2026-06-17)

- **A — Toda la info, simple, vía el poder del gráfico.** Nada de esconder con "ver más". Se muestra todo, y la simplicidad la da la **leyenda clicable** (§4.1): el lector prende/apaga capas con un clic. El **resumen textual** (§4.2) contiene toda la información del gráfico, pero en bloques digeribles. Ver todo junto o cada parte aislada — control del lector.
- **B — Pick y Cierre.** El Pick (lista de candidatas) sigue siendo la entrada: eliges una moneda → su "idea". El Cierre se absorbe como el pie de la vista.
- **C — Densidad del gráfico.** Resuelta por A: las capas se controlan con la leyenda clicable (no "siempre todas" ni "resaltar al enfocar" — el lector decide). Default: todas encendidas.
- **D — Sin mockups, build directo.** Se construye el feature ya. Alcance: **track 1 = la vista fusionada completa** (gráfico unificado + leyenda + narrativa + Quién + sección de Noticias en estado vacío honesto). El **backend de Noticias es el track 2** (su propio ciclo); la sección queda renderizada con su empty-state hasta que ese backend exista.
