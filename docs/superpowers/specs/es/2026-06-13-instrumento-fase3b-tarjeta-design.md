# El Instrumento — Fase 3b (la tarjeta de selección) · Diseño

**Fecha:** 2026-06-13
**Rama:** `feat/instrumento-fase3b-tarjeta`
**Parte de:** el instrumento completo. La última pieza — los **órganos de entrada** (A vivo + C dossier + D.1 niveles) cosidos en una vista por moneda.

## §0 — Qué es F3b, y la restricción que lo gobierna

F3b compone los tres sensores pre-entrada en **una tarjeta por moneda**: A (¿está viva y en rango?), C (dossier de fundamentales citados), D.1 (zonas S/R + precio vivo). Cada uno ya existe y ya afirma SOLO hechos, sin veredicto.

**La restricción central (revelada por el roster, Voronov):** la tarjeta es **donde se gasta la neutralidad de cada sensor**. Tres hechos neutrales yuxtapuestos generan un cuarto objeto que ninguno autorizó: **la correlación percibida** ("vivo + fundamentales limpios + precio en soporte" se lee como "comprá" — un veredicto que no se escribe en ningún campo, se escribe en el ojo). Es el riesgo del dossier ("añade convicción sin poder") consumado.

Por eso la decisión de arquitectura (roster **unánime: composición en frontend**, no un compositor backend):
> Un compositor backend produce UN objeto con UN status — reifica la correlación en el contrato (la firma). El frontend, con tres relojes, tres estados, tres latencias, mantiene **las costuras visibles**. Las costuras son la honestidad. El compositor firma el veredicto; el frontend solo exhibe.

**El instrumento que mide conducta no puede emitir la conducta que mide.** La tarjeta exhibe; no firma.

## §1 — Arquitectura

Una pieza backend mínima (A on-demand) + la tarjeta frontend que compone.

| Pieza | Archivo | Responsabilidad |
|---|---|---|
| A on-demand | `api/valleys.py` → `GET /valley-eval/{symbol}` | Reusa `_fetch_daily_bars` (de `api/levels.py`) + `evaluate_symbol` (puro, de `screener/valley_filter.py`). Devuelve los hechos de vida/rango de UNA moneda, o `no_disponible` si la red falla. |
| La tarjeta | `frontend/src/components/CoinCard.tsx` + `.module.css` | Toma un símbolo (tecleado o clic en la tabla de valles), llama A + D.1 + C en paralelo; cada bloque pinta cuando llega, con su propio estado/frescura. Dossier lazy. |
| Cliente | `frontend/src/api.ts` → `getValleyEval(symbol)` | Cliente del nuevo endpoint A. |
| Tipos | `frontend/src/types.ts` → `ValleyEval` | Tipo de la respuesta de A on-demand. |
| Cableado | `frontend/src/components/ValleysView.tsx` | Botón/entrada que abre la `CoinCard` para un símbolo. |

`GET /levels/{symbol}` (D.1) y `GET /dossier/{symbol}` (C) ya existen y se reutilizan tal cual.

## §2 — A on-demand: `GET /valley-eval/{symbol}`

Por qué A necesita un path por-símbolo: hoy A solo se expone como la FOTO del screener (`GET /valley-candidates`, sobre el universo). Pero `evaluate_symbol(symbol, bars)` es **puro** — solo le falta el fetch de velas. Halberg confirmó que el contrato de barras de `_fetch_daily_bars` (D.1, 365 días) satisface a A (necesita ≥120, lee hasta 365).

```python
@router.get("/valley-eval/{symbol}", summary="Evalúa vida + rango de UNA moneda (A on-demand)")
def get_valley_eval(symbol: str) -> dict:
    """A para un símbolo arbitrario: fetch de velas + evaluate_symbol (puro).
    Devuelve los hechos si está VIVA y EN RANGO, o un veredicto honesto de por
    qué no (no es un juicio de atractivo — son hechos de liveness). no_disponible
    si la red falla. Read-only, red fuera de tx, sin caché."""
    symbol = symbol.upper()[:20]
    try:
        bars = _fetch_daily_bars(symbol)   # reusa el fetch de D.1
    except (requests.RequestException, BinanceUnavailable) as e:
        log.warning("VALLEY_EVAL_NO_DISPONIBLE symbol=%s causa=%s", symbol, e)
        return {"symbol": symbol, "estado": "no_disponible"}
    cand = evaluate_symbol(symbol, bars)          # bars ya es el contrato puro
    if cand is None:
        # NO es candidata: viva-pero-no-en-rango, o no-viva. Reportar el hecho.
        vivo, razones = classify_liveness(bars)   # hechos de por qué
        return {"symbol": symbol, "estado": "ok", "candidata": False,
                "vivo": vivo, "razones_muerte": razones}
    return {"symbol": symbol, "estado": "ok", "candidata": True, **cand}
```

**Nota de contrato de barras:** `_fetch_daily_bars` (de `api/levels.py`) ya devuelve el dict del contrato puro (`open_time/open/high/low/close/volume/quote_volume`), que es exactamente lo que `evaluate_symbol`/`classify_liveness` consumen. No hace falta remapear. `BinanceUnavailable` también se importa de `api/levels.py`.

**Honestidad (no veredicto):** la respuesta reporta `candidata: true/false` y, si false, los `razones_muerte` (hechos: `volumen_bajo_piso`, `volumen_agonizante`, etc.) — describe POR QUÉ no es candidata, no si es "mala". Mismo estándar de A.

`no_disponible` (red caída) ≠ `candidata: false` (evaluó, no califica) — la misma distinción honesta de A/C/D.1.

## §3 — La tarjeta `CoinCard.tsx` (composición, costuras visibles)

Toma un `symbol` y dispara **tres llamadas independientes en paralelo**:
- A → `getValleyEval(symbol)` (rápido)
- D.1 → `getLevels(symbol)` (rápido)
- C → `getDossier(symbol)` (lento, Exa — **lazy**: se dispara al abrir, pinta cuando llega)

Cada bloque tiene su **propio estado** (cargando / ok / no_disponible) y pinta **independientemente** cuando su llamada resuelve. Un bloque caído muestra "sin datos" sin tocar los otros.

Layout — tres secciones **visualmente distintas**, apiladas, cada una con su propia etiqueta de frescura:

```
┌─ SOLUSDT ──────────────────────────────┐
│ VIDA (A)            · hace unos segundos │   ← bloque A
│   viva · en rango 18% · 9 semanas · …    │
│   (o: "no es candidata — volumen bajo")  │
├──────────────────────────────────────────┤
│ NIVELES (D.1)       · precio vivo         │   ← bloque D.1
│   precio 142.3 · techo 150 (+5.4%)        │
│   piso 138 (−3%) · zonas…                 │
├──────────────────────────────────────────┤
│ FUNDAMENTALES (C)   · cargando…/fecha     │   ← bloque C (lazy)
│   equipo: 9 · github activo · …           │
│   (o "sin datos")                         │
└──────────────────────────────────────────┘
```

**Anti-veredicto (la restricción de Voronov, hecha código):**
- **Cero score agregado, cero badge "comprá/buena", cero color que implique juicio.** No hay una cuarta línea que resuma los tres.
- Los tres bloques quedan separados, con sus propios timestamps — el operador ve tres hechos apilados, nunca una frase fusionada.
- Reutiliza los componentes existentes donde aplique: el bloque D.1 puede reusar `LevelsPanel`, el bloque C puede reusar `ProjectDossier`. El bloque A es nuevo (mínimo).

## §4 — Cableado en `ValleysView`

La tabla del screener ya lista candidatas con botones Dossier + Niveles. F3b añade:
- Un **input de símbolo** (teclear cualquier ticker) que abre la `CoinCard` — el valor real de la tarjeta (Cassian: "poder pegar un ticker y ver A+C+D.1 al instante").
- Opcional: clic en una fila de la tabla también abre su `CoinCard`.

Los botones Dossier/Niveles separados pueden quedarse o reemplazarse por la tarjeta unificada — decisión menor de UX en implementación; lo esencial es que la tarjeta exista y sea consultable por símbolo arbitrario.

## §5 — Pruebas

**Backend** (`tests/test_valley_eval_api.py`): `get_valley_eval` con fetch mockeado → `candidata: true` (hechos), `candidata: false` (con razones), `no_disponible` (red caída sin 500). Read-only, sin caché, sin escritura.

**Frontend** (`CoinCard.test.tsx`): los tres bloques mockeados pintan; un bloque `no_disponible` degrada solo (los otros siguen); **test anti-veredicto** — el `textContent` de la tarjeta NO contiene `comprá|buena|score|recomend|veredicto|potencial`; no hay una línea-resumen de los tres.

## §6 — Invariantes preservados
- **La tarjeta exhibe, no firma** (Voronov): cero veredicto compuesto; costuras visibles (tres estados, tres frescuras).
- **Composición en frontend** (roster unánime): no un compositor backend que reifique la correlación.
- **A on-demand es hechos, no juicio:** `candidata: false` reporta razones (hechos), no "malo".
- **Read-only, red fuera de tx, sin caché** en `valley-eval`; degradación honesta `no_disponible` (sin 500).

## §7 — Fuera de alcance (defer / delete)
- **Compositor backend `GET /valley/{symbol}`** — DEFER hasta un segundo consumidor (Cassian).
- **Cualquier score/ranking/badge de atractivo** — DELETE (la celda B del programa; viola la neutralidad).
- **Caché de `valley-eval`** — DELETE (A es barato; el screener-snapshot ya cubre el caso de universo).
