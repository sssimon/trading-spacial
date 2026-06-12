# Spec — Dossier C: due-diligence de hechos citados (Exa + DeepSeek)

**Fecha:** 2026-06-12 · **Estado:** APROBADO (diseño validado con Samuel en sesión de brainstorming).
**Tipo:** instrumento de descubrimiento (observabilidad de hechos externos). NO emite veredicto de potencial. NO alimenta el eje-conducta ni el equity.
**Relacionado:** crítica ontológica de Voronov (2026-06-11) sobre la "Vista Valles"; pieza A (screener de vida, `#584`); pieza B (probe valle-calidad, `#585`). El dossier es la pieza **C** del trío valle A/B/C.

---

## 0. Qué es / qué NO es

**Es:** una herramienta que, sobre una candidata del screener A, trae **hechos verificables citados** del proyecto cripto (equipo, presencia, actividad, financiación) usando Exa.ai para recolectar y DeepSeek para extraer-y-estructurar. Cada hecho lleva su URL fuente. El operador (Samuel + Simón) juzga; el sistema recolecta y cita.

**NO es:**
- NO emite veredicto de "potencial", recomendación, score ni predicción. No hay casillero de opinión en el esquema.
- NO alimenta el eje-conducta, el equity, ni el ranking del screener A. No auto-filtra ni rankea la lista de candidatas.
- NO usa los endpoints `/answer` ni `/research` de Exa (esos *sintetizan* — reintroducirían el "juicio delegado" que Voronov marcó). Solo `/search` + `/contents` (hechos crudos citados); DeepSeek extrae.
- NO inventa datos: cada hecho debe estar anclado a una URL que Exa devolvió (candado anti-alucinación, §3).
- NO es per-tenant: el dossier de un proyecto es información pública, idéntica para todos.

## 1. La frontera ontológica (la línea de Voronov, cerrada por tres mecanismos)

El riesgo que Voronov nombró: un dossier "añade convicción sin poder → peor disciplina" (`size_usd` mayor, holds underwater más largos), y la función de relevancia del agente sería "juicio delegado disfrazado de recolección". El diseño lo neutraliza por construcción:

1. **Hechos sin veredicto:** esquema fijo sin campo de opinión/potencial/score. Imposible deslizar un juicio si no existe el casillero (§2). El prompt de DeepSeek prohíbe explícitamente opinar (§3).
2. **Separación de planos:** el dossier es observabilidad de hechos **externos**. NO alimenta el eje-conducta ni el equity ni el ranking de A. No actúa sobre nada — informa al humano. El eje-conducta sigue midiendo la disciplina del operador independientemente.
3. **Ausencia es información:** el estado `opaco` (proyecto sin equipo público, sitio caído, sin actividad) se muestra con la misma fuerza que los hechos positivos. El riesgo factual se hace visible, no se oculta. La convicción que el dossier pueda añadir queda contrapesada.

## 2. El esquema fijo (cada hecho con su fuente; sin casillero de opinión)

Output validado contra un schema Pydantic estricto (`extra='forbid'`). Cada campo: o un valor con su `fuente` (URL), o `estado: "no_encontrado"`.

- **`equipo`**: lista de `{nombre, rol, enlaces[], fuente}` + `identificado: bool` (¿equipo público o anónimo? — hecho, no juicio; el anonimato es una señal de riesgo factual).
- **`presencia`**: `{sitio_web, github, twitter, telegram_discord, whitepaper}`, cada uno `{url, activo: true|false|desconocido, fuente}`.
- **`actividad`**: `{ultimo_commit_github, ultimo_release, ultimo_post_anuncio}`, cada uno `{fecha, fuente}` — la "muerte fundamental" hecha factual (eco del filtro de muerte de A, capa fundamental).
- **`financiacion`**: lista de `{ronda, inversores[], monto_publico, fecha, fuente}` + `hitos[]` de `{descripcion, fecha, fuente}`.
- **`estado_general`** (DERIVADO de hechos, no juicio):
  - `"rastreable"` — se encontraron hechos en la mayoría de dominios.
  - `"opaco"` — equipo no identificado **+** sitio caído/ausente **+** sin actividad reciente. Viene con `no_encontrado_en: []` (la lista de qué se buscó y no apareció) — la ausencia de información es información de primer orden.
  - `"no_disponible"` — NO es un hallazgo: es un fallo técnico (Exa/DeepSeek caído o sin credencial). Se distingue SIEMPRE de `opaco` (§3).

## 3. Flujo + blindaje del "sin veredicto"

### 3.1 Flujo
1. **Trigger:** botón "Dossier" en la fila de `ValleysView` → `GET /dossier/{symbol}`.
2. **Resolución ticker→proyecto:** el símbolo es `ADAUSDT`; el query a Exa se arma sobre el activo base + contexto (`"ADA cryptocurrency project team founders funding"`) — Exa/DeepSeek resuelven el nombre ("Cardano") sin un mapeo manual de 200 tickers.
3. **Recolección (Exa):** una búsqueda por dominio (equipo, presencia, actividad, financiación) vía `/search` + `/contents` (`https://api.exa.ai`, header `x-api-key`). Devuelve contenido limpio **con sus URLs fuente**.
4. **Extracción (DeepSeek):** se le pasa el contenido crudo de Exa; extrae los hechos a los campos del esquema, cada uno con su URL fuente; `no_encontrado` si falta. Output JSON validado.
5. **Caché:** tabla `project_dossiers`, global, TTL 7 días. Foto fresca → se devuelve sin re-pagar Exa; `?refresh=true` regenera.
6. **UI:** panel/modal en español, cada hecho con su enlace clickeable, banner `generated_at`.

### 3.2 Blindaje (la frontera, en mecanismos verificables)
- **Prompt de extracción restringido:** "Sos un extractor de hechos. Llená estos campos solo con hechos presentes en el contenido, cada uno con su URL. Si un hecho no está, `no_encontrado`. **Prohibido: opinar, evaluar, recomendar, predecir, calificar.** No inventes datos ausentes."
- **Validación Pydantic `extra='forbid'`:** un campo de opinión que DeepSeek intente meter → output rechazado.
- **Candado anti-alucinación:** la `fuente` de cada hecho DEBE ser una URL que estaba en los resultados de Exa. Una cita a una URL fuera de ese set → el hecho se descarta (DeepSeek se la inventó). El dossier solo afirma hechos anclados a fuentes reales recolectadas.
- **Test de frontera:** verifica que el schema no tiene campos de opinión y que el prompt contiene la prohibición literal.

### 3.3 Manejo de errores (la distinción crítica)
- **Exa caído / rate-limited / sin `EXA_API_KEY`** → `estado_general: "no_disponible"` con razón ("no pude buscar"). NUNCA `opaco`. Un fallo técnico no es un hallazgo (eco F8 del repo: parcial = incorrecto, no incompleto).
- **Exa busca y no encuentra** → `opaco` legítimo.
- **DeepSeek caído** → `no_disponible`, no un dossier vacío.

## 4. Arquitectura (módulos aislados, sigue patrones del repo)

- **`research/exa_client.py`** — cliente fino read-only de Exa (`search`, `contents`), `EXA_API_KEY` del env, I/O aislado en un `_http` wrapper para mockear en tests (mismo patrón que `data/providers/binance_account.py`). Fail-closed si falta la key.
- **`research/dossier.py`** — orquesta: arma los queries por dominio, llama Exa, pasa el contenido a DeepSeek (reusa el cliente DeepSeek de `api/agent/providers`), valida el output contra el schema, aplica el candado anti-alucinación. Función central testeable con Exa + DeepSeek inyectados.
- **`research/schemas.py`** — los Pydantic del dossier (`extra='forbid'`).
- **`db/schema.py`** — migración `project_dossiers` (symbol TEXT, dossier_json TEXT, generated_at TEXT), PRAGMA-guarded idempotente, en el bloque del eje binance/research. Global (sin tenant_id).
- **`api/dossier.py`** — `GET /dossier/{symbol}` (caché-or-generate), read-only respecto al estado del usuario, no per-tenant. Lectura de caché vía `snapshot_connection`; escritura de caché vía `transaction` corta (el fetch de red va FUERA de la tx — regla del repo).
- **`frontend/src/components/ProjectDossier.tsx`** + `.module.css` + test — panel con el esquema estructurado, enlaces clickeables, badge de `estado_general` (incluido el badge "opaco" / "no disponible"), banner `generated_at`. Disparado desde un botón en `ValleysView`.

## 5. Caché, TTL, scope, candados

- **TTL 7 días.** Datos de proyecto cambian lento; `?refresh=true` fuerza fresco.
- **Scope global** (no per-tenant).
- **`EXA_API_KEY` fail-closed:** sin credencial → `no_disponible`, no rompe el dashboard. Local para desarrollo; prod (`trading.sdar.dev`) al desplegar, junto a `DEEPSEEK_API_KEY` (mismo patrón).
- **Llamada externa declarada:** el nombre del proyecto se envía a Exa (público, no sensible); se loguea `DOSSIER_FETCH symbol=...`.
- **El dossier NO toca el eje-conducta, el equity, ni el ranking de A.** Observabilidad externa pura.

## 6. Pruebas

- **`exa_client`:** mock del `_http` — request firmado con `x-api-key`, parseo de resultados, fail-closed sin key, manejo de rate-limit.
- **`dossier` (extracción):** con Exa + DeepSeek inyectados — extracción correcta al esquema; candado anti-alucinación (una cita fuera del set de Exa se descarta); `no_disponible` cuando Exa falla (≠ `opaco`); `opaco` cuando Exa devuelve vacío; validación rechaza un campo de opinión.
- **Frontera:** el schema no tiene campos de opinión; el prompt contiene la prohibición literal.
- **API:** caché-hit no re-llama Exa; `?refresh` sí; `no_disponible` cuando falta la key; sin per-tenant.
- **Frontend:** renderiza el esquema; badge "opaco" y "no disponible" distintos; cada hecho con su enlace; sin ningún texto de recomendación/score (test de ausencia).

## 7. Fuera de alcance (declarado)

- Tool conversacional del copiloto que invoque el dossier (posible add-on futuro; el output sería el mismo objeto estructurado, nunca prosa de opinión). Esta entrega es endpoint + botón sobre la fila.
- Mapeo manual ticker→nombre de proyecto (se resuelve vía query a Exa).
- Cualquier scoring, ranking o filtrado automático basado en el dossier (pisaría la frontera de Voronov).
- Integración del estado `opaco` como filtro de muerte fundamental del screener A (mencionado en el spec de A §2 como capa C; queda como follow-up, no se auto-aplica aquí).
- Datos tras login (LinkedIn/Instagram privados) — límite de cualquier buscador; el dossier trae lo públicamente indexable.
