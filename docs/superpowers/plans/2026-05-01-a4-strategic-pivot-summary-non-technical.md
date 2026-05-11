# Plan estratégico — Pivot del sistema de señales BTC/USDT

**Fecha:** 1 de mayo de 2026
**Para:** Analista de trading / stakeholder no técnico
**Resumen ejecutivo:** El diagnóstico ejecutado el 30 de abril mostró que **bajo el exit logic vigente y con la canasta actual de 10 monedas, la estrategia no tiene edge ejecutable** — solo 4 de 10 monedas tienen señal predictiva sólida, y aún en esas la lógica de salida basada en volatilidad destruye ~100% del edge antes de materializarse. El diagnóstico identifica la estructura del problema y un path para resolverlo: pivotamos a una arquitectura de salida nueva (tres barreras con límite de tiempo por cluster), reducimos la canasta efectiva a 4 monedas, y posponemos la comunicación al cliente hasta tener números honestos disponibles. La estrategia en sí no está descartada — está pendiente de evaluación honesta contra el período no visto (holdout).

---

## 1. Qué encontramos (el diagnóstico)

El sistema se construyó alrededor de dos premisas:

1. **10 monedas curadas son rentables** (BTC, ETH, ADA, AVAX, DOGE, UNI, XLM, PENDLE, JUP, RUNE).
2. **El exit logic basado en volatilidad** (stops y targets calculados como múltiplos del ATR) captura la edge predictiva del scoring.

Ambas premisas resultaron falsas tras un diagnóstico riguroso ejecutado el 30 de abril sobre el segmento de entrenamiento (18 meses de datos, costos de transacción realistas aplicados).

### 1.1 Edge predictiva real — solo 4 de 10 monedas

Análisis de retornos futuros (ignorando exits, midiendo solo el desplazamiento del precio post-señal a +5 horas):

| Cluster | Monedas | Diagnóstico |
|---|---|---|
| **Edge predictiva sólida** | PENDLE, ADA | Señal estadísticamente significativa (t-stat ≥ 2.55) a horizonte +5h |
| **Edge predictiva parcial** | BTC, ETH | Señal viable, pero con ganadores que mantienen posición ~14h vs perdedores ~4-5h (heterogeneidad fuerte) |
| **Sin edge** | DOGE, JUP, RUNE, XLM, AVAX, UNI | Ruido puro o señal marginal no rescatable |

**Importante:** AVAX inicialmente apareció con t-stat 2.68 (señal sólida), pero el análisis de stops post-mortem mostró que ampliar el SL solo rescata ≤6.6% — los SL hits son trades genuinamente equivocados. Reclasificado a "sin edge".

### 1.2 El exit logic destruye la edge

El hallazgo más crítico: **incluso en las 3 monedas con edge predictiva sólida (PENDLE, AVAX, ADA), el strategy actual termina con expectancy bruto ≈ 0**. ¿Cómo es posible si la señal funciona?

Comparación directa:
- **Exit actual (ATR-based):** ~0% de retorno bruto promedio por trade
- **Exit timer fijo a +5h (sin SL/TP):** **+0.46–0.55% de retorno bruto promedio por trade**

→ La lógica de salida basada en volatilidad **expulsa al sistema de las posiciones antes de que la tesis predictiva se materialice**. La señal es real, el exit la mata.

### 1.3 La calibración no salva al ATR

La conclusión obvia sería "tunear los multiplicadores ATR mejor". Eso es lo que A.4-1 (re-tune pre-holdout) hizo durante el día de hoy — generó parámetros nuevos que mejoran marginalmente sobre la ventana de validación.

**Pero el diagnóstico ya cerró que esa pregunta es la equivocada:** el ATR no es la herramienta correcta de exit para esta estrategia. Tunear sus multiplicadores es ajustar una llave inglesa cuando se necesita un destornillador. El re-tune se ejecutó técnicamente bien, pero su output **no se promueve a producción** — sería curve-fit a la estructura equivocada.

### 1.4 Una pregunta abierta importante

El análisis #8 buscó verificar si la edge predictiva en train es real o un artifact del tuning previo del scoring. La ventana pre-train accesible es de solo 2 meses (n=21-33 entradas por moneda) — estructuralmente insuficiente para resolver. **No podemos cerrar esa pregunta sin tocar el holdout.**

Implicación: A.4 v2 acepta el riesgo y deja al holdout como test final de generalización. Si falla → la conclusión es "el scoring también necesita rediseño", lo cual es información válida. Si pasa → estrategia validada honestamente. No hay forma de eludirlo.

---

## 2. Qué decidimos (las 9 decisiones de hoy)

> **Nota de lectura:** las decisiones 1–6 y 8 son sobre la estrategia y su evaluación (qué hace el sistema, qué tunea, qué publica, qué baselinea). Las decisiones 7 y 9 son meta — sobre cómo documentamos y comunicamos lo anterior + qué asunciones técnicas heredadas requieren documentación explícita. La separación es lectura ergonómica, no organizativa — los IDs siguen orden cronológico de cuando emergieron en la sesión.

### Decisión 1 — Path para A.4 (la fase de re-evaluación de parámetros)

**Decisión:** Rediseño del exit logic. **No** continuar tuneando ATR.

**Por qué:** El diagnóstico mostró que el problema no es calibración, es estructura. Pausar A.4 hasta resolver la pregunta abierta del análisis #8 sería bloquear indefinidamente — esa pregunta es estructuralmente irresoluble sin gastar holdout.

### Decisión 2 — Variante de exit logic

**Decisión:** **Triple Barrier puro con límite de tiempo por cluster.**

Triple Barrier es el patrón canónico documentado en López de Prado (*Advances in Financial Machine Learning*) y la implementación default de Hummingbot v2. Funciona así:
- **Barrera 1:** Stop loss (basado en ATR, igual que ahora)
- **Barrera 2:** Take profit (basado en ATR, igual que ahora)
- **Barrera 3:** Límite de tiempo — cierra la posición cuando expira, sin importar precio

La novedad es la barrera 3. La lectura del diagnóstico:
- **Para PENDLE/ADA:** límite de tiempo ~5 horas (peak de retorno predictivo a h=+5)
- **Para BTC/ETH:** límite de tiempo ~14 horas (mediana de holding de ganadores)

**Caveat material:** un límite de tiempo global a 5h **dañaría activamente a BTC/ETH** (sus ganadores tienen tiempo de tenencia mucho mayor). Por eso el límite es **per-cluster**, no global. Esa calibración exacta se cierra en el spec de implementación — el plan no la fija a priori.

### Decisión 3 — Composición de la canasta efectiva

**Decisión:** **Reducir a 4 monedas: PENDLE, ADA, BTC, ETH.**

Las 6 restantes (DOGE, JUP, RUNE, XLM, AVAX, UNI) NO se tunean. Quedan en la lista por consistencia con el baseline histórico, pero el sistema explícitamente las marca como no-viables. La decisión final ("removerlas / rediseñar scoring per-coin / mantenerlas con flag de disabled") se toma DESPUÉS de que A.4 v2 haya shippeado y se sepa cuánto del edge real es capturable.

### Decisión 4 — Qué hacemos con el trabajo del re-tune ATR ya hecho

**Decisión:** Mergear el código de la herramienta de re-tune (es reusable para cualquier exit logic futuro), pero **NO publicar los parámetros generados**. Quedan archivados como referencia, no se promueven a producción.

### Decisión 5 — Priority del cap de tamaño de posición

Se identificó previamente que el sizing actual puede tomar posiciones que el mercado no puede absorber (especialmente en monedas mid/small-cap, donde el costo modelado fue 12,000-15,000 bps por trade — matemáticamente imposible en ejecución real).

**Decisión:** **Deprioritizar a track paralelo.** Razón: el problema dominante es exit logic, no sizing. Además, 5 de los 6 coins donde sizing era catastrófico salen de la canasta efectiva → urgencia baja. PENDLE (que se queda) sí tiene problema de sizing, pero su distribución de trades cambiará bajo el nuevo exit logic — re-evaluar después.

### Decisión 6 — Re-baseline de números honestos

Hay docs autoritativos previos (*"fórmula ganadora"* de abril 17, *"documento completo del sistema"* de abril 18) con números de backtest **inflados** por bugs combinados que se corrigieron a fines de abril (rounding precision + phantom profit guard). Los números mostraban +$168K / +241% en 4 años, pero la decomposición post-fix mostró que la contribución real del strategy era **-$11,741** (negativa).

**Decisión:** **Re-baseline ejecutado HOY como prerequisito de cualquier comparación futura.** A.4 v2 va a comparar contra el current baseline — si el baseline está inflado, las comparaciones son engañosas. Sin números honestos, no hay terreno firme para juzgar A.4 v2.

### Decisión 7 — Documentos de research/diagnóstico al sistema central

**Decisión:** Mergear hoy a la base autoritativa del repo:
- El doc del diagnóstico de no-edge (estaba en branch separado, no era visible para futuros readers).
- El benchmark de exit logic en frameworks crypto (idem).

**Lección operativa del miss del reviewer interno hoy** (auditoría completa, no solo diagnóstico): hubo dos capas de fallo — (a) los docs estaban en branches no-mergeadas, no en main, así que el inventory inicial no los encontró; pero (b) más importante, los hallazgos load-bearing (cluster classification, "ATR es structural fail") **no estaban resumidos en los issue comments visibles** — vivían dentro del markdown del doc en branch. Mergear los docs es necesario; resumir lo crítico en el comment del issue es igual de necesario. Las dos capas son separadas; ambas fallaron en este caso. Going forward: cuando un comment de PR/issue contiene info que decide direction-change, la información load-bearing debe vivir en el comment text del issue, no solo como link al doc.

### Decisión 8 — Update a la documentación operativa

**Decisión:** Agregar caveat explícito en el doc maestro reflejando el cluster classification — para que futuros readers sepan que la lista de 10 monedas no es afirmación de viabilidad sino baseline histórico.

### Decisión 9 — Asunciones técnicas heredadas

**Contexto:** El sistema actual tiene asunciones técnicas (cooldown 6h entre trades, simetría LONG/SHORT en BE-move, sizing R-multiple, regime detector) que pueden no ser apropiadas con el nuevo exit logic Triple Barrier.

**Decisión:** El spec de A.4 v2 debe documentar explícitamente, antes de evaluar contra holdout:
- Qué cooldown usa y por qué (preservar 6h, ajustar al time-limit, eliminar).
- Qué simetría LONG/SHORT preserva en BE-move y por qué (si BE-move se mantiene como concepto).
- Cualquier otra asunción heredada del código actual que afecte el N efectivo del Deflated Sharpe.
- Justificación per asunción: "se preserva porque..." o "se cambia porque...".

**Por qué importa:** estas asunciones son grados de libertad implícitos. Si entran al holdout sin documentación, el threshold del DSR ex-ante se subestima — y eso se traduce en "A.4 v2 pasó" cuando en realidad el threshold debería haber sido más alto. Documentación explícita = N honesto = Gate 3 honesto.

---

## 3. Qué NO hacemos en esta ronda (out of scope explícito)

Para evitar scope creep:

- **No resolvemos la pregunta de robustez temporal de la señal** (real vs artifact). Datos pre-train insuficientes; el holdout es el test final.
- **No removemos las 6 monedas no-viables de la lista master** todavía. Caveat documentado, decisión final post-A.4 v2.
- **No tuneamos las 6 no-viables** (diagnóstico dice "NO tunear", explícito).
- **No comunicamos a Simon antes de tener el re-baseline disponible** — comunicar con números inflados sería engañoso.
- **No publicamos los parámetros del re-tune ATR** ya generados (archivados como referencia).
- **No ejecutamos el cap de sizing** en esta ronda (deprioritized).
- **No "arreglamos" el exit logic ATR inline** — lo reemplazamos. Parchearlo sin time-limit no captura la edge según el diagnóstico.
- **No movemos el trabajo de multi-tenant ni auth-hardening.** Esos epics siguen su track. Este pivot solo afecta el contenido de la fase de validación de estrategia.
- **No expandimos el grid de búsqueda de A.4 v2 más allá del time-limit per-cluster.** Ver R1.

---

## 4. Secuencia de ejecución

**Fase 0 — Hoy, primero (anchor de la decisión):**
- Comentar la pausa en el PR del re-tune ATR. Esto va antes que cualquier merge de docs para que la cronología del repo cuente la historia correcta — pausa primero, docs de soporte después.

**Fase 1 — Hoy, paralelo post-Fase 0 (3 tareas independientes):**
- Mergear el doc del diagnóstico al sistema central (no se cierra el ticket — sigue trackeando hilos abiertos).
- Mergear el doc del benchmark de exit logic al sistema central (idem).
- Comentar deprioritization del cap de sizing.

**Fase 2 — Hoy, secuencial:**
- Decidir y ejecutar disposición del PR del re-tune ATR (mergear código, no params).

**Fase 3 — Hoy o mañana, secuencial post-Fase 2:**
- Re-baseline de números honestos (re-correr backtests con código corregido sobre todo el histórico, deprecar docs viejos, doc nuevo con números reales).
- Update al doc maestro con el caveat de cluster classification.

**Fase 4 — Esta semana:**
- Escribir el spec de A.4 v2 (Triple Barrier per-cluster, basket de 4, pre-registered cost-survival check, asunciones técnicas explícitas, criterio de éxito pre-registrado).

**Fase 5 — Semanas:**
- Implementación de A.4 v2.
- Evaluación honesta contra holdout.
- Decisión sobre las 6 monedas no-viables.
- Re-evaluación de priority del cap de sizing.
- Comunicación a Simon (con números honestos + decisiones tomadas).

---

## 5. Riesgos y preguntas abiertas

### R1 — Calibración del límite de tiempo per-cluster
El spec de A.4 v2 debe correr Triple Barrier con un grid de límites por cluster (por ejemplo: 3, 5, 8 horas para PENDLE/ADA; 10, 14, 20 horas para BTC/ETH) y reportar performance por combo. NO elegir un único valor a priori.

**Cota dura (no negociable):** el grid de time-limit es el ÚNICO grado de libertad nuevo en A.4 v2. Todo lo demás (multiplicadores ATR, scoring, regime detector, sizing, basket, cooldown, simetría LONG/SHORT) queda fijo en valores pre-registrados antes de evaluar contra holdout. Cualquier expansión post-hoc del grid bajo presión ("ya que estamos exploramos también X") expande N del Deflated Sharpe y vuelve el threshold de Gate 3 inalcanzable. Esta cota es lo que separa research disciplinada de curve-fit ex-post.

### R2 — Cost-survival check pre-registrado
Triple Barrier puede ganar en gross expectancy pero perder en net (post-costos) si el límite de tiempo agrupa exits en horas de baja liquidez. El spec debe pre-registrar (antes de correr) que el criterio de éxito incluye expectancy neta positiva + análisis cruzado de hora del día × razón de exit × costo.

### R3 — Robustez temporal sigue inconclusiva
La pregunta "¿la edge en train es real o artifact?" no se puede cerrar sin holdout. A.4 v2 acepta el riesgo. Si holdout falla → la edge era artifact → la conclusión es que el scoring también necesita rediseño. Resultado válido, blast radius limitado.

**Criterio de éxito pre-registrado (no negociable):** A.4 v2 se considera **exitoso si y solo si pasa el threshold pre-registrado del Gate 3 / DSR (de ticket #249)** sobre el holdout. Cualquier resultado por debajo de ese threshold es "falló", sin importar cercanía. **No hay "casi pasó", no hay sub-resultado rescatable post-hoc, no hay narrativa que cambie el verdict.** Esto es lo que separa research honesta de curve-fit ex-post.

### R4 — Decisión sobre las 6 monedas no-viables
Pendiente. Trackeada como acción específica que se cierra post-A.4 v2 con tres opciones consideradas (remover de lista master / rediseñar scoring per-coin / mantener con flag disabled).

### R5 — Integridad del holdout
Sistema tiene guards técnicos (lectura controlada + scanner que falla CI si algún módulo nuevo referencia el holdout fuera de protocolo). Cualquier código nuevo de A.4 v2 debe respetarlos.

### R6 — Comunicación a Simon
Pendiente, trackeada como acción específica que se cierra después de tener docs en main + re-baseline ejecutado + comment de deprioritization del cap de sizing posteado. Owner: Sam. Canal y formato a definir por Sam (Telegram, voice, email).

---

## 6. Lo que queda preservado del trabajo de hoy

A pesar del pivot, lo siguiente es código durable y reusable:
- **Herramienta de re-tune con cutoff temporal** — cualquier re-tune futuro (con cualquier exit logic) la usa.
- **Verificaciones de no-leakage** (assertions automáticas + manifest auditable) — protocolo aplicable a cualquier evaluación honesta.
- **Spec del modelo operacional manual-vs-auto** (mergeado a main hoy más temprano) — formaliza decisiones implícitas que el diagnóstico no afecta.
- **Disciplina de review formalizada** — el proceso del benchmark de exit logic generó protocolo de gates ex-ante (Gate 0–4 + sub-gate 2a) que A.4 v2 hereda. La auditabilidad del proceso (diff del doc, comment differential, correcciones post-review, cierres verificados) es output durable igual que la herramienta del re-tune; aplicable a cualquier validación futura.

---

## 7. Estado actual (resumen)

- **El sistema en producción NO está funcionando** rentablemente sobre las 10 monedas con el exit logic actual. Esto es ahora información explícita y documentada, no especulación.
- **Hay un path concreto adelante** (Triple Barrier per-cluster, basket reducida, re-baseline → spec → implementación → evaluación honesta).
- **Nada se promete antes de tiempo.** A.4 v2 puede fallar contra el holdout — y "fallar" significa "no pasa el threshold pre-registrado", no es un juicio post-hoc. Si falla, sabemos que el scoring también requiere trabajo. Si pasa, tenemos sistema honestamente validado.
- **La comunicación al cliente espera** a tener los números re-baselined disponibles. No queremos que Simon reciba números inflados ni decisiones provisionales.

---

**¿Preguntas o feedback?** El plan completo con detalle técnico (issue numbers, branch names, action IDs, dependencies) está disponible en el repo para el equipo técnico.
