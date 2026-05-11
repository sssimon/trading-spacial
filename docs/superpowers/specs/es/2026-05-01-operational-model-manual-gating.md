# Modelo operacional: gating manual de señales

**Fecha:** 2026-05-01 (revisado post-PR3 cooldown parity)
**Issue:** #283
**Tono:** descriptivo (captura el estado vigente, no propone cambios)

---

## 1. Contexto y propósito

El sistema genera señales de trading de forma automática (scanner → score → exclusiones → notificación), pero las decisiones de **entrada** y **cierre** de posiciones requieren aprobación manual del operador via CLI o frontend autenticado.

Este spec materializa una decisión operacional que hasta ahora vivía implícita en el código (`btc_scanner.py: scan() exclusions dict (search `excl = {`)`) y en convenciones no escritas. No introduce comportamiento nuevo: documenta el modelo vigente para que cualquier trabajo futuro sobre validación, automatización o comparación backtest-vs-live tenga una referencia explícita.

Post-PR3 (cooldown parity), E5_Cooldown se promovió de manual-check a auto-enforcement (scanner consulta `db_last_exit_ts` y reporta el `activo` como bool). E2/E3/E4 retienen status manual-check porque dependen de información externa al sistema.

## 2. Pipeline determinista vs gating manual

**Lo que el scanner hace por sí solo (determinista):**
- Fetch OHLCV (Binance, Bybit fallback) en 4 timeframes (5m, 1h, 4h, 1d).
- Cálculo de indicadores (LRC, RSI, BB, SMA100, ATR, ADX, divergencias, engulfings).
- Regime detector (composite F&G + funding + price).
- Score multi-timeframe (0–9).
- Evaluación booleana de exclusiones automáticas (E1, E5, E6) e informativas (E7).
- Persistencia a `signals.db` y emisión de notificación a Telegram.

**Lo que requiere intervención humana:**
- Verificación de las exclusiones marcadas `VERIFICAR_MANUAL` (E2, E3, E4).
- Decisión final de abrir la posición.
- Decisión de cerrar manualmente antes de SL/TP.

## 3. Exclusiones E1–E7 — clasificación auto vs manual

| ID | Campo `activo` | Tipo | Bloquea entrada |
|----|---------------|------|-----------------|
| E1_BullEngulfing | `bull_eng` (boolean) | Auto | Sí, si `True` |
| E2_Noticias_Macro | `"VERIFICAR_MANUAL"` | Manual | Operador decide |
| E3_RachaPerdedora | `"VERIFICAR_MANUAL"` | Manual | Operador decide |
| E4_Capital_Min | `"VERIFICAR_MANUAL"` | Manual | Operador decide |
| E5_Cooldown | `bool` (auto-evaluated, post-PR3) | Auto | Sí, si `True` |
| E6_Divergencia_Bajista | `bear_div` (boolean) | Auto | Sí, si `True` |
| E7_Tendencia_Fuerte | `"INFORMATIVO"` | Informativo | No |

Resumen: 3 manual-check + 3 auto + 1 informativo.

## 4. Flujo de aprobación

1. Scanner emite señal: score + estado de cada exclusión + indicadores resumidos.
2. Notificación sale por Telegram (push al chat configurado en `config.json`).
   - **Telegram es outbound only.** No hay bot de entrada que reciba aprobaciones. Las decisiones nunca se confirman vía Telegram.
3. Operador revisa la señal en el frontend (`http://localhost:5173` en dev, `https://trading.sdar.dev` en prod) o vía la CLI.
4. Operador resuelve manualmente las exclusiones marcadas `VERIFICAR_MANUAL` (E2–E4) consultando el contexto necesario (calendar de noticias para E2, historial de trades para E3, balance para E4). E5 ya no requiere intervención: scanner reporta `activo: bool` derivado de `db_last_exit_ts(symbol)` vs `cooldown_hours_required`.
5. Si las manual-checks pasan, operador ejecuta entrada via `POST /positions`.
6. Cierre manual (cuando aplica) via `POST /positions/{id}/close`. SL/TP automáticos siguen activos en background y disparan cierres aun sin intervención.

## 5. Implicancias para validación (backtest vs live)

Post-PR3, **ambos contextos enforcean automáticamente E5_Cooldown** con valores per-symbol (default-fallback al global `COOLDOWN_H=6` cuando el override no está configurado o es inválido). El backtest skipea la barra si `hours_since < effective_cooldown` (`backtest.py: cooldown enforcement block (search `Cooldown check`)`); el scanner consulta `db_last_exit_ts(symbol)` y reporta `E5_Cooldown.activo` como bool en cada tick.

| Contexto | Quién enforcea E5 | Cómo |
|----------|-------------------|------|
| Backtest | Simulador | Skip de la barra si `hours_since < effective_cooldown` |
| Live | Scanner + frontend/CLI | `db_last_exit_ts` query en cada scan; bloqueo automático cuando `activo: True` |

Las constantes per-symbol (BTC=14, ETH=14, AVAX=8, demás=6) viven en `config.json["symbol_overrides"]` y son fuente de verdad compartida entre scanner y backtest.

**Implicancia para A.4 (#250) y cualquier evaluación holdout:** post-PR3, la densidad de trades de backtest y producción debería converger en este eje (ambos auto-enforcean). Cualquier divergencia material indica drift en otro eje (orderbook fills, slippage, regime cache, etc.) y debe investigarse contra los specs de PR2 (cap) o el cost model.

E2, E3 y E4 retienen status manual-check: el backtest no las simula (no tiene calendar de noticias, no rastrea racha psicológica del operador, no modela balance externo). Cualquier comparación backtest-vs-live debe asumir que estas tres exclusiones operan asimétricamente — sólo E5 alcanzó parity en PR3.

## 6. Lectura del diseño actual

Las 3 exclusiones manual-check restantes (E2–E4) están agrupadas en el mismo dict de `btc_scanner.py: scan() exclusions dict (search `excl = {`)`, todas con `"activo": "VERIFICAR_MANUAL"` y un `nota` describiendo qué consultar. Todas dependen de información externa al sistema (calendar, balance externo, racha psicológica) que el scanner no puede consultar autónomamente.

E5_Cooldown se promovió a auto-evaluación en PR3 (epic #294) porque la información necesaria — timestamp del último exit — vive en `signals.db` y es accesible por el scanner via `db_last_exit_ts`. Esto eliminó la asimetría operacional con el backtest sin agregar dependencias externas.

## 7. Promotion to auto-enforcement

**Precedente: E5_Cooldown (PR3, epic #294).** Promovido cuando se cumplió:
- La información necesaria estaba accesible localmente (`db_last_exit_ts` query a `signals.db`).
- El backtest ya enforceaba auto, y la asimetría con producción era documentada como riesgo de divergencia (§5 pre-PR3).
- Per-symbol values estaban derivados deterministically (rule `max(TL, NW=4, floor=6)` per spec D9 §2.1) — sin grado de libertad nuevo.

Para promover E2/E3/E4 a auto-enforcement, cualquier propuesta debe satisfacer:

- Scope explícito (qué exclusión, en qué condiciones).
- Información accesible localmente (e.g., calendar API para E2, derivar E3 de `positions` table, balance via exchange API para E4).
- Criterios de aceptación (cómo se valida que el auto-enforcement coincide con la decisión humana actual).
- Plan de validación (backtest + paper trading + golden-path manual antes de promover a default).

Este spec captura el estado vigente — no define criterios genéricos para futuras promociones, solo documenta el patrón aplicado en E5.

## 8. Referencias

- `btc_scanner.py: scan() exclusions dict (search `excl = {`)` — tabla de exclusiones E1–E7.
- `btc_scanner.py`: `COOLDOWN_H` constant — global default-fallback (= 6).
- `backtest.py: cooldown enforcement block (search `Cooldown check`)` — enforce automático de E5 en simulación con per-symbol resolution.
- `db/positions.py` — `db_last_exit_ts(symbol)` helper consumido por scanner para E5.
- `strategy/_validators.py` — `validated_cooldown_hours` (boundary `> 168` reject, default-fallback).
- `btc_api.py` — endpoints `POST /positions` y `POST /positions/{id}/close` que materializan la aprobación manual de entrada/cierre (E5 ya no requiere aprobación).
- Issue #283 — modelo operacional: producción manual vs backtest automático (cierra con este spec).
- Issue #284 — análisis previo que confirmó que `COOLDOWN_H = 6` es consistente entre código y docs (cerrado como outdated).
- `docs/superpowers/specs/es/2026-05-03-asunciones-tecnicas-pre-holdout.md` §2.1 — pre-registered cooldown per-symbol values (source of truth para PR3).
- `docs/strategy-backtest-report.md` — reporte del backtest; menciona "6h cooldown" en §2 Methodology con referencia cruzada a este spec.
