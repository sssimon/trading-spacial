# Kickoff de diseño — El Instrumento dentro del screener de Valles

Para: equipo de diseño
De: producto
Fecha: 2026-06-17
Fuente funcional: arco Valles (specs 2026-06-11 a 2026-06-15), Instrumento construido (`api/plan.py`, `instrument/*`, `db/lifecycle_states.py`, `db/conduct_episodes.py`), flujo cálido shipeado (PR #595), revisión del roster.

Entregable pedido: mockups dentro del lenguaje cálido actual de Valles. No código.

---

## 1. Contexto que define el encargo

El arco Valles construyó tres lentes de hechos (Vida, Niveles, Dossier) y, sobre ellas, **el Instrumento**: la pieza que deriva una *jugada disciplinada* desde las paredes de soporte/resistencia (D.1) — zona de entrada, stop bajo el piso, escalera de salidas escalonadas contra las resistencias, un runner abierto, y la regla de mover el stop a break-even cuando se llena la primera salida.

El Instrumento **está construido y corriendo en producción** (`/plan/derive`, `/plan/confirm`, `/plan/{symbol}`; el tracker `track_live` lo actualiza cada 5 min y al cierre escribe el episodio de conducta). Pero el rediseño cálido (PR #595) shipeó el recorrido de las 3 lentes y **dejó el Instrumento fuera de pantalla**. Hoy nadie en el frontend lo invoca. El motor que arma la jugada está andando solo, sin nadie que lo muestre.

Eso es lo que se corrige: **darle cara al Instrumento dentro del flujo de Valles**, con su ciclo completo (derivar → confirmar → seguir en vivo → leer la conducta al cierre), sin romper la doctrina que el arco entero defendió.

Lema de trabajo (copy final pendiente, pasa por solace-wren):

> EL INSTRUMENTO TE MUESTRA CÓMO SE ORDENA UNA SALIDA CONTRA LAS PAREDES. NO TE DICE QUE ENTRES.

## 2. El encargo en una frase

Integrar el ciclo completo de la *jugada* (derivarla desde tus niveles, confirmarla en frío, seguirla en vivo, y al cierre leer si la honraste) dentro del recorrido cálido de Valles, leído como **disciplina geométrica** —"si decides entrar, así se sale por partes"— nunca como un veredicto de compra.

No es una vista nueva de trading. No es un radar. No es un botón de "entrar". Es darle cara a lo que el Instrumento ya hace, con la misma honestidad de las 3 lentes: hechos y procedencia, cero firma.

## 3. Decisiones ya tomadas (vienen del arco, no se reabren)

1. **La jugada es disciplina, no predicción.** Los TPs en las resistencias no afirman "esto va a subir" — afirman "estas son las paredes; una salida ordenada escalona contra ellas". El Instrumento nunca dice que el plan gana; solo mide si se cumplió. *(spec instrumento-lifecycle §1.)*
2. **Exhibe hechos, nunca un veredicto.** La jugada se deriva de la geometría de D.1, con la costura visible ("esto sale de tus niveles"). No es una síntesis de las 3 lentes — eso sería la "cuarta línea" prohibida (F3b). La decisión de entrar es del humano, a propósito.
3. **El número sale de la pared, no del sistema.** Cada cifra del plan se atribuye a su origen geométrico: la entrada a la zona de soporte, el stop al piso inmediato, cada salida a una resistencia. Si un número aparece sin su pared, miente.
4. **El operador firma; el Instrumento sostiene.** El plan se fija en frío (gate) y la máquina lo sostiene como ley para medir si lo honraste. La interfaz acompaña la disciplina; no la sustituye.
5. **La conducta se mide, no el acierto.** Al cierre, el Instrumento lee si honraste la ley que aprobaste — campo por campo, sin PnL, sin score. Un trade puede perder y ser conducta perfecta. *(spec instrumento-lifecycle §7.)*
6. **Lenguaje cálido, lector mayor.** Mismo sistema de Valles: papel/arcilla/salvia/ocre, Source Serif 4 + Instrument Sans, cuerpo ≥18px, toque ≥48px, contraste AA, tuteo, frases simples. Lo ve papá.

### Decisiones cerradas con Samuel (2026-06-17)

- **A — Dónde:** "La jugada" es **pantalla propia, después de Niveles** (+ marca discreta de "tiene jugada" en el screener). Separa la geometría (Niveles) de su consecuencia disciplinaria (la jugada) y evita el efecto "cuarta línea".
- **B — Alcance v1:** **con todo el ciclo** — derivar, confirmar (gate), seguir en vivo (`/plan/{symbol}`) y leer la conducta al cierre (`conduct_episodes`). No se difiere nada.
- **C — De qué entrada se deriva:** al **precio vivo** por defecto ("calculado al precio de ahora"), **con la entrada SIEMPRE expresada como un rango (la zona de soporte), nunca como un precio fijo**. Un precio puntual se lee como "compra exactamente aquí"; la zona es honesta y es lo que D.1 entrega.

## 4. Modelo de pantalla pedido

### 4.1 Dónde vive la jugada en el recorrido

El flujo cálido hoy es: **Pick (screener) → Vida → Niveles → Fundamentales → Cierre.**

La jugada se deriva de **Niveles** (las paredes). Recorrido pedido:

**Pick → Vida → Niveles → La jugada → Fundamentales → Cierre.**

- En el **Pick (la lista del screener)**, cada candidata lleva una marca discreta y de bajo contraste de "tiene jugada derivada" — sin números, solo señalar que existe. Nunca un ranking ni un "esta es mejor".
- "La jugada" entra **inmediatamente después de Niveles**, como su consecuencia geométrica directa, no como un resumen final de todo.
- El **gate de confirmar**, la **jugada en curso** y la **lectura de conducta** cuelgan de "La jugada": una jugada confirmada se sigue en vivo; una jugada cerrada muestra su lectura de conducta.

### 4.2 "La jugada" — el plan derivado (pre-entrada)

Debe mostrar, en lenguaje simple y con cada número anclado a su pared:

- **Dónde entrarías:** **la zona de entrada como rango** (el soporte de D.1, `precio_bajo`–`precio_alto`), nunca un precio puntual. El precio vivo se ubica dentro o cerca de esa zona, con la línea "calculado al precio de ahora".
- **Hasta dónde aguantas:** el stop, y que está bajo el piso inmediato (con su margen).
- **Cómo sales por partes:** la escalera de salidas (hasta 4), cada peldaño sobre una resistencia nombrada, con el tamaño de cada salida (la primera es la más grande: 0.50 / 0.20 / 0.15 / 0.10).
- **Lo que dejas corriendo:** el runner abierto sin objetivo (0.05), y que su stop sube a break-even cuando se llena la primera salida ("a partir de ahí, esa parte ya no puede perder").
- **De dónde sale todo esto:** una línea honesta — "esto se arma con tus niveles, no es un consejo de comprar".

La pantalla describe una **salida ordenada**, no una entrada urgente. El titular es la disciplina ("así se sale por partes"), no la oportunidad.

### 4.3 El gate — confirmar la jugada en frío

Es el momento en que **el operador firma** (`POST /plan/confirm`). Debe sentirse como un acto deliberado y tranquilo, no un "comprar":

- Repite el plan completo que se va a fijar como ley.
- Deja claro que confirmar **no ejecuta nada** — la entrada la hace el humano por su flujo normal en Binance. Confirmar solo le dice al Instrumento "esta es la ley que voy a honrar".
- Una sola acción primaria, sobria (p. ej. "Fijar esta jugada"), sin verde de "entrar".
- Tras confirmar, la jugada pasa a estar "en curso" y se puede seguir en vivo.

### 4.4 La jugada en curso — el plano vivo

Vista viva (`GET /plan/{symbol}`) de cómo va la jugada contra el plan. Son **hechos del estado**, nunca instrucciones:

- Qué salidas se llenaron, dónde está el stop ahora, si ya se movió a break-even, cuánto queda abierto.
- Hechos en palabras simples: "la primera salida se llenó", "tu stop está en break-even", "tu stop sigue debajo de la zona".
- **Frescura visible:** cuándo se actualizó por última vez; si está vieja o incierta, se dice ("transición sin confirmar — revisá en Binance").
- **Pull-only:** el lector mira cuando él quiere; cero notificaciones, cero push.

### 4.5 La lectura de conducta — al cierre

Cuando la jugada cierra, el Instrumento emite la lectura de conducta (`conduct_episodes`): **¿honraste la ley que aprobaste?** — campo por campo, **sin PnL, sin score**:

- Entraste en la zona; respetaste el stop; moviste a break-even cuando tocaba; honraste los peldaños; escalonaste; cerraste según el plan; cuánto aguantaste.
- Es un **espejo, no un juez**. Un trade puede perder y ser conducta perfecta; o ganar y ser conducta pobre. La pantalla nunca califica el trade por si ganó.
- Cero número único de "calidad". Solo los hechos de tu propia conducta, para que te veas.

### 4.6 El cierre del recorrido

La pantalla de Cierre se mantiene como recap neutral de los hechos de las lentes. La jugada **no** se funde ahí dentro como conclusión. Si el cierre la menciona, la nombra como pieza aparte ("la salida ordenada que se derivó de tus niveles"), no como el veredicto que cierra el caso.

## 5. Gramática visual

Mismo lenguaje cálido de Valles. La jugada debe sentirse como una **lámina de instrucciones de salida**, serena y leíble, no como un tablero de trading.

- Neutros cálidos dominan (papel/arcilla). La jugada no se ilumina.
- **Salvia:** lo cumplido / lo que ya está (una salida llena, el stop en BE, un campo de conducta honrado).
- **Ocre:** atención honesta (el stop, lo que aguantas, dato viejo, estado incierto). Con mesura.
- **Sin verde/rojo** como gramática de "entra/sal" ni de "ganaste/perdiste". En trading excitan antes de informar.
- Sin glow, sin números hero gigantes, sin badges de "compra", sin countdowns, sin pulso de urgencia.
- La escalera de salidas se dibuja contra las paredes (relación espacial con Niveles), para que se lea como geometría, no como promesa.
- La zona de entrada se dibuja como **banda**, no como línea — refuerza visualmente que es un rango.

## 6. Candados de honestidad

1. **La jugada es estructura de salida.** No es una orden de compra ni una predicción de subida.
2. **La entrada es un rango, nunca un punto.** Siempre la zona de soporte (`precio_bajo`–`precio_alto`). Jamás un precio puntual que se lea como "compra exactamente aquí".
3. **Cada número trae su pared.** Entrada→soporte, stop→piso, cada salida→una resistencia. Ningún número aparece huérfano.
4. **El stop es lo que aguantas, no una garantía.** "No puede perder" solo aplica tras mover a break-even, y solo a esa porción.
5. **La frescura es edad del dato.** La jugada en curso muestra cuándo se actualizó; si está vieja o incierta, se dice.
6. **Confirmar no ejecuta.** El gate fija la ley; la entrada la hace el humano por su flujo. No hay one-click execute.
7. **La conducta no es el acierto.** El cierre mide si honraste el plan, nunca si ganaste. Sin PnL como veredicto, sin score.
8. **El plano vivo es pull-only.** Cero push, cero notificación.
9. **No hay síntesis de las 3 lentes.** La jugada sale de Niveles, no de "vivo + limpio + en soporte = compra".
10. **El frontend no recalcula.** No deriva el plan ni la conducta en cliente; los pide al backend y los muestra.

## 7. Lenguaje permitido y prohibido

Prohibido en UI primaria:

- comprar; entrar ya; oportunidad; va a subir; buena entrada; la mejor; señal de compra; aprovecha; ahora o nunca; objetivo de precio (como promesa); ganancia esperada; ganaste/perdiste (como juicio); confianza; probabilidad; precio de entrada (puntual).

Preferir:

- la jugada; cómo se ordena la salida; si decides entrar; la zona de entrada; hasta dónde aguantas; sales por partes; primera salida; lo que dejas corriendo; tu stop sube a break-even; esto sale de tus niveles; fijar la jugada; ¿honraste tu plan?; la decisión es tuya.

`Plan` / `objetivo` solo se usan si vienen acompañados de su pared y de la línea "no es un consejo".

## 8. Hechos de runtime que diseño debe respetar

- **`GET /plan/derive/{symbol}`** deriva el plan sin persistir. Devuelve `entry`, `sl_plan`, `rungs[]` (`tp_price`, `size_frac`), `runner_frac`, `entry_zone`. Necesita un `entry_price` de entrada → se deriva al precio vivo por defecto.
- **La entrada se muestra SIEMPRE como `entry_zone` (rango), no como `entry` (punto).** Si el contrato hoy entrega un `entry` puntual, diseño lidera con `entry_zone`; alinear el contrato para expresar la entrada como rango es tarea de backend, no de diseño.
- **`POST /plan/confirm`** fija el plan como ley (crea la jugada viva en `lifecycle_states`). Es el gate del operador. Escribe estado: es la única acción del flujo que no es solo lectura.
- **`GET /plan/{symbol}`** es la vista viva: `estado_vivo` (`activo`/`cerrado`/`incierto`), `plan`, `realidad` (`fase`, `rungs_llenos`, `sl_actual`, `be_movido`, `size_restante_frac`), `hechos[]`.
- **Frescura (No-Negociable #8):** la vista viva **no viene envuelta en `LiveSnapshot` todavía**. Como B incluye el plano vivo, **cerrar ese hueco es prerrequisito de backend** antes de shipear 4.4: el plan en curso debe emitir su frescura (fresco/rancio/muerto) en el contrato. Diseño debe reservar el lugar para esa frescura.
- **Dueño de frescura:** `track_live` corre cada 5 min dentro del ciclo de sync. La jugada viva se mueve a ese ritmo, no en tiempo real.
- **Conducta al cierre:** vive en `conduct_episodes`, emitida por el tracker al pasar a CLOSED. Son campos de conducta (`entry_en_zona`, `sl_respetado`, `adherencia_be`, `rungs_honrados`, `cierre_en_plan`, `hold_hours`), **no una resta de PnL**.
- Tamaños de la escalera (0.50 / 0.20 / 0.15 / 0.10) y runner (0.05) salen del backend. No inventarlos ni recalcularlos en cliente.
- Es **self-tenant**: el lector ve su sesión (papá = tenant EXTERNAL). No hay selector de usuario.

## 9. Estados que los mockups deben cubrir

1. Candidata con jugada derivada limpia (paredes claras arriba y abajo, zona de entrada nítida).
2. Candidata sin resistencias claras arriba → escalera incompleta o sin peldaños (la jugada lo dice honesto, no inventa paredes).
3. Precio vivo fuera de la zona de entrada → mostrarlo honesto ("el precio de ahora está por encima de tu zona").
4. Gate de confirmar (plan a punto de fijarse) + estado "ya fijada".
5. Jugada en curso, primera salida llena, stop en break-even.
6. Jugada en curso con estado incierto (snapshot ambiguo) → "revisá en Binance".
7. Jugada en curso vieja/rancia (dato de hace rato).
8. Cierre con lectura de conducta honrada (todo en salvia).
9. Cierre con lectura de conducta no honrada (campos en ocre, sin regaño, sin PnL).
10. Sin jugada (la moneda no da estructura) → vacío honesto, no relleno.
11. Variante móvil compacta de cada pantalla.

Si la pantalla solo se ve bien cuando la jugada es "bonita" o "ganadora", está mal diseñada.

## 10. Cortes explícitos

No diseñar:

- botón "entrar" / "comprar" / "confirmar y ejecutar";
- entrada como precio puntual;
- ranking de candidatas por "mejor jugada";
- objetivo de precio presentado como ganancia prometida;
- PnL ni "ganaste/perdiste" como veredicto del cierre;
- verde/rojo como semáforo de entrada o de resultado;
- notificaciones push de la jugada;
- síntesis de las 3 lentes en un veredicto;
- gráfico pesado de trading dentro del flujo cálido;
- cálculo del plan o de la conducta en cliente.

## 11. Criterio de éxito

El mockup correcto se mira rápido y produce calma: el lector entiende **cómo saldría por partes si decidiera entrar**, de dónde sale cada número, que la entrada es una zona y no un punto, y que la decisión de entrar es suya. En el cierre, se ve a sí mismo sin sentirse juzgado por el resultado. Si al mirarlo tres segundos se siente que la app dice "compra esto" o "ganaste/perdiste", el diseño falló.

## 12. Flujo de trabajo

Diseño entrega:

1. pantalla "La jugada" derivada (desktop + móvil);
2. marca discreta de "tiene jugada" en el Pick;
3. gate de confirmar;
4. vista de la jugada en curso (con su frescura);
5. lectura de conducta al cierre;
6. estados vacíos / incierto / rancio / sin paredes;
7. nota de lenguaje con términos usados y evitados.

Producto valida contra los candados de §6 antes de implementación. Backend cierra el hueco de `LiveSnapshot` en `/plan/{symbol}` (§8) en paralelo, como prerrequisito de 4.4.

---

## Acta breve del roster

- **Voronov:** la jugada se porta como consecuencia de Niveles, no se firma como veredicto. La costura ("esto sale de tus paredes") es lo que la mantiene del lado de la observabilidad. La entrada-como-rango es correcta: un punto reifica una precisión que el dato no tiene.
- **Halberg:** con el plano vivo en alcance, el hueco de `LiveSnapshot` en `/plan/{symbol}` deja de ser opcional — sin frescura en el contrato, la jugada viva puede mostrarse muerta como viva. Prerrequisito duro.
- **Serrano:** el riesgo de tipo es presentar el cierre como PnL. La lectura de conducta debe quedar blindada contra "ganaste/perdiste"; es espejo, no juez.
- **Cassian:** el core shippable es el ciclo completo de una jugada leíble; el corte está en no meter charts pesados ni ejecución. El gate escribe estado — es la única pieza no-read-only, trátese con cuidado.
- **Null Vale:** la mentira más fácil aquí es el precio de entrada puntual y el verde de "ganaste". Aunque el copy sea prudente, una línea de entrada y un número en verde firman solos. La banda de entrada y la ausencia de PnL los desactivan.

Decisión final: el Instrumento entra al flujo de Valles con su ciclo completo (derivar → confirmar → vivo → conducta), leído como disciplina geométrica. La interfaz muestra cómo se ordena una salida; no recomienda, no ejecuta y no celebra el resultado.
