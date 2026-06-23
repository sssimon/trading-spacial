# Gate de exposición por régimen (alt-season) — Diseño

**Fecha:** 2026-06-23
**Rama:** `feat/alt-season-gate`
**Estado:** diseño revisado tras crítica adversarial (serrano + halberg), pendiente de plan.

> **Revisión adversarial (2026-06-23):** este spec fue auditado por `adrian-serrano` (análisis de
> spec) y `marcus-halberg` (factibilidad runtime) ANTES de implementar. Encontraron 3 BLOCKERS +
> 6 HIGH, todos de **cableado y contrato** (no de política). Esta versión los incorpora. Los
> hallazgos diferidos al plan están en §Items abiertos.

## Objetivo

Hacer que el régimen de mercado (`alt-season`) **module la exposición a alts**: cuando el
clima es claramente adverso (`btc`), las candidatas alt **no afloran** (Valles) y las señales
de entrada en alts **no se emiten** (scanner); cuando es ambiguo de verdad (`mixto` por empate)
se muestran pero marcadas; cuando es favorable (`alts`) pasan normal. El operador (en Valles, el
papá) puede **destapar lo escondido**. Una sola política pura compartida, dos motores que la
consultan, calibración de umbrales **sobre la marcha** (sin estudio bloqueante, sin fase shadow).

## Contexto verificado (por qué esto y no otra cosa)

- **No hay edge de selección per-coin.** El estudio multi-régimen 2020-2025
  (`data/retune/2026-06-18-setup-edge-multiregimen/`, 455k filas) probó que ni la firma de
  debilidad ni el momentum le ganan al azar de alts en ningún régimen. El único eje con señal fue
  el **régimen**. "Conectar el régimen al trade" = modular la selección/señal que ve el operador.
- **El sistema no auto-ejecuta** (`.mex/context/architecture.md:139`): emite señales, el operador
  ejecuta. El gate filtra **visibilidad/emisión**, nunca ejecuta.
- **El detector de régimen ya existe.** `regime/alt_season.py::compose_regime` emite
  `estado ∈ {alts, mixto, btc}` + `votos.vivos` por voto de 3 componentes. Se computa en la pasada
  del screener (`tools/run_valley_screener.py`, owner = `screener_loop`, `SCREENER_INTERVAL_SEC=21600`
  = 6h), se persiste en `data/alt_season.json` (escritura **atómica**, `run_valley_screener.py:159`),
  y se sirve en `GET /alt-season`. **Hoy está desconectado del trade** (doctrina anti-veredicto).
- **La frescura NO se persiste en el snapshot.** Es una propiedad **derivada en tiempo de lectura**
  (`generated_at` vs `umbral_seg` vía `freshness.LiveSnapshot`, `api/alt_season.py:50`). El único
  lector hoy vive en un request HTTP — `scan()` del scanner **no abre** `alt_season.json`
  (verificado: grep sin matches en `btc_scanner.py`).
- **Los 6 umbrales de alt-season son PROVISIONALES, sin calibrar** (`regime/alt_season.py:19-25`).

## Decisiones de diseño (tomadas en brainstorming)

1. **Significado:** alt-season como **gate de exposición**, no scoring per-coin ni sizing.
2. **Motores:** **ambos** — screener de Valles y scanner consultan una política pura compartida.
3. **Comportamiento:** **graduado por estado** (`alts`/`mixto`/`btc`).
4. **Enforcing:** **esconde en mal clima desde el arranque** (no fase shadow). Calibración
   **sobre la marcha**: umbrales config-driven + log de auditoría + señal de daño + reversa.
5. **Honestidad:** lo escondido es **destapable** en Valles (UX). En el scanner queda **auditable**
   (asimetría declarada — ver §Honestidad).

## No-negociables respetadas

- **#4 (RISK_PER_TRADE fijo, sin scalers):** el gate es filtro de **selección/visibilidad**, NO
  multiplicador de sizing. No toca `RISK_PER_TRADE` ni `size_mult`.
- **#8 (freshness owner + LiveSnapshot):** el gate **lee estado vivo cross-proceso** (el snapshot
  de régimen). Para el screener es trivial (lo computó en la misma pasada, edad ~0). Para el
  **scanner es un acto de lectura nuevo, sujeto a #8** — por eso este spec **diseña el lector**
  (§Componente 1) con su modo de falla, su umbral de frescura propio, y `LiveSnapshot`. La
  `GateDecision` lleva la frescura **computada por el orquestador**, no leída de un campo.
- **#6 (specs autoritativas mandan):** este spec **enmienda** la sección "Sin modulación per-coin
  por régimen" de `docs/superpowers/specs/es/2026-06-18-alt-season-regimen-design.md`
  (§Cambio de doctrina). No se toca `strategy/regime.py` (régimen macro-BTC, eje distinto).
- **#2/#3 (holdout bloqueado):** la calibración usa el panel 2020-2025 vía `edge_study.py`
  (Binance público + cache local). **No** toca `data/holdout/`, **no** llama `simulate_strategy`
  ni `open_holdout`.

## Arquitectura

```
                 regime/alt_season.py  (existe)
                  └─ compose_regime() → estado + votos.vivos  → data/alt_season.json (atómico)
                             │
                             ▼
        regime/alt_season_read.py   ←── NUEVO · lector compartido del snapshot
        leer_regimen(umbral_seg) → RegimenVivo{estado, frescura, votos_vivos, generated_at}
         · maneja archivo ausente / JSONDecodeError → frescura="muerto" (guard de api/alt_season.py:41-49)
         · computa frescura vía LiveSnapshot contra umbral_seg (parámetro, NO el de la UI)
                  │                                            │
        (lo usa api/alt_season.py — DRY)          (lo usa el hook del scanner)
                             │
                             ▼
        regime/exposure_gate.py   ←── NUEVO · puro · sin red/DB
        evaluar_gate(estado, frescura, votos_vivos, es_alt, cfg) → GateDecision
                  │                                          │
   ┌──────────────┘                                          └───────────────┐
   ▼                                                                          ▼
 tools/run_valley_screener.py::build_snapshot                       btc_scanner.py::scan
  (screener_loop, 6h) — régimen EN MANO (edad ~0)                   (crypto-scanner, 300s)
  por candidata (es_alt=True): evaluar_gate                          1× por ciclo: leer_regimen(umbral_gate)
   · suprime → candidatas_ocultas[]                                  por símbolo alt: evaluar_gate
   · atenua  → entra con clima_ambiguo                               · suprime → señal NO emitida
   · si enabled: fila en regime_gate_audit                           · atenua  → señal con flag
                                                                     · si enabled: fila en audit (batch)
   │                                                                          │
   └───────────────────────────────┬──────────────────────────────────────────┘
                                    ▼
                       db: tabla regime_gate_audit  (solo si enabled) ←── NUEVA · append-only
```

**Aislamiento:** `evaluate_symbol` (screener) y la evaluación de señal del scanner **quedan puras
e intactas** — no reciben el régimen. El gate se aplica en el **orquestador**. El lector del
snapshot (`alt_season_read.py`) se **extrae** de `api/alt_season.py` para que API y scanner
compartan un solo path de lectura (DRY, un solo guard de ausencia/corrupción).

## Contrato `GateDecision`

Devuelto por `regime/exposure_gate.py::evaluar_gate`. **Un hecho del clima, no un veredicto.**

```python
@dataclass(frozen=True)
class GateDecision:
    nivel: str            # "pasa" | "atenua" | "suprime"
    estado_regimen: str   # "alts" | "mixto" | "btc"  (de compose_regime)
    es_alt: bool          # ¿entrada en alt (no-BTC)?
    regime_frescura: str  # "fresco" | "rancio" | "muerto"  — COMPUTADA por el orquestador
                          #   (vía alt_season_read.leer_regimen), NO leída de un campo del JSON
    votos_vivos: int      # del snapshot — para distinguir mixto-por-datos de mixto-por-empate
    razon: str            # ej "régimen 'btc' — el viento no acompaña a las alts"
    enforced: bool        # = cfg.regime_gate.enabled AND regime_frescura == "fresco"
    umbral_version: str   # sello del set de umbrales + overrides + gobierno de evidencia (§Items)
```

**Política pura** (toda la lógica en un solo lugar, sin efectos secundarios):

```
def evaluar_gate(estado, frescura, votos_vivos, es_alt, cfg) -> GateDecision:
    enforced = cfg.regime_gate.enabled and frescura == "fresco"
    if not enforced:                       nivel = "pasa"  # flag off, o régimen rancio/muerto
    elif not es_alt:                       nivel = "pasa"  # el gate es sobre exposición a ALTS
    elif estado == "alts":                 nivel = "pasa"
    elif estado == "btc":                  nivel = "suprime"
    elif estado == "mixto":
        # mixto tiene DOBLE origen (alt_season.py:105 vs :111). Distinguir:
        if votos_vivos < MIN_LIVE_VOTERS:  nivel = "pasa"   # datos degradados = AUSENCIA de señal
        else:                              nivel = "atenua" # empate genuino = ambigüedad
    else:                                  nivel = "pasa"  # estado inesperado → fail-open (ver Items)
    return GateDecision(nivel, estado, es_alt, frescura, votos_vivos, razon(...), enforced, umbral_version)
```

## Comportamiento por estado

| Régimen | `votos_vivos` | `nivel` | Valles screener | Scanner | Visible al operador |
|---|---|---|---|---|---|
| `alts`  | —      | `pasa`    | candidata normal | señal normal | sí, normal |
| `mixto` | ≥2 (empate) | `atenua`  | con flag `clima_ambiguo` | señal con flag | sí, marcada |
| `mixto` | <2 (datos degradados) | `pasa` | normal | señal normal | sí, normal |
| `btc`   | —      | `suprime` | a `candidatas_ocultas[]` | señal NO emitida | Valles: **destapable** · scanner: solo auditoría |
| cualquiera, frescura `rancio`/`muerto` | — | `pasa` | normal | normal | sí, normal |
| cualquiera, `cfg.regime_gate.enabled=false` | — | `pasa` | normal (sin campos nuevos) | normal | sí, normal |

- **Por qué `mixto` empate marca pero no esconde:** solo escondemos sobre clima **claramente**
  malo (`btc`). En ambigüedad genuina, mostrar + marcar.
- **Por qué `mixto` por datos degradados pasa:** `n_live < MIN_LIVE_VOTERS` (CoinGecko caído +
  cobertura baja) es **ausencia de señal**, no clima dudoso — esconder ahí sería el modo F3a.

## Componentes

### 1. `regime/alt_season_read.py` (NUEVO — lector compartido, resuelve BLOCKERS 1+2+3)
- `leer_regimen(umbral_seg: float) -> RegimenVivo` con `RegimenVivo{estado, frescura, votos_vivos, generated_at}`.
- Lee `data/alt_season.json`; **replica el guard** de `api/alt_season.py:41-49` (archivo ausente o
  `JSONDecodeError` → `frescura="muerto"`, `estado` irrelevante).
- **Computa la frescura** con `freshness.LiveSnapshot` contra `umbral_seg` (parámetro), NO contra
  el de la UI. Devuelve `votos.vivos` del snapshot para la desambiguación de `mixto`.
- **`api/alt_season.py` se refactoriza para usar este lector** (DRY: un solo path de lectura y un
  solo guard). La API sigue pasando su `FRESCURA_VALLES_SEG` (12h, UI); el gate pasa el suyo.

### 2. `regime/exposure_gate.py` (NUEVO, puro)
- `evaluar_gate(estado, frescura, votos_vivos, es_alt, cfg) -> GateDecision` (la política de arriba).
- `umbral_version(cfg) -> str` — sello determinista (ver §Items: qué entra al hash).
- Sin red, sin DB, sin import de orquestadores ni del lector.

### 3. Hook en el screener — `tools/run_valley_screener.py::build_snapshot`
- El régimen ya está en mano (misma pasada, `run_valley_screener.py:141`) → `frescura="fresco"`
  trivial (edad ~0). Por cada candidata (`es_alt=True`): `evaluar_gate(...)`.
- `suprime` → `candidatas_ocultas[]`; `atenua` → `clima_ambiguo: true`; `pasa` → lista normal.
- **`valley_candidates.json` debe migrar a `_atomic_write_json`** (hoy `run_valley_screener.py:157`
  es escritura NO atómica; el payload crece con los campos nuevos → ventana de truncamiento más
  ancha para un `GET /valley-candidates` concurrente). Ver §Items.
- **Con `enabled=false`: los campos `candidatas_ocultas`/`clima_ambiguo` NO se emiten** (byte-idéntico)
  y **no se escribe auditoría**.

### 4. Hook en el scanner — `btc_scanner.py::scan`
- **1× por ciclo de scan**: `leer_regimen(cfg.regime_gate.frescura_umbral_seg)` (NO releer el JSON
  por símbolo×tenant). Pasar el `RegimenVivo` al gate por cada símbolo.
- `es_alt = symbol != "BTCUSDT"` (consistente con `alt_season.py`, que excluye BTCUSDT de
  `alt_contribs`). Por símbolo alt con señal: `evaluar_gate(...)`. `suprime` → señal NO emitida;
  `atenua` → señal con flag; BTC/`pasa` → intacto.
- **Ortogonal al gate de dirección macro-BTC** (`strategy/core.py::_regime_to_direction_token`):
  ese decide *dirección* (BEAR→SHORT, BULL/NEUTRAL→LONG); el de alt-season decide si la entrada alt
  **aflora**. Una señal alt pasa **ambos**. Ejes distintos, se apilan.
- **Sitio de inserción del hook = item abierto** (§Items) — debe quedar en el path de emisión real
  con su propio `try/except` fail-open, NO depender por accidente del `try/except` del bloque
  v2_shadow (~286-327).
- Fail-open `try/except`: un fallo del gate o del lector NUNCA tumba el scan ni esconde por error.

### 5. `db: tabla regime_gate_audit` (NUEVA, append-only, **solo si `enabled=true`**)
- Columnas: `id, ts, motor (valles|scanner), symbol, estado_regimen, nivel, es_alt,
  regime_frescura, votos_vivos, enforced, umbral_version, tenant_id`.
- **`tenant_id` es NULLABLE.** La decisión de exposición es un **hecho de mercado global**: el
  screener escribe `tenant_id=NULL`; el scanner escribe **UNA fila por símbolo evaluado por ciclo**
  (NO una por tenant, aunque el loop v2_shadow itere tenants) con `tenant_id=NULL`.
- **Costo de escritura acotado** (HIGH crítico — el burst de writes ya tumbó endpoints de lectura
  el 2026-05-29, `db/connection.py:88-95`): escribir las filas del ciclo **en batch dentro de una
  sola transacción**, no N `BEGIN IMMEDIATE` separados (`db/transaction.py:70` serializa escritores
  vía WAL — no corrompe, pero cada `BEGIN IMMEDIATE` compite por el único writer lock). Con
  `enabled=false` no se escribe nada → cero contención añadida.
- **Política de retención:** rotación/poda (p.ej. conservar N días) — definir en el plan (§Items).
- Owner de frescura: hereda el de cada orquestador. Es rastro histórico, no snapshot vivo (no
  introduce estado vivo nuevo sujeto a LiveSnapshot — es append-only de auditoría).

### 6. Config — bloque `cfg.regime_gate` en `config.defaults.json`
```json
"regime_gate": {
  "enabled": false,
  "frescura_umbral_seg": 27000,   // ~1.25× SCREENER_INTERVAL_SEC (6h). Distinto del de la UI (12h):
                                   //   un enforcer que actúa cada 300s no puede aceptar clima de 12h.
  "umbral_overrides": {}          // pisa los 6 umbrales de regime/alt_season.py sin deploy (calibración)
}
```
- `enabled` arranca `false` (merge byte-idéntico). Se enciende en config de prod.
- `frescura_umbral_seg` resuelve el BLOCKER 6h-vs-12h: el gate usa un umbral **más estricto** que
  la UI. Justificación citable: `api/alt_season.py:40` advierte "'fresco' = el cálculo es reciente,
  NO que la afirmación de mercado siga vigente".
- `umbral_overrides` = retuneo sobre la marcha sin tocar código.

### 7. Frontend Valles — válvula "ver ocultas"
- *"N alts fuera de alt-season — ver"*; al expandir, lista `candidatas_ocultas` con su clima.
- Doble función: doctrina honesta + feed humano de calibración. Reusa `useValleyBundle` / PickScreen.
  Sin endpoint nuevo.

## Calibración, señal de daño y reversa (sobre la marcha, sin shadow)

- **Arranque:** umbrales provisionales (o `umbral_overrides`).
- **Opcional, en paralelo (no bloquea merge):** `edge_study.py` vs panel 2020-2025 → mejores
  umbrales iniciales → `umbral_overrides`.
- **Señal de daño cuantitativa** (resuelve HIGH "enforcing sin criterio de reversa"): la **tasa de
  supresión** (% de alts ocultas por ciclo) se computa desde `regime_gate_audit`. Un umbral de
  alarma (p.ej. >X% del universo oculto sostenido) dispara revisión. Detecta la sobre-supresión que
  el "sesgo de no-evento" esconde.
- **Reversa inmediata:** `cfg.regime_gate.enabled=false` → vuelve a byte-idéntico al instante.
  Más fino: ajustar `umbral_overrides`.
- **Nota honesta:** Samuel eligió enforcing desde el arranque (sin fase shadow) con umbrales
  provisionales. El riesgo residual (esconder de más durante un régimen mal clasificado) se mitiga
  con la señal de daño + la reversa por flag, NO se elimina. Es una decisión consciente del dueño.

## Precondiciones de activación (gates antes de `enabled=true` en prod)

La feature se mergea **apagada** (`enabled=false`, byte-idéntica). Antes de encenderla en prod,
estos gates son obligatorios (marcados por el review final de rama):

1. **`config.json` de prod debe llevar el bloque `regime_gate`.** El scanner lee su config de
   `config.json` (no de `config.defaults.json`), mientras el screener usa `load_config()` (que sí
   mergea defaults). Poner `enabled=true` solo en `config.defaults.json` activaría el gate del
   screener pero NO el del scanner. Para activar AMBOS motores, `config.json` debe incluir
   `regime_gate.enabled=true` (+ `frescura_umbral_seg`, `umbral_overrides`). Con la clave ausente,
   ambos motores leen `enabled=False` (byte-idéntico) — el default es seguro.
2. **Calibrar los umbrales antes de encender** (correr `edge_study.py` vs panel 2020-2025 → sembrar
   `umbral_overrides`), o aceptar explícitamente el riesgo de enforcing con provisionales.
3. **Auditoría batcheada por ciclo:** el scanner acumula las filas del ciclo y hace UN solo
   `registrar_decisiones(filas)` por ciclo (no per-símbolo), para no recrear el burst de write-lock
   del 2026-05-29. (Implementado; verificar que sigue así si se toca `scanner_loop`.)

## Manejo de errores y frescura (#8)

- **Fail-open sobre régimen rancio/muerto:** `frescura != "fresco"` → `enforced=False` → `pasa`.
  Nunca esconder sobre clima muerto (modo F3a). El umbral lo fija `frescura_umbral_seg` (estricto).
- **Fail-open ante excepción** del gate o del lector: `try/except` (log warning) → `pasa`.
- **Snapshot ausente en arranque:** el lector devuelve `frescura="muerto"` → `pasa`. (El scanner
  arranca casi a la par del screener; durante la primera ventana `alt_season.json` puede no existir.)
- **Flag-off byte-idéntico REAL:** con `enabled=false`, (a) los campos nuevos del JSON NO se emiten,
  (b) NO se escriben filas de auditoría, (c) la señal del scanner es idéntica. Test de regresión que
  verifique AUSENCIA de campos nuevos Y ausencia de filas de auditoría.

## Cambio de doctrina (enmienda #6)

Enmienda `docs/superpowers/specs/es/2026-06-18-alt-season-regimen-design.md`:
- "El régimen ENMARCA pero NO modula la moneda" / "Sin modulación per-coin por régimen" queda
  **superada para el eje de exposición**: el régimen ahora **gatea qué alts afloran**.
- Matiz preservado: **no es scoring per-coin** (no rankea monedas por régimen). Es un filtro de
  exposición sobre un **hecho de mercado** (el clima), igual para todas las alts. El veredicto sobre
  una coin sigue prohibido. Lo escondido es destapable (Valles).
- **Tarea bloqueante del merge** (no follow-up): actualizar la sección correspondiente del spec de
  2026-06-18 con un apuntador a este documento, para que no queden dos specs autoritativas en
  contradicción.

## Honestidad: asimetría declarada (resuelve HIGH 9)

"Destapable" aplica a **Valles** (UX "ver ocultas") — el motor de exhibición para un humano. En el
**scanner**, una señal suprimida no se emite y el operador no tiene afordancia de destape en tiempo
real; queda **auditable** en `regime_gate_audit` (query). La doctrina de honestidad es: *Valles
destapa; el scanner deja rastro.* No se presenta "destapable" como propiedad universal.

## Testing

- **`tests/test_exposure_gate.py`** (núcleo puro): tabla de verdad estado×frescura×votos_vivos×es_alt×enabled.
  Casos clave: `btc`+fresco+alt+enabled→`suprime`; `btc`+rancio→`pasa`; `btc`+enabled=false→`pasa`;
  BTC(no-alt)→`pasa`; `mixto`+votos≥2→`atenua`; `mixto`+votos<2→`pasa`; `alts`→`pasa`.
- **`tests/test_alt_season_read.py`**: archivo ausente→`muerto`; JSON corrupto→`muerto`;
  `generated_at` viejo (> umbral)→`rancio`; reciente→`fresco`. **Test que fija `generated_at` viejo
  y verifica `enforced=False`** (atrapa el fail-open silencioso permanente — BLOCKER 2).
- **`tests/test_run_valley_screener.py`**: `enabled=false` → snapshot byte-idéntico (sin campos
  nuevos, sin filas de auditoría); `enabled=true`+`btc`+fresco → alts en `candidatas_ocultas` + fila.
- **`tests/test_scanner.py`**: `enabled=false` → emisión idéntica, sin auditoría; `btc`+fresco → no
  emite señal alt; error del gate/lector → fail-open (scan sobrevive).
- **`tests/test_regime_gate_audit.py`**: la fila lleva `umbral_version`, `regime_frescura`,
  `tenant_id=NULL`; con `enabled=false` no se escribe.

## Fuera de alcance (diferido)

- Modulación de **sizing** por régimen (choca con #4; solo vía épico regime-allocation).
- Conectar el gate al **épico regime-allocation #338**.
- Fase shadow formal (Samuel la descartó a favor de enforcing + calibración sobre la marcha).
- Afordancia de destape en el scanner / dashboard del `regime_gate_audit` (endpoint de lectura).
- Tendencia de dominancia / breadth200 (v1.1 del detector).

## Items abiertos (para el plan)

- **Mecánica de migración** de `regime_gate_audit` (cómo se crean tablas en `db/`).
- **`umbral_version`:** qué entra al hash. Debe incluir los 6 umbrales **+ `umbral_overrides`
  aplicados + `COVERAGE_MIN`/`MIN_LIVE_VOTERS`** (gobierno de evidencia — afectan qué votos cuentan).
  "Los 6 umbrales" a secas es insuficiente (dos calibraciones con distinto `COVERAGE_MIN` darían el
  mismo sello → auditoría engañosa).
- **Sitio exacto del hook del scanner:** path de emisión real con `try/except` propio (no depender
  del `try/except` del v2_shadow ~286).
- **Migrar `valley_candidates.json` a `_atomic_write_json`** (`run_valley_screener.py:157`).
- **Recarga en caliente de `umbral_overrides`:** confirmar que los loops releen config por ciclo
  (`runtime.py:428` sugiere relectura por ciclo del screener) o requieren reinicio — "sin deploy"
  exige relectura por ciclo.
- **Política de retención** de `regime_gate_audit` (tabla append-only de alta frecuencia).
- **Rama `else` (estado inesperado):** hoy `compose_regime` solo emite `{alts,mixto,btc}` (rama
  muerta). Decidir: defensa de estados futuros (`pasa`) vs error explícito (degradación muda vs ruidosa).
- **"Espejo estructural"** de `alt_season.py`: traducir a propiedad concreta (funciones puras, sin
  red/DB) o eliminar la instrucción.
