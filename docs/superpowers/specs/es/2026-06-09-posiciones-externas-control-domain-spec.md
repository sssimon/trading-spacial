# Spec — Posiciones EXTERNAL: el eje `control_domain` (REV 2)

**Fecha:** 2026-06-09 · **REV 2** (tras roast de Adrian: 5 BLOCKER / 4 HIGH / 4 MEDIUM, todos direccionados). **Estado:** PROPUESTO.
**Tipo:** integración de modelo de dominio. **Alcance partido** (decisión de Samuel): **v0.1 = FUNDACIÓN** (objetivo viernes 2026-06-12), **v0.1.5 = VISTA** (fast-follow), **v0.2 = plano vivo**.
**Origen:** papá (tenant 2) hizo 2 operaciones LONG por fuera del sistema (Binance), sin SL, las aguanta underwater; NO están en la base.
**Funda:** junta del roster 2026-06-09 (Voronov/Halberg/Adrian/Null Vale/Richter/Cassian, sin Axiom-0) + roast de Adrian sobre REV 1.
**Relacionado:** `2026-06-09-integracion-eje-conducta-spec.md`, `2026-06-08-panel-disciplina-suerte-aware-spec.md`.

---

## REV 2 — qué cambió (changelog del roast)

El error raíz de REV 1: razonó contra el **stub** del esquema (`db/schema.py:170-190`), no contra el esquema VIVO (los CHECK reales los instalan las migraciones que **recrean** la tabla, `schema.py:1132-1403`; el canon lo congela `db/positions_schema.py`). REV 2 razona contra el esquema vivo y cierra:

1. **B1 — exención sistemática, no de un consumidor.** `control_domain='INTERNAL'` debe filtrar TODOS los lectores de `status='open'` que tocan riesgo/actuador, no solo `check_position_stops` (§4).
2. **B2 — la columna entra al canon + a la recreación.** `control_domain` se añade a `CANONICAL_POSITIONS_COLUMNS` y a las `CREATE TABLE positions_new` de las migraciones que recrean, o una recreación posterior la borra (§2).
3. **B3 — camino de nacimiento declarado.** Las EXTERNAL nacen por un registrador one-shot dedicado (no por el INSERT de señal `db_create_position_sql`, que no escribe la columna) (§3).
4. **B4 — `scan_id=NULL` para EXTERNAL.** Resuelve la colisión con el índice único `(tenant_id, scan_id) WHERE status='open'` y es semánticamente correcto: sin linkage de señal del sistema (§3).
5. **B5 — `qty>0` se mantiene; "no autoritativa" es sobre Binance, no sobre el CHECK.** La fila cumple el constraint duro (los valores de papá son >0); "espejo no autoritativo" describe la correspondencia-con-Binance (concern de v0.2), no debilita el CHECK (§3).
6. Decisiones de Samuel bakeadas: ETH entry `2026-06-02T11:11`; aviso de horizonte = **flag pull en la vista, NO push Telegram**; vista **self-tenant — la ve papá en su cuenta** (multitenant, JWT), Samuel monitorea por el export read-only.

---

## 0. Qué es / qué NO es
Declara el eje `control_domain` y registra de forma SEGURA 2 posiciones que el sistema observa pero no controla. v0.1 NO construye la vista roja ni el P&L (eso es v0.1.5). NO cierra nada. NO lee precio vivo. NO reconcilia con Binance. NO promete edge.

## 1. El hallazgo (Voronov) — `positions` confundía provenance y control
`positions` mezcló dos semánticas que coincidían por accidente: **provenance** (de dónde nació el hecho) y **control** (si el sistema puede actuar). Toda fila fue provenance-interno ⟹ control-interno. `check_position_stops` lo encarna: `WHERE status='open'` → cierra; asume *estar-en-la-tabla ⟹ autorización-para-actuar*. Las ops de papá rompen la coincidencia: **provenance EXTERNO, control CERO**. No es un estado del lifecycle — es una columna que siempre fue constante implícita.

**Ley de orquestación:** observación y control son ejes independientes. El sistema representa lo que ve sin afirmar que puede actuar, y rehúsa todo actuador cuya autorización no esté presente.

## 2. La columna `control_domain` (cierra B2)
`positions.control_domain TEXT NOT NULL DEFAULT 'INTERNAL'`, dominio `{INTERNAL, EXTERNAL}`.
- **Canon:** añadir a `CANONICAL_POSITIONS_COLUMNS` (`db/positions_schema.py:124-145`) — pasa de 20 a 21 columnas; actualizar el test de canonicalidad con justificación en el PR.
- **Recreación:** añadir `control_domain TEXT NOT NULL DEFAULT 'INTERNAL'` a cada `CREATE TABLE positions_new` de las migraciones que recrean (`_migrate_qty_not_null`, `_migrate_qty_positive`, `_migrate_tenant_id_not_null`, `_migrate_direction_enum`) y a sus `TARGET_COLS`, **o** una recreación posterior la borra. Idempotente (PRAGMA-guarded ALTER en el stub para DBs ya migradas).
- Backfill: el `DEFAULT 'INTERNAL'` cubre toda fila existente; ninguna es EXTERNAL salvo registro explícito (§3).

## 3. Registro de las 2 operaciones EXTERNAL (cierra B3/B4/B5)
**Camino de nacimiento:** un registrador one-shot dedicado (`tools/register_external_position.py` o equivalente), NO el INSERT de señal. Escribe explícitamente `control_domain='EXTERNAL'`. Idempotente (no duplica si ya existe por `(tenant_id, symbol, entry_ts)`).

| Campo | BTC | ETH |
|---|---|---|
| `tenant_id` | 2 | 2 |
| `direction` | LONG | LONG |
| `status` | open | open |
| `control_domain` | EXTERNAL | EXTERNAL |
| `entry_price` | 64390 | 1700 |
| `entry_ts` | 2026-06-04T00:56 | 2026-06-02T11:11 |
| `qty` | 0.01967 | 0.448 |
| `size_usd` | qty×entry = **1266.55** | qty×entry = **761.60** |
| `sl_price` / `tp_price` | NULL / NULL | NULL / NULL |
| `scan_id` | **NULL** | **NULL** |

- **`scan_id=NULL` (B4):** evita la colisión con `idx_positions_open_scan_unique` y es correcto — no hubo linkage de señal del sistema. Consecuencia coherente con el spec-conducta §4.2: `apertura_discrecional=true` (fue manual). *Matiz (Richter): papá "siguió una señal" pero ejecutó por fuera; ese acto ("siguió-pero-ejecutó-afuera") es un tipo de conducta más rico que el modelo aún no nombra — diferido. `control_domain='EXTERNAL'` es la señal más fuerte y ortogonal: "ejecutado fuera del sistema", no solo "sin scan_id".*
- **`size_usd` (cierra MEDIUM #12):** registrado como `qty×entry_price` para que las proyecciones de conducta (`size_usd`, `costo_piso`) no nazcan NO-DISPONIBLE.
- **`qty>0` (B5):** los valores reportados cumplen el CHECK vivo; la fila es válida. "No autoritativa" = no verificada contra el fill real de Binance (correspondencia, v0.2), NO una debilidad del constraint.

## 4. Exención sistemática del control (cierra B1 + HIGH #11)
**Regla:** todo lector de `status='open'` que alimente un ACTUADOR del sistema o su MATEMÁTICA DE RIESGO/COOLDOWN debe filtrar `control_domain='INTERNAL'`. La VISTA (v0.1.5) lee EXTERNAL a propósito. Consumidores a tocar (verificados por Adrian):

| Consumidor | Archivo | Acción |
|---|---|---|
| Auto-cierre SL/TP/TIME | `api/positions.py:155-263` (`check_position_stops`) | `… AND control_domain='INTERNAL'` en el SELECT de Fase 1 (predicado SQL, no flag en memoria — evita race) |
| MTM / `P(ruina)` / `τ_b` | `strategy/kill_switch_v2_shadow.py:65-88` (`_load_open_positions`) | excluir EXTERNAL del MTM de riesgo |
| Cooldown del scanner | `db/positions.py` (`db_last_exit_ts`) | excluir EXTERNAL: cerrar la op de papá NO debe pausar señales INTERNAL del símbolo |
| Contexto del agente/copiloto | `api/agent/tools/handlers.py:95,125` | el copiloto NO debe tratar una EXTERNAL como posición gobernable (no proponer cierre que no puede ejecutar) |
| `rule_a_check` (diagnóstico) | `tools/rule_a_check.py:93` | excluir EXTERNAL del análisis de reglas del sistema |

**Patrón recomendado:** un helper único (p.ej. `INTERNAL_OPEN_PREDICATE = "status='open' AND control_domain='INTERNAL'"`) reusado, para que un futuro consumidor no olvide el filtro. (Voronov: el control nunca debió derivarse de la presencia; este helper hace explícita la autorización.)

**No-cierre:** una EXTERNAL nunca entra a `pos_list_to_close`. No-negociable #1 intacto: el único camino a `REALIZED` sigue siendo `PositionClosure`; EXTERNAL solo lo toma vía `PositionClosure(USER)` cuando papá cierra en Binance y reconcilia.

**Cancelación (cierra HIGH #7):** el endpoint `DELETE` (`api/positions.py:435`, `→ cancelled`) debe **rechazar** `control_domain='EXTERNAL'` (409): una EXTERNAL corrió y tiene P&L; `cancelled` (outcome nulo) corrompería su `EpisodioDeConducción`.

## 5. La vista (v0.1.5 — fast-follow, decisiones LOCKED)
DIFERIDA del viernes, pero con el contrato congelado:
- **Self-tenant — la ve papá en su cuenta** (decisión de Samuel; multitenant, `tenant_id` del JWT, NUNCA de param — cierra HIGH #6/IDOR). Samuel monitorea por el export read-only (`tools/tenant_export`), no por un endpoint cross-tenant.
- **El "rojo" se parte** (no-mezcla): rojo-de-conducta = VIOLACIÓN tipable (`sin_stop` = `sl_price IS NULL`; `past_time_horizon`) — legible sin precio. Separado del **P&L snapshot** (outcome/suerte), etiquetado *"no realizado, al momento de cargar; el ledger no es tu saldo en Binance"*, tipo `RUIDO`. Nunca rojo-porque-pierde. (Corrección: "abrió sin señal" NO es violación; el rojo afirma solo `sin_stop` + `past_time_horizon`.)
- **Aviso de horizonte = flag PULL en la vista** (decisión de Samuel; NO push Telegram — coherente con panel §6 pull-only). `past_time_horizon` = **derivado read-only, NO persistido** (cierra HIGH #8/Q2). Cuando `time_limit_hours[símbolo]` es `None` → `NO-DISPONIBLE`, no `false` (cierra MEDIUM #10, consistente con `costo_piso`).
- **P&L snapshot (cierra HIGH #9/LOW #14):** fuente = último `scans.price` del símbolo vía `snapshot_connection` (read-only, evita el lock del scanner). `mark_ts` explícito + banda de staleness (umbral a fijar, sugerido 2× scan_interval); si no hay precio reciente o falta → mostrar `"—"` con razón, NO un número stale disfrazado de actual.

## 6. Contrato de runtime (Halberg)
- Exención = predicado SQL en cada SELECT (§4), no flag en memoria.
- P&L: SOLO `snapshot_connection`; CERO roll-in a capital.
- El sistema NUNCA escribe `closed` ni `cancelled` sobre una EXTERNAL por su cuenta.
- Idempotencia del registro y de la migración de columna.

## 7. Alcance
- **v0.1 (viernes 06-12):** columna `control_domain` (canon + recreación + ALTER idempotente); exención sistemática en los 5 consumidores; rechazo de DELETE sobre EXTERNAL; registro one-shot de las 2 ops. **Resultado: las ops están en el ledger, NO contaminan riesgo/cooldown/agente, NO se auto-cierran.** (Papá aún no ve la vista — esa es v0.1.5.)
- **v0.1.5 (fast-follow):** la vista self-tenant (rojo-de-violación + flag `past_time_horizon` + P&L snapshot etiquetado).
- **v0.2:** plano vivo `CONDUCTING` persistido, objeto cross-mundo INV-5, MAE/MFE, mark continuo, botón de cierre, reconciliación real ledger↔Binance, primitiva de correspondencia (Richter).

## 8. Invariantes
- **CD-1.** Ningún actuador del sistema ni su matemática de riesgo/cooldown toca una EXTERNAL (auto-cierre, ratchet SL, roll-in capital, MTM de ruina, cooldown, propuesta del agente).
- **CD-2.** El P&L no-realizado de una EXTERNAL es `outcome` efímero tipo `RUIDO`; no persiste, no roda a capital, no se co-renderiza con la conducta.
- **CD-3.** El rojo afirma SOLO violaciones tipables pre-declaradas (`sin_stop`, `past_time_horizon`), nunca el signo del P&L.
- **CD-4.** La fila EXTERNAL es espejo no-autoritativo de Binance; ninguna lectura la trata como verdad sobre el broker.
- **CD-5.** Cierre solo vía `PositionClosure(USER)` (papá); el sistema nunca lleva una EXTERNAL a `REALIZED` ni a `CANCELLED`.
- **CD-6.** `control_domain` está en el canon y sobrevive toda recreación de tabla; el default `INTERNAL` preserva el comportamiento de toda fila existente.

## 9. Consistencia cross-documento
- Spec-conducta §4.2 (`apertura_discrecional ← scan_id IS NULL`): consistente — EXTERNAL lleva `scan_id=NULL` ⟹ `apertura_discrecional=true` (correcto, fue manual). `control_domain='EXTERNAL'` es señal ortogonal más fuerte ("ejecutado fuera"); no se rellena scan_id (eso borraría la conducta — Adrian #13).
- El tipo de conducta "siguió-señal-pero-ejecutó-afuera" queda DIFERIDO (no hay campo; el modelo aún no lo nombra — Richter).

## 10. Preguntas abiertas (residuales para el build)
1. Umbral exacto de staleness del P&L snapshot (sugerido 2× scan_interval) — v0.1.5.
2. ¿El registrador one-shot es un script en `tools/` o un comando admin? (recomendado: script idempotente versionado.)
3. ¿`scans.price` es la fuente canónica del último precio, o hay una cache de runtime preferible? — v0.1.5.

## 11. Kill del spec
Si una verificación encuentra que (a) el P&L se co-renderiza con la conducta, (b) el sistema escribe `closed`/`cancelled` sobre una EXTERNAL, (c) el rojo afirma una violación sin regla pre-declarada, (d) la exención queda fuera del SELECT (race), o (e) un consumidor de riesgo lee EXTERNAL sin filtro — esa pieza se corta o re-tipa antes de codificar.
