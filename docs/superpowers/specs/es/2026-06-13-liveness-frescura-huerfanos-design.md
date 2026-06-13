# Liveness operacional: revivir los huérfanos + frescura como contrato · Diseño

**Fecha:** 2026-06-13
**Rama:** `feat/liveness-frescura-huerfanos`

## §0 — El problema (de la auditoría) y la raíz

Una auditoría del despliegue de prod (contenedor que corre **solo `uvicorn btc_api:app`**) encontró que el sistema late por **threads del lifespan** (`scanner_loop`, `health_monitor_loop`, `kill_switch_calibrator_loop`, arrancados por `start_scanner_thread`). Pero **dos piezas con estado vivo son HUÉRFANAS** — su writer es un comando CLI manual sin scheduler, así que nunca corren en prod:

- **Screener de valles** (`tools.run_valley_screener` → `data/valley_candidates.json`) → la pestaña Valles está vacía.
- **Sync de Binance** (`sync_tenant` vía `tools/sync_binance_spot`) → `observed_orders` (captura SL/TP) nunca se refresca, **y el tracker en vivo de F3a (`track_live`) nunca avanza** — F3a, recién mergeado, está operacionalmente muerto en prod.

Un tercer reader **miente sobre frescura** sin ser huérfano-de-thread: el **dossier** (`build_dossier_live`, caché 7d) se auto-cura on-request, pero entre pedidos sirve datos viejos como frescos.

**La raíz (consenso del roster):** *la frescura no es una propiedad operacional externa (un cron que rezás). Es una propiedad SEMÁNTICA del dato que cruza una frontera de proceso desacoplada, y debe vivir en el CONTRATO.* El `_EMPTY`-sin-500 de los readers no es honestidad — es camuflaje: colapsa "aún-no-corrió" y "nunca-correrá" al mismo punto. El sistema es "experto en ocultar su propia muerte operacional con dignidad".

**El enfoque (roster unánime, opción 1 con afinaciones):**
1. Revivir los huérfanos duros como **threads del lifespan** (mismo patrón que el scanner).
2. La frescura como **TIPO inconstruible sin marca temporal** (Richter), no un helper opcional — aplicado a los readers que mienten (valles + dossier).
3. Un **gate de liveness** en CLAUDE.md respaldado por un **INVENTARIO ENUMERADO** de readers de estado vivo — "patrón ARMADO, N migrados, M pendientes nombrados", no falsamente "cerrado".

**NO en alcance:** retrofitear los readers que YA respiran (`symbols_status.json`, `equity`, `kill_switch state` — alimentados por el scanner vivo o computados on-read). El triage del roster: riesgo agudo concentrado en los huérfanos; los demás se migran al tocarlos, registrados como deuda visible en el inventario.

## §1 — Parte 1: revivir los huérfanos (threads del lifespan)

Mismo patrón que `scanner/runtime.py::start_scanner_thread` / `scanner_loop` (registra en `_managed_threads`, recibe el `_thread_stop_event` compartido, `stop_managed_threads` lo junta en el teardown; `while running and not stop_event.is_set()` con sleep inter-ciclo interrumpible).

| Loop | Archivo | Qué hace | Cadencia (config, default) |
|---|---|---|---|
| `screener_loop(stop_event)` | `scanner/runtime.py` (o módulo hermano) | Llama `tools.run_valley_screener.build_snapshot` + escribe `valley_candidates.json` | `screener_interval_sec` (default **21600** = 6h; las zonas diarias cambian lento) |
| `sync_loop(stop_event)` | idem | Para cada tenant con credencial Binance **ACTIVE**: `tools.sync_binance_spot.sync_tenant(tid)` (que ya corre `track_live` de F3a al final) | `sync_interval_sec` (default **300** = 5min; vivo sin golpear el rate-limit) |

- Ambos arrancan en el lifespan junto a `start_scanner_thread()` (o desde una `start_liveness_threads()` añadida al lifespan), registrados en `_managed_threads`.
- **Fail-soft:** una excepción en un ciclo (red caída, rate-ban, símbolo malo) se loguea y el loop continúa el próximo ciclo — nunca tumba el thread (mismo principio que `scanner_loop` con su `try/except` por símbolo).
- **Reutiliza la lógica pura existente:** `build_snapshot` (screener) y `sync_tenant` (sync) ya existen y ya hacen el trabajo; los loops solo los invocan con cadencia. Cero duplicación.
- **El sync respeta lo ya construido:** CD-1/CD-5 (no actúa EXTERNAL, no escribe `closed`), BNC-12, red fuera de tx — todo eso vive ya dentro de `sync_tenant`/`track_live`. El loop solo agrega el reloj.

## §2 — Parte 2: la frescura como TIPO (`instrument`/`db` no — un módulo propio)

Un valor **inconstruible sin `generated_at`** (condición de Richter: leer estado vivo sin evaluar frescura debe ser un error de tipo, no un olvido). `freshness.py` (raíz, como `backtest.py`/`binance_sync.py`) (puro, sin red/DB):

```python
@dataclass(frozen=True)
class LiveSnapshot:
    """Envuelve un payload de estado vivo con su marca temporal OBLIGATORIA.
    No se puede emitir el payload sin la frescura (to_response la inyecta siempre).
    'muerto' = generated_at None (nunca se generó); 'rancio' = más viejo que el
    umbral; 'fresco' = dentro del umbral. Eje SNAPSHOT, distinto de
    screener.valley_filter.classify_liveness (liveness de SÍMBOLO sobre velas)."""
    payload: dict
    generated_at: str | None     # ISO-8601 UTC, o None = nunca generado
    umbral_seg: float            # antigüedad máxima para 'fresco'

    @property
    def estado(self) -> str:     # 'fresco' | 'rancio' | 'muerto'
        if not self.generated_at:
            return "muerto"
        edad = _edad_seg(self.generated_at)
        if edad is None:
            return "muerto"      # timestamp no parseable
        return "rancio" if edad > self.umbral_seg else "fresco"

    def to_response(self) -> dict:
        edad = _edad_seg(self.generated_at) if self.generated_at else None
        return {**self.payload,
                "frescura": {"estado": self.estado, "edad_seg": edad,
                             "generated_at": self.generated_at, "umbral_seg": self.umbral_seg}}
```

`_edad_seg(generated_at)` parsea el ISO (tolera `Z`/offset/naive como `_fresh` del dossier) y devuelve la antigüedad en segundos, o `None` si no parsea. `classify_freshness(generated_at, umbral) -> str` como atajo funcional. **NO se reutiliza `classify_liveness`** — es otro eje (Cassian).

## §3 — Aplicar el tipo a los dos readers que mienten

- **`GET /valley-candidates`** (`api/valleys.py`): envuelve la foto en `LiveSnapshot(payload=snap, generated_at=snap["generated_at"], umbral_seg=FRESCURA_VALLES_SEG)` (default 2× la cadencia del screener = 12h) y devuelve `.to_response()`. El caso "no existe el archivo" → `generated_at=None` → `estado: "muerto"` (distinto de una foto vieja → `rancio`). La UI muestra el estado.
- **`GET /dossier/{symbol}`** (`api/dossier.py`): el dossier ya tiene `generated_at`; se envuelve igual con `FRESCURA_DOSSIER_SEG` (default = el TTL de 7d). Un dossier dentro de TTL pero viejo se marca `rancio` honestamente.
- **Frontend:** la pestaña Valles y la tarjeta/dossier muestran la frescura — p.ej. `"foto de hace 9 días · rancia"` / `"sin foto — el screener no ha corrido"` (muerto) — en vez de fingir frescura. Un componente mínimo `FreshnessTag` reusable.

## §4 — Parte 3: el gate de liveness + el inventario enumerado

**El inventario** (`docs/superpowers/inventario-estado-vivo.md`): lista CERRADA de todo reader de estado vivo que cruza una frontera de proceso (writer ≠ reader en el tiempo). Cada entrada:

| Reader | Writer | ¿Quién lo corre en prod? | Frescura | Estado |
|---|---|---|---|---|
| `/valley-candidates` | `run_valley_screener` | `screener_loop` (lifespan) | `LiveSnapshot` | **migrado** |
| `/dossier/{symbol}` | `build_dossier_live` (on-request) | on-request (auto-cura) | `LiveSnapshot` | **migrado** |
| `observed_orders` / F3a `track_live` | `sync_tenant` | `sync_loop` (lifespan) | (estado en DB, no snapshot) | revive con sync_loop |
| `symbols_status.json` | `update_symbols_json` | `scanner_loop` (lifespan) | trae `updated_at` | **respira-vía-scanner** (deuda: sin tipo) |
| `equity` | computado on-read | n/a | n/a | **respira** (vivo por consulta) |
| `kill_switch state` | `health_monitor_loop` | lifespan | observability | **respira-vía-scanner** (deuda: sin tipo) |

**El gate** (no-negociable nuevo en `CLAUDE.md`): *toda pieza con estado vivo cruzando una frontera de proceso DEBE (a) declarar su owner de frescura — quién la escribe en prod y con qué cadencia — y (b) emitir su estado vivo vía `LiveSnapshot` (frescura en el contrato). Una pieza nueva sin owner de frescura nombrado o sin `LiveSnapshot` no mergea. Los readers existentes no-migrados están enumerados en `inventario-estado-vivo.md` como deuda visible; tocar uno sin migrarlo es una violación del gate.*

Esto hace el patrón **armado y honesto**: el inventario dice exactamente qué órgano sabe su edad y cuál es deuda nombrada — no una falsa sensación de "cerrado" (la trampa que Richter y Null Vale marcaron).

## §5 — Cadencia (config)

Defaults en la config (mismo mecanismo que `scan_interval_sec`), calibrables:
```
screener_interval_sec = 21600   # 6h (universo pesado, zonas diarias lentas)
sync_interval_sec      = 300     # 5min (vivo, sin golpear rate-limit Binance)
FRESCURA_VALLES_SEG    = 43200   # 12h (2× cadencia del screener)
FRESCURA_DOSSIER_SEG   = 604800  # 7d (= TTL del dossier)
```

## §6 — Pruebas

- **`LiveSnapshot` / `classify_freshness`** (`tests/test_freshness.py`, puro): `generated_at=None` → muerto; viejo → rancio; reciente → fresco; no-parseable → muerto; `to_response` SIEMPRE inyecta `frescura`; el payload no se puede emitir sin ella.
- **`/valley-candidates`** (existente + nuevo): foto presente → `frescura.estado` correcto; archivo ausente → `muerto` (no `_EMPTY` mudo).
- **`/dossier`**: la respuesta trae `frescura`.
- **`screener_loop` / `sync_loop`** (puros donde se pueda + smoke): un ciclo invoca `build_snapshot`/`sync_tenant` (mockeados) y respeta el `stop_event` (no loop infinito en el test); fail-soft (una excepción en un ciclo no mata el loop).
- **Lifespan:** `start_liveness_threads` registra los threads en `_managed_threads` y `stop_managed_threads` los junta (sin threads colgados — el patrón anti-leak ya existe).
- **Frontend:** `FreshnessTag` muestra fresco/rancio/muerto; la Vista Valles muestra el estado.

## §7 — Invariantes y alcance
- **Frescura en el contrato (Richter):** `LiveSnapshot` es inconstruible sin marca temporal; `to_response` siempre emite frescura. Leer estado vivo sin frescura no se puede por construcción.
- **Honestidad del alcance (Richter/Null Vale):** el inventario enumera lo migrado y la deuda; nada se declara "cerrado" falsamente.
- **Reutiliza lo que ya respira (Cassian):** los loops solo agregan el reloj a `build_snapshot`/`sync_tenant` existentes; no se duplica lógica; no se retrofitea código que ya late.
- **Respeta los contratos vivos:** el sync_loop hereda CD-1/CD-5/BNC-12/red-fuera-de-tx de `sync_tenant`/`track_live`. Los threads son fail-soft y torn-down-limpio (anti-leak).
- **Eje correcto:** `classify_freshness` (snapshot) ≠ `classify_liveness` (símbolo). No se confunden.

## §8 — Fuera de alcance (deuda nombrada, no atacada)
- Migrar `symbols_status.json`, `equity`, `kill_switch state` al tipo `LiveSnapshot` — respiran vía scanner; se migran al tocarlos (enumerados en el inventario).
- Alarmas/notificaciones proactivas de "dato rancio" — la frescura se expone (pull); alertar es una extensión futura.
- Aplicar `LiveSnapshot` al estado de F3a `lifecycle_states` (es estado en DB, no un snapshot-archivo; su frescura la da el `updated_at` del tracker vivo, que el sync_loop ahora garantiza).
