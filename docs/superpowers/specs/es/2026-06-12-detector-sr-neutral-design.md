# D.1 — Detector neutral de soporte/resistencia · Diseño

**Fecha:** 2026-06-12
**Pieza:** D.1 del trío valle (A screener / C dossier / **D detector S/R**).
**Rama:** `feat/sr-level-detector-d1`

## §0 — Qué es y qué no es

D.1 es un **detector neutral de niveles de soporte/resistencia**. Dado un símbolo,
detecta zonas S/R desde las velas diarias y ubica el **precio en vivo** respecto a
ellas. Afirma **solo hechos observables**; nunca emite veredicto, ranking ni consejo.

Hereda la disciplina de la pieza A (`screener/valley_filter.py`): cada campo es un
hecho derivado de las velas, no un juicio. El estándar es el motivo de `pct_rango`
o `semanas` en `valley_filter` — un número que *deriva* de las barras, no que las
reemplaza.

**En qué eje se cobra cada campo** (marco eje-conducta,
`docs/superpowers/specs/es/2026-06-09-integracion-eje-conducta-spec.md`): cada hecho
que D.1 reporta se cobra en el **eje velas** (esas velas giraron ahí, legible en `t`).
Nada en D.1 se cobra en el eje de la multitud (dónde "está" la gente) — ese mundo no
lo observamos (no hay order book histórico, solo 1 snapshot vivo). Esta es la razón de
la decisión central del §2 sobre el "punto psicológico".

**Lo que D.1 NO hace** (fronteras de alcance):
- No puntúa la "calidad" de una zona. `toques` es un conteo (hecho), no un score.
- No rankea símbolos por atractivo. Eso es la celda B del programa.
- No dice "compra/vende aquí". No badge, no push. Eso es D.2 (fast-follow, fuera de alcance).
- No persiste niveles. Eso es D.4 (deferred, fuera de alcance).
- No reubica un nivel sobre un número redondo (ver §2).

## §1 — Decisión central: zona, no punto psicológico

Un nivel S/R es **una zona** (una banda de precio), no un punto. La definición del
operador: un nivel es "un punto en el gráfico diario donde hubo cambio de trayectoria"
(un **pivote**: vela cuyo máx/mín supera a sus vecinas). Varios pivotes caen cerca en
precio → se agrupan en una **zona** `[precio_bajo, precio_alto]`.

El operador propuso situar el valor en el **punto psicológico** (el número redondo que
la multitud mira). La junta (Voronov + Null Vale) lo rechazó por **error de tipo**, no
de precisión:

- La banda de pivotes se cobra en el eje velas (observable). El número redondo se
  cobra en otro mundo (las cabezas de la multitud) que el detector nunca lee. Anclar
  ahí es fabricar una observación de algo que no podemos ver.
- Colapsar una zona (un intervalo) a un punto (un escalar) **descarta el ancho** —
  precisamente la información que constituye el hecho y donde el operador se sienta a
  esperar la ola.
- "Soporte: 65.000" cuela una presuposición de continuidad de la multitud (que
  volverán a defender *ese* número exacto): predecir la racha, prohibido.

**Reconciliación adoptada:** el valor representativo legible se **deriva de las velas**
(la mediana de los precios de pivote del clúster = `centro`), no del número redondo. Y
si la banda contiene un número redondo notable, se **anota como confluencia observable**
(`confluencia_redondo`) — sin reubicar el nivel sobre él. Esto honra la intuición
psicológica del operador como hecho de coincidencia, no como ancla fabricada.

## §2 — Arquitectura

Tres piezas, espejo de A y C. `screener/` se mantiene **puro** (sin red); la red vive
en el endpoint, como en C (`api/dossier.py`).

| Pieza | Archivo | Naturaleza |
|---|---|---|
| Detector puro | `screener/sr_levels.py` | Sin red, sin DB. Velas diarias → zonas. Hermano de `valley_filter.py`. |
| Endpoint | `api/levels.py` → `GET /levels/{symbol}` | Trae velas + precio vivo (red **fuera de tx**), corre el detector, ubica. **Sin caché, sin DB.** |
| Vista mínima | `frontend/src/components/LevelsPanel.tsx` | Botón en la fila del símbolo. Muestra bandas + precio vivo. Sin badge, sin color "bueno". |

El fetch de klines + precio vivo va **local** en `api/levels.py` (igual que `dossier.py`
guarda su `_http_post` local), para no acoplar red dentro del paquete puro `screener/`.

## §3 — El detector puro (`screener/sr_levels.py`)

Contrato de barras idéntico al de `valley_filter`: `list[dict]` diarias ascendentes con
claves `{open_time, open, high, low, close, volume, quote_volume}`. El detector usa
`high` (pivote-alto), `low` (pivote-bajo) y `close` (precio de referencia para tests).

### §3.1 — Detección de pivotes

`_pivots(bars, k) -> tuple[list[float], list[float]]` → (precios de pivote-alto, precios de pivote-bajo).

- Una vela `i` es **pivote-alto** si `bars[i].high` es estrictamente mayor que el `high`
  de las `k` velas a su izquierda y a su derecha (`bars[i-k..i-1]` y `bars[i+1..i+k]`).
- Una vela `i` es **pivote-bajo** si `bars[i].low` es estrictamente menor que el `low`
  de las `k` velas a cada lado.
- **Las últimas `k` velas se excluyen**: un pivote necesita `k` velas confirmatorias a
  la derecha, y todavía no existen. Esto evita look-ahead y pivotes prematuros.
- Las primeras `k` velas también se excluyen (sin `k` velas a la izquierda).
- Empates: la comparación estricta (`>` / `<`) significa que una meseta plana no
  produce pivote. Es conservador a propósito (no inventa giros donde el precio no giró).

### §3.2 — Agrupación en zonas

`_cluster(precios, tipo, tol_pct, min_touches) -> list[dict]`.

- Ordena los precios de pivote ascendentes.
- Agrupa de forma codiciosa: un precio entra al clúster actual si está dentro de
  `tol_pct` del **centro corriente** del clúster (`abs(p - centro) / centro <= tol_pct`);
  si no, abre un clúster nuevo.
- Cada clúster con `toques >= min_touches` produce una zona:
  - `tipo`: `"resistencia"` (de pivote-altos) o `"soporte"` (de pivote-bajos).
  - `precio_bajo` = min de los precios del clúster; `precio_alto` = max.
  - `centro` = **mediana** de los precios del clúster (derivado, no redondo).
  - `toques` = cantidad de pivotes en el clúster.
  - `confluencia_redondo` = lista de números redondos dentro de `[precio_bajo, precio_alto]` (§3.3).

### §3.3 — Confluencia con número redondo

`_round_confluence(precio_bajo, precio_alto) -> list[float]`.

- Paso redondo a **un orden por debajo** de la magnitud:
  `step = 10 ** (floor(log10(precio_alto)) - 1)` (p.ej. precio ~69.000 → step 1.000,
  captura 69.000; precio ~65.000 → step 1.000, captura 65.000; precio ~6.0e-6 →
  step 1e-7). La granularidad es **calibrable**.
- Devuelve los múltiplos de `step` que caen en `[precio_bajo, precio_alto]`. Como las
  bandas son estrechas (`CLUSTER_TOL_PCT = 0.75%`), en la práctica esto da 0–1 redondos
  por zona; no es ruido.
- Es una **anotación**: no modifica `centro` ni la banda.

### §3.4 — Ubicación del precio vivo

`locate_price(price, zonas) -> dict` → hechos geométricos, no consejo.

- `dentro_de` = la zona que satisface `precio_bajo <= price <= precio_alto`, o `null`.
  Si hay varias (raro), la de mayor `toques`.
- `techo` = zona con menor `centro` tal que `precio_bajo > price` (la inmediata por
  encima), con `dist_pct = (centro - price) / price * 100`. `null` si no hay.
- `piso` = zona con mayor `centro` tal que `precio_alto < price` (la inmediata por
  debajo), con `dist_pct = (centro - price) / price * 100` (negativo). `null` si no hay.

### §3.5 — Función de orquestación pura

`detect_levels(bars) -> list[dict]`: corre `_pivots` → `_cluster` (altos y bajos) →
devuelve las zonas ordenadas por `centro` ascendente. Sin red. Es la única función que
el endpoint llama además de `locate_price`.

### §3.6 — Constantes de arranque (calibrables)

```python
PIVOT_REACH      = 3        # velas a cada lado para confirmar un giro
CLUSTER_TOL_PCT  = 0.0075   # 0.75% → pivotes más cercanos = misma zona
LOOKBACK_DAYS    = 365      # un año de velas diarias (lo pide el endpoint)
MIN_TOUCHES      = 2        # zona defendida ≥2 veces (=1 mostraría cada giro suelto)
```

## §4 — El endpoint (`api/levels.py`)

`GET /levels/{symbol}`. Read-only, **no per-tenant** (los niveles de un símbolo son
globales). Todo el flujo corre **fuera de cualquier transacción**; no toca DB.

1. Normaliza `symbol`: `.upper()` y recorte defensivo de longitud (`symbol[:20]`), como
   `dossier.py`.
2. Trae velas diarias: `/api/v3/klines`, `interval=1d`, `limit=LOOKBACK_DAYS`.
3. Trae precio vivo: `/api/v3/ticker/price?symbol=`.
4. Corre `detect_levels(bars)` + `locate_price(price_live, zonas)`.
5. Devuelve el payload (§4.1). **Nunca cachea, nunca toca DB** — el precio es vivo, se
   computa fresco cada request (D.4 persistencia está fuera de alcance).

El fetch se aísla en helpers privados (`_fetch_daily_bars`, `_fetch_live_price`) para
mockear en tests, como `_http_post` en `dossier.py`.

### §4.1 — Payload

```jsonc
{
  "symbol": "BTCUSDT",
  "estado": "ok",                 // "ok" | "no_disponible"
  "generated_at": "2026-06-12T12:00:00+00:00",
  "price_live": 67230.0,
  "zonas": [
    {"tipo":"resistencia","precio_bajo":69000,"precio_alto":69200,
     "centro":69100,"toques":4,"confluencia_redondo":[69000]},
    {"tipo":"soporte","precio_bajo":64800,"precio_alto":65400,
     "centro":65100,"toques":3,"confluencia_redondo":[65000]}
  ],
  "ubicacion": {
    "dentro_de": null,
    "techo": {"centro":69100,"dist_pct":2.78},
    "piso":  {"centro":65100,"dist_pct":-3.17}
  }
}
```

### §4.2 — Fallo (contrato de honestidad de A y C)

Si Binance falla (red, 429/418, símbolo inválido HTTP 400, cualquier no-200) → **no
500**. Devuelve `estado:"no_disponible"` con `price_live:null`, `zonas:[]` y `ubicacion`
con las tres claves en `null`. Símbolo inexistente → `no_disponible` (no inventa
niveles). La UI muestra "sin datos", como el screener sin foto.

## §5 — Frontend (`LevelsPanel.tsx`)

Mínimo, espejo de `ProjectDossier.tsx` (se cuelga de la misma vista de valles).

- Botón **"Ver niveles"** en la fila del símbolo, junto al de dossier.
- Al abrir: `GET /levels/{symbol}`, y muestra:
  - el precio vivo arriba,
  - las zonas como **bandas** (`precio_bajo → precio_alto`) con `centro` y `toques`,
    ordenadas por precio (resistencias arriba del precio, soportes abajo),
  - `techo` y `piso` resaltados con su distancia %,
  - `confluencia_redondo` como etiqueta discreta dentro de la banda.
- **Sin color bueno/malo, sin badge, sin flechas de acción.** Solo los hechos.
- `estado:"no_disponible"` → "sin datos ahora".

## §6 — Pruebas (TDD, como `valley_filter`)

**Detector puro** (`tests/test_sr_levels.py`, sin red):
- pivotes correctos en serie sintética con giros conocidos;
- exclusión de las últimas `k` velas (no look-ahead) y de las primeras `k`;
- meseta plana no produce pivote (comparación estricta);
- agrupación: pivotes dentro de `tol` → una zona; fuera de `tol` → dos zonas;
- `centro` = mediana de pivotes del clúster (y **distinto** del número redondo cuando
  los pivotes no son redondos);
- `toques` correcto; `MIN_TOUCHES` filtra giros sueltos;
- `confluencia_redondo` detecta el redondo dentro de banda y **no** altera `centro`;
- `locate_price`: precio entre zonas (techo+piso), dentro de zona (`dentro_de`), sin
  zonas (las tres en `null`), y sin techo o sin piso.

**Endpoint** (`tests/test_levels_api.py`, fetch inyectado/mockeado):
- payload `estado:"ok"` con zonas y ubicación;
- Binance caído → `no_disponible` sin 500;
- símbolo inválido (HTTP 400) → `no_disponible`;
- no se abre ninguna transacción ni se escribe DB (el endpoint no importa `transaction`).
