---
name: generar-un-dossier
description: Runbook para generar un dossier de due-diligence de hechos citados de un proyecto cripto — Exa /search + DeepSeek extracción, sin veredicto, caché global TTL 7d.
triggers:
  - "dossier"
  - "due-diligence"
  - "GET /dossier"
  - "botón Dossier"
  - "Dossier C"
  - "hechos citados"
last_updated: 2026-06-12
---

# Pattern: Generar un dossier de due-diligence de un proyecto cripto

## Propósito

Producir un dossier de hechos externos citados sobre un proyecto cripto para apoyo al análisis humano. El dossier recopila hechos públicamente verificables (equipo, tokenomics, estado del protocolo, red, mercado) sin emitir ningún juicio de inversión. Es observabilidad de hechos externos, no estrategia.

## Cuándo usar

- Cuando el operador hace clic en el botón "Dossier" en la vista Valles sobre una candidata del screener A.
- Cuando se llama directamente a `GET /dossier/{symbol}`.
- Antes de analizar manualmente una candidata — para tener hechos de contexto citados antes de abrir posición.

## Pasos

### Paso 1 — Solicitar el dossier

**Vía UI:** botón "Dossier" en la vista Valles → dispara `GET /dossier/{symbol}`.

**Vía API directa:**

```bash
curl http://localhost:8000/dossier/BTCUSDT
```

### Paso 2 — Lógica del endpoint

El endpoint `GET /dossier/{symbol}` (en `api/dossier.py`) sigue este flujo:

1. Consulta caché global en `project_dossiers` (tabla en `signals.db`, global: `tenant_id IS NULL`).
2. Si existe fila con `generated_at` dentro del TTL (7 días): devuelve la caché directamente.
3. Si no: genera el dossier:
   a. Llama a Exa `/search` con el nombre del proyecto como query (solo `/search` — nunca `/answer` ni `/research` que sintetizan sin citado).
   b. Pasa los fragmentos de resultados a DeepSeek para extracción estructurada de hechos.
   c. Cada hecho extraído DEBE anclar a una URL que Exa devolvió — si no hay URL, el hecho se descarta (candado anti-alucinación).
   d. El resultado se persiste en `project_dossiers` con `generated_at = now()`.
4. Devuelve el dossier como JSON con la estructura de `DossierSchema`.

**Nota de ejecución:** la red (llamadas a Exa + DeepSeek) corre FUERA de cualquier transacción de base de datos. La escritura en caché es la única operación transaccional.

### Paso 3 — Interpretar el resultado

```json
{
  "symbol": "ADAUSDT",
  "status": "ok",
  "generated_at": "2026-06-12T00:00:00Z",
  "facts": [
    {
      "category": "equipo",
      "text": "Fundado por Charles Hoskinson en 2015.",
      "source_url": "https://..."
    }
  ]
}
```

Cuando el proyecto existe pero Exa no encontró fuentes relevantes, el status es `opaco` — buscó pero no encontró. Cuando hubo fallo técnico (Exa caído, `EXA_API_KEY` ausente, DeepSeek inaccesible), el status es `no_disponible`.

## Gotchas

- **Es OBSERVABILIDAD de hechos externos, NO juicio:** el esquema `DossierSchema` no tiene campo de opinión, veredicto, puntuación ni recomendación. La frontera Voronov es estructural — el dossier no puede opinar porque el campo no existe (2026-06-11).

- **`opaco` ≠ `no_disponible`:** `opaco` significa "Exa buscó y no encontró fuentes relevantes para este proyecto" — es un resultado válido. `no_disponible` significa fallo técnico (Exa caído, sin `EXA_API_KEY`, DeepSeek inaccesible). NUNCA confundir: un `opaco` no se reintenta (es la respuesta honesta); un `no_disponible` NO se cachea y el próximo request vuelve a intentar.

- **Candado anti-alucinación:** cada hecho en el dossier debe anclar a una URL que Exa devolvió efectivamente. Si DeepSeek extrae un hecho que no matchea ningún resultado de Exa, se descarta. No hay hechos sin URL de respaldo.

- **La red (Exa + DeepSeek) corre FUERA de la transacción:** llamar a APIs externas dentro de un `with transaction()` bloquea el WAL durante toda la latencia de red. El patrón correcto es: (1) llamar Exa, (2) llamar DeepSeek, (3) abrir `with transaction()` solo para persistir el resultado.

- **`EXA_API_KEY` fail-closed:** si la variable de entorno `EXA_API_KEY` no está configurada, el cliente Exa lanza error y el endpoint retorna `no_disponible` (nunca finge `opaco`). Verificar que la key está en `.env` o en el EnvironmentFile del server antes de usar en producción.

- **Los `no_disponible` NO se cachean:** un dossier con status `no_disponible` no se escribe en `project_dossiers`. El próximo request generará un nuevo intento real. Cachear un fallo técnico como si fuera un resultado contaminaría la caché con datos de error.

- **Solo `/search` de Exa, nunca `/answer` ni `/research`:** los endpoints `/answer` y `/research` de Exa sintetizan sin exponer las URLs individuales de respaldo, lo que rompe el candado anti-alucinación. El dossier usa exclusivamente `/search` y extrae los hechos con DeepSeek sobre los fragmentos crudos.

- **Caché global (no per-tenant):** la tabla `project_dossiers` usa `tenant_id IS NULL` — el dossier de un símbolo es compartido entre todos los tenants. No hay dossiers per-tenant. Esto es intencional: los hechos externos de un proyecto son los mismos para todos los usuarios.

## Verify Checklist

Antes de dar el dossier por válido o mergear cambios en `api/dossier.py` / `db/dossiers.py` / `research/`:

- [ ] El schema (`DossierSchema`) no tiene campo de opinión, veredicto, puntuación ni recomendación.
- [ ] El prompt de DeepSeek incluye la prohibición explícita de emitir juicio o veredicto.
- [ ] `opaco` y `no_disponible` están implementados como valores distintos — ni uno se puede confundir con el otro ni con `ok`.
- [ ] Los `no_disponible` NO se escriben en caché (`project_dossiers`).
- [ ] La caché usa `tenant_id IS NULL` (global, no per-tenant).
- [ ] Las llamadas a Exa y DeepSeek ocurren FUERA de cualquier `with transaction()`.
- [ ] El cliente Exa usa solo `/search` — ninguna llamada a `/answer` o `/research` en el código.
- [ ] Los tests del dossier pasan: `python -m pytest tests/test_exa_client.py tests/test_dossier_schema.py tests/test_dossier_build.py tests/test_project_dossiers_migration.py tests/test_dossier_api.py -q` → todos verdes.
