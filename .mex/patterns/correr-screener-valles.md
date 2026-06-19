---
name: correr-screener-valles
description: Runbook para refrescar la foto de candidatas del screener de valles — filtro de vida + consolidación geométrica como observabilidad pura (lista neutral, sin ranking por calidad).
triggers:
  - "screener de valles"
  - "valley_candidates"
  - "valley-candidates"
  - "Vista Valles"
  - "consolidación geométrica"
  - "correr el screener"
last_updated: 2026-06-11
---

# Pattern: Refrescar la lista de candidatas del screener de valles

## Propósito

Actualizar la foto de monedas vivas en consolidación para análisis humano. El screener aplica dos filtros secuenciales sobre el universo VIVO de Binance: (1) filtro de vida (volumen y precio dentro de umbrales operacionales), y (2) filtro de consolidación geométrica (rango precio comprimido). El resultado es una lista plana y neutral — observabilidad, no estrategia.

## Cuándo usar

Cuando querés actualizar la lista de monedas en consolidación disponible para análisis humano en la vista "Valles" del dashboard. Típicamente:

- Antes de una sesión de análisis manual.
- Cuando sospechás que el universo de candidatas cambió (nuevas monedas listadas, cambios de volumen macro).
- Para confirmar qué símbolos están "en rango" antes de investigar una posición potencial.

## Pasos

### Paso 1 — Correr el screener

```bash
python -m tools.run_valley_screener
```

El script:
1. Consulta el universo VIVO de Binance (futurables USDT perpetuos).
2. Aplica filtro de vida (`min_volume_usdt`, `min_price_usdt`).
3. Aplica filtro de consolidación geométrica (rango % en ventana de N barras).
4. Escribe el resultado en `data/valley_candidates.json`.
- La misma pasada también escribe `data/alt_season.json` (régimen de mercado) y hace 1 llamada a CoinGecko `/global` para la dominancia (degradación elegante si falla). Lector: `GET /alt-season`.

### Paso 2 — Verificar la salida

```bash
python -c "import json; d=json.load(open('data/valley_candidates.json')); print(d['generated_at'], d['coverage'])"
```

El JSON tiene la forma:

```json
{
  "generated_at": "2026-06-11T00:00:00Z",
  "coverage": { "total": 120, "screened": 98, "complete": true },
  "candidates": [...]
}
```

### Paso 3 — La vista del dashboard lo refleja automáticamente

La vista "Valles" del dashboard lee `GET /valley-candidates`. El endpoint sirve el JSON si existe, o retorna `{"candidates": [], "generated_at": null}` si no se ha corrido todavía (la UI muestra "sin foto").

No se necesita reiniciar el backend — el endpoint lee el archivo en cada request.

## Gotchas

- **Es OBSERVABILIDAD, NO estrategia:** la lista es plana y neutral. El ranking por calidad de valle (¿qué tan "limpio" es el canal?) es la celda B del programa y NO se implementa aquí — ese es un claim de mercado que requiere falsificación propia (Voronov 2026-06-11). Nunca añadir badges de compra a la UI de valles.

- **Corre sobre el universo VIVO de Binance, no sobre `program_ohlcv.db`:** el screener consulta Binance en tiempo real (o con la latencia del intervalo de refresco). `program_ohlcv.db` es el panel congelado de datos para falsificación del programa — no lo uses como fuente para el screener de valles; son fuentes distintas con propósitos distintos.

- **Cobertura incompleta se reporta honestamente:** si el screener no pudo obtener datos de algunos símbolos (timeout, error 429, símbolo delisted en medio del scan), el JSON reporta `"complete": false` y lista los símbolos faltantes. Nunca se finge foto completa cuando hay gaps (eco F8 — parcial es incorrecto). Inspeccionar los warnings del log antes de dar la foto por buena.

- **La UI no lleva badges de compra por diseño:** la vista "Valles" es un visor de estado de consolidación, no un generador de señales. Añadir cualquier indicador de "comprar / entrar" rompe la separación observabilidad ↔ señal y contamina la foto neutral.

- **El screener es stateless entre corridas:** cada `python -m tools.run_valley_screener` sobrescribe `data/valley_candidates.json` completamente. No hay historial de corridas previas. Si querés comparar fotos en el tiempo, guardá manualmente una copia con timestamp antes de re-correr.

> **SP2 / deploy:** el contrato del candidato cambió (de `pct_rango`/`semanas_consolidando` a
> `pos_in_30d_range`/`rsi14`/…). El snapshot persistido `data/valley_candidates.json` queda
> incompatible hasta que el `screener_loop` lo regenere. El deploy DEBE forzar una regeneración
> (correr `python -m tools.run_valley_screener` o esperar un ciclo del loop) al activar el front
> nuevo; si no, `/valley-candidates` sirve campos viejos y el front lee `undefined` → "NaN%".

## Verify Checklist

Antes de dar la foto por válida:

- [ ] `data/valley_candidates.json` existe y tiene `generated_at` con un timestamp reciente.
- [ ] El campo `coverage` es coherente: `screened <= total` y, si `complete=false`, existe la lista de símbolos faltantes.
- [ ] La vista "Valles" del dashboard muestra la lista actualizada (no muestra "sin foto").
- [ ] No hay símbolos en `candidates` con `last_price=0` o volumen=0 — eso indica que el filtro de vida falló silenciosamente.
- [ ] Los tests puros del filtro pasan: `python -m pytest tests/test_valley_filter.py -q` → todos verdes (sin `@pytest.mark.network`).
