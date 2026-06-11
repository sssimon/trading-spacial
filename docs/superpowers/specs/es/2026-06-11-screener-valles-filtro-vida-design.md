# Spec — Vista "Valles" A: filtro de vida + consolidación geométrica

**Fecha:** 2026-06-11 · **Estado:** APROBADO (diseño validado con Samuel en sesión de brainstorming).
**Tipo:** instrumento de descubrimiento (observabilidad de hechos de mercado). NO es estrategia, NO emite claim de edge.
**Relacionado:** crítica ontológica de Aurelius Voronov (2026-06-11, ver §1); ley de los dos planos (`2026-06-09-integracion-eje-conducta-spec.md`); panel anti-survivorship E1-T0 (`.mex/programa/`).

---

## 0. Qué es / qué NO es

**Es:** un filtro que produce una lista **plana y neutral** de monedas spot USDT **vivas** (operables, no muertas) y **actualmente en consolidación geométrica** (en un valle). La lista existe para que el operador (Samuel + Simón) aplique su propio criterio de inversión, asistido después por el dossier de fundamentales (sub-proyecto C, separado).

**NO es:**
- NO rankea por "atractivo de entrada" ni "calidad de valle". Ese orden es un claim de mercado y vive en una celda del programa (sub-proyecto B, ver §1).
- NO sugiere entrada, NO emite señal, NO predice que ninguna moneda vaya a subir.
- NO usa colores de señal ni badges de "compra" en la UI — la presentación misma no debe contrabandear un juicio.
- NO corre sobre el panel congelado de 187 (ese es para falsificación honesta del sub-proyecto B); corre sobre el universo vivo de Binance con datos frescos (§3).
- NO es per-tenant: el universo de mercado es global, idéntico para todos los usuarios.

## 1. Frontera ontológica (la línea que mantiene esto honesto)

Tras la crítica de Voronov (2026-06-11), la idea original ("Vista Valles" como screener con score de atractivo) se descompuso en tres piezas de distinta naturaleza. Este spec cubre **solo A**.

| Pieza | Qué es | Plano | Estado |
|---|---|---|---|
| **A — Filtro de vida** (este spec) | Descarta muertas/ilíquidas; lista plana neutral de vivas en consolidación. | Observabilidad (hechos verificables hoy). | Construible ya. |
| **B — Celda "valle-calidad"** | El ranking por "qué tan buen valle es". Claim de mercado. | Programa Edición 1 — pre-registro + falsificación sobre el panel de 187. | Diferido; se abre como celda. |
| **C — Dossier de fundamentales** | Hechos verificables citados (equipo, enlaces, financiación, abandono). | Observabilidad de hechos externos. | Diferido; bloqueado por búsqueda web. |

**El principio rector:** A solo afirma dos cosas, ambas verificables en `t` sin predecir nada:
1. **"Está viva"** — hecho de liquidez/actividad presente.
2. **"Está geométricamente en rango"** — hecho descriptivo del precio reciente (osciló dentro de ±X% durante N semanas).

El salto a "esto es una oportunidad" lo da el humano. El salto a "este orden es mejor" va a B. La distinción clave validada con el operador: **"¿está viva?" es observabilidad; "¿va a subir?" es edge.** Constatar que un proyecto está abandonado (C) es un hecho de estado presente, no un juicio de potencial futuro.

**Nota de survivorship (Voronov §6):** A corre sobre monedas vivas en Binance a propósito — solo lo comprable importa para listar candidatas operables. El sesgo de supervivencia es un problema para *validar una estrategia* (B, que por eso usa el panel con delistadas), no para *listar lo comprable* (A). Son sustratos distintos por razones distintas.

## 2. Las dos capas del filtro de muerte

El operador pidió filtrar muertas tanto por mercado como por fundamentales. Son dos capas:

- **Muerte mecánica (A, este spec):** todo sale del OHLCV, sin internet.
- **Muerte fundamental (C, diferido):** proyecto abandonado (repo sin commits, sitio caído, equipo desaparecido). Requiere búsqueda web. C la marca **después**, en cascada sobre la lista de A. No se mezcla en A.

### Señales de muerte mecánica (todas elegidas por el operador)
1. **Volumen bajo un piso absoluto** — volumen diario USD < umbral mínimo (calibrar; arranque sugerido $500K/día). Sin liquidez real para entrar/salir.
2. **Volumen agonizante** — volumen en tendencia decreciente sostenida mes a mes. Distingue "descansa con vida" de "se muere".
3. **Velas planas / días sin actividad** — exceso de velas con rango ≈0 o días con muy pocos trades. Libro abandonado.
4. **Historia insuficiente** — recién listada o con pocos meses de data; no se puede juzgar consolidación. Se excluye hasta tener historia.

## 3. Universo + datos

- **Universo:** pares USDT spot **activos** en Binance ahora (vía `exchangeInfo`). Excluye stablecoins, fiat, apalancados (UP/DOWN/BULL/BEAR), y delistados (no comprables).
- **Datos:** klines recientes frescos de Binance (ventana a calibrar; arranque sugerido 6–12 meses, diario). NO el `program_ohlcv.db` congelado.
- **Frescura:** cada foto lleva `generated_at`; la UI muestra cuán reciente es (no finge tiempo real).

## 4. Consolidación geométrica (hecho descriptivo, no claim)

De las vivas, se queda con las que están en valle geométrico — descripción del presente, no afirmación de favorabilidad:
- **Rango estrecho sostenido:** el precio osciló dentro de ±X% durante N semanas (calibrar).
- **Volatilidad en percentil bajo propio:** la volatilidad actual está en la cola baja de su propia historia.
- **Distancia al máximo:** se reporta como **dato informativo** (columna), NO como criterio de filtro ni de orden (usarlo como "más lejos = mejor" sería claim → B).

## 5. Arquitectura (sigue patrones del repo)

### 5.1 Cálculo puro — `screener/valley_filter.py` (sin red, testeable)
- `classify_liveness(klines) -> (vivo: bool, razones: list[str])` — las 4 señales de muerte mecánica.
- `measure_consolidation(klines) -> {en_rango: bool, pct_rango: float, semanas: int, vol_percentil: float}` — la geometría del valle.
- `liquidity_value(klines) -> float` — volumen sostenido como hecho, para el orden neutral.

Cada función devuelve hechos, cero claims. Se prueba con fixtures de klines, offline.

### 5.2 Orquestación + fetch — `tools/run_valley_screener.py` (la red, fuera de todo cálculo)
Enumera el universo spot vivo, baja klines recientes, aplica el filtro puro, escribe `data/valley_candidates.json` (foto regenerable; mismo patrón que `data/symbols_status.json`). El fetch de 200+ símbolos es pesado → corre como comando, no por request.

Estructura del JSON:
```json
{
  "generated_at": "2026-06-11T...Z",
  "coverage": {"evaluated": 198, "universe": 210, "complete": false},
  "candidates": [
    {"symbol": "XYZUSDT", "price": ..., "pct_rango": ..., "semanas_consolidando": ...,
     "volumen_usd_dia": ..., "distancia_ath_pct": ..., "razones_vida": []}
  ]
}
```

### 5.3 API — `GET /valley-candidates`
Lee el JSON y lo devuelve. Read-only, **no per-tenant** (mercado global), lectura terminal simple (`snapshot_connection` no aplica — es un archivo, no DB; lectura directa del JSON).

### 5.4 Frontend — vista "Valles"
- Registrada en `LeftRail.tsx` (sección análisis), `App.tsx` (`mainTab`), `types-ui.ts` (`MainTab` union) — igual que `KillSwitchView`/`HistorialView`.
- `ValleysView.tsx` + helper puro + tests.
- Tabla con columnas de hechos: símbolo, precio, % del rango, semanas en consolidación, volumen USD/día, distancia al máximo. **Orden neutral por liquidez** (hecho), nunca por "calidad de valle".
- **Sin badges de compra, sin colores de señal.** Presentación neutral. Banner con `generated_at` y cobertura.
- El dossier (C) se engancha después como acción sobre una fila.

### 5.5 Cadencia
Manual primero: `python -m tools.run_valley_screener` refresca la foto (igual que el sync de Binance arrancó manual). El estado de consolidación cambia en semanas; no urge automatizar. Auto-enganche al hilo del scanner (contador de ciclos, como el backup diario) = mini-PR futuro separado.

## 6. Manejo de errores

- Un símbolo que falla al bajar klines se **omite con su razón**; no tumba el screener (fallo parcial no corrompe el resultado).
- Rate-limit (429/418) → backoff; si persiste, el JSON se escribe con `coverage.complete=false` y el % evaluado, en vez de fingir foto completa (honestidad de cobertura, eco del panel E1-T0).
- `generated_at` siempre presente para que la UI no finja tiempo real.

## 7. Pruebas

- **Cálculo puro (el grueso):** fixtures de klines por señal de muerte (volumen bajo piso, agonizante, velas planas, recién listada) → cada una excluida con su razón; valle real pasa, tendencia/volátil no pasa.
- **Caso límite nombrado por el operador:** "descansa con vida" (volumen bajo estable) vs "agoniza" (volumen bajo y cayendo) — test dedicado que fija la línea.
- **Orden neutral:** la lista se ordena por liquidez, nunca por calidad de valle.
- **Orquestación:** símbolo que falla → omitido, no tumba el run; cobertura incompleta marcada en el JSON.
- **API + frontend:** endpoint devuelve la foto; la vista no renderiza ningún badge de compra (test de ausencia).

## 8. Fuera de alcance (declarado)

- Ranking por calidad de valle (B — celda del programa, pre-registro + falsificación).
- Dossier de fundamentales / búsqueda web (C — bloqueado por proveedor de búsqueda web).
- Auto-cadencia en el hilo del scanner (mini-PR futuro).
- Sizing, señales de entrada, cualquier automatización de la decisión (pisaría las trampas #316/#357).
- Per-tenant / personalización del universo (el mercado es global).
