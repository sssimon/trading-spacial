# Kill Switch v2 — diseño consolidado

**Epic:** #187
**Autor:** Samuel (direction) + Claude (drafting)
**Fecha:** 2026-04-23
**Status:** Draft para review

---

## TL;DR

El kill switch v2 evoluciona el sistema actual (#138) de "thresholds estáticos con intervención de dev" a una **capa de inteligencia configurable por el operador**. Tres propiedades definen la diferencia frente a v1:

1. **Operator-facing vía slider** — un slider principal 0-100 "agresividad" ajusta todos los thresholds proporcionalmente. Panel avanzado permite overrides por-eje para tuning fino.
2. **Auto-tuning vía daemon reactivo** — el sistema recalcula la config óptima cuando el mercado cambia (cambio de régimen, drawdown, cascade de events). Propone al operador; apply siempre es manual.
3. **Dos dimensiones** — per-symbol (5 tiers) × portfolio agregado (4 tiers), compuestos multiplicativamente. Permite circuit breaker a nivel portfolio además del per-symbol existente.

El objetivo de negocio es **portfolio DD agregado ≤ 10%** sostenido, sin sacrificar más del 5% del P&L total del sistema base. Se valida con walk-forward sobre 2021-2026 + 2-4 semanas de shadow en producción antes de activación live.

---

## 1. Contexto y motivación

### 1.1 Estado actual (v1, Epic #138)

Kill switch v1 tiene 4 tiers per-symbol (NORMAL / ALERT / REDUCED / PAUSED) con thresholds fijos en `config.defaults.json`:

| Tier | Trigger (v1 actual tras PR #202) |
|---|---|
| ALERT | `win_rate_20_trades < 0.30` |
| REDUCED | `pnl_30d < 0` (ventana 14 días tras tighten) |
| PAUSED | 2 meses consecutivos negativos |

v1 es simple, probado, y funciona — pero es reactivo y ciego al contexto de mercado. En el backtest fiel (hipotético, post epic #186), ETH acumula -55.8% individual antes de que PAUSED dispare, porque 2 meses consecutivos es una señal lenta.

### 1.2 Problema que v2 resuelve

Para el target operativo **DD agregado ≤ 10%**, v1 es insuficiente:

- Thresholds globales iguales para todos los símbolos → falla con heterogeneidad (DOGE vs ETH).
- Solo per-symbol → no hay mecanismo a nivel portfolio para cortar cuando varios símbolos sangran simultáneamente.
- Reactivo sobre métricas rolling → tarda en reaccionar a cambios de régimen.
- No configurable por operador → cualquier ajuste requiere PR + deploy.
- Sin aprendizaje → los thresholds se quedan fijos aunque el mercado evolucione.

### 1.3 Restricción de arquitectura del operador

Samuel fijó la regla de diseño: **el operador debe tratarse como "mono con computadora"** — no se le pide entender trade-offs ni métricas crudas. El sistema recomienda qué configurar; el operador aprueba o ignora. Todas las mejoras v2 se evalúan contra esta regla.

---

## 2. Objetivos y criterios de éxito

### 2.1 Objetivos primarios

1. **Portfolio DD agregado ≤ 10%** en cualquier ventana ≥ 3 años del histórico disponible (2021-2026).
2. **P&L agregado ≥ 95%** del P&L del sistema sin kill switch en la misma ventana (no sacrificar > 5% de alpha).
3. **Operator autonomy**: Simon puede ajustar agresividad desde frontend sin requerir PR/deploy.
4. **Auditabilidad**: cada decisión (abrir/no abrir, size factor, tier transition) se registra con razón + métricas snapshot.

### 2.2 Criterios de éxito del epic

El epic #187 se declara **done** cuando:

1. Todos los sub-issues B0-B6 + auto-calibrator están merged.
2. Gate epic-level (§9) PASS sobre ventana 2021-2026.
3. v2 está activa en producción ≥ 4 semanas continuas.
4. Zero operational-level gate alerts en esas 4 semanas.
5. Portfolio DD real observado en las 4 semanas ≤ 10%.
6. P&L real en las 4 semanas está dentro del 85%-115% del proyectado por el backtest.
7. Operador (Samuel) firma off en el performance.

### 2.3 Fuera de scope explícito

- **Predicción perfecta**: el backtest es un proxy. Aceptamos que v2 a veces pause símbolos que se habrían recuperado. Es costo de safety.
- **Mejor performance que v1**: no lo exigimos. v2 equivalente-en-DD pero más granular/auditable/configurable es win.
- **ML / modelos entrenados**: v2 es heurístico + optimización por grid search. Nada de modelos ML.
- **Kill switch en timeframe intraday**: v2 opera sobre trades cerrados y métricas diarias/horarias. No micro-estructura.

---

## 3. Arquitectura global — 5 pilares

### 3.1 Pilar 1: Slider principal + panel avanzado

- Slider `kill_switch.v2.aggressiveness: 50` en config (valor 0-100, default 50).
- Todos los thresholds de todos los features derivan linealmente del slider (ver §6).
- Panel "modo avanzado" con overrides por-eje (`velocity_sensitivity`, `portfolio_dd_sensitivity`, `calibration_strictness`) — default `null` = derivar del slider principal.
- Operator-facing: slider en frontend con preview live de proyecciones.

### 3.2 Pilar 2: Sistema vivo de recomendación (no cron)

- Un thread daemon `kill_switch_calibrator_loop` en el backend monitorea 4 signals.
- Cuando un signal dispara, corre el backtest fiel vía infrastructure del epic #186, prueba valores del slider en grilla de 5% (21 puntos), encuentra `slider_optimal` según función objetivo.
- Escribe "propuesta pendiente" a tabla DB; notifica via Telegram + badge en frontend.
- Apply siempre es **manual** (operator decide).
- Rate limit: max 1 recalibración/día, min 6h cooldown entre autos.
- Safety net: 30 días sin recalibración → corre preventiva.

### 3.3 Pilar 3: Función objetivo con DD hard-coded

```
fitness(slider) = P&L(backtest(slider))   si  portfolio_DD ≤ 10%
                  -∞                         si  portfolio_DD > 10%
```

- DD target `kill_switch.v2.dd_target: 0.10` — **NO editable por operador** (solo cambiable via PR + deploy).
- Si ningún slider cumple el constraint: reporta `status="no_feasible"` + slider más cercano + gap. No falla silenciosamente.

### 3.4 Pilar 4: Shadow mode como primera fase

- **Fase 1** (2 semanas): observability foundation — decision log estructurado, endpoints, dashboard mínimo mostrando v1 decisions.
- **Fase 2** (2-3 semanas): features B1-B6 implementadas en shadow (escriben log, no actúan).
- **Fase 3** (2-4 semanas): período shadow live — diff reports diarios v1 vs v2_shadow, weekly review.
- **Fase 4** (4 semanas): activación live con feature flag gradual, 1 feature por semana típicamente.

### 3.5 Pilar 5: 6 features atómicos

Cada uno PR independiente; se integran con los 4 pilares anteriores de forma coherente.

- **B0** — este spec.
- **B1** — velocity triggers (N stop losses en M horas → pausa temporal).
- **B2** — portfolio-level circuit breaker (DD agregado + concurrent failure count).
- **B3** — regime-aware thresholds (BULL/NEUTRAL/BEAR varían).
- **B4** — auto-calibración per-symbol (thresholds en σ sobre histórico propio; global fallback hasta 100 trades).
- **B5** — tier PROBATION (post-PAUSED reactivación al 50%).
- **B6** — dashboard observability completo.

### 3.6 Lo que hace el operador

1. Ve un dashboard con estado actual por símbolo + estado portfolio agregado.
2. Cuando el auto-calibrator genera propuesta nueva, ve: "Mueve slider de X% a Y% — proyectado +$N P&L con DD -Z%. [Apply] [Ignore] [Ver detalle]".
3. Decide.
4. **Un único caso requiere acción manual**: reactivar un símbolo PAUSED (decisión humana por design, ver §4.4).

Todo lo demás es transparente: transiciones de tier, velocity triggers, portfolio breakers, etc. ocurren automáticamente con notificación pasiva.

---

## 4. Modelo de estados y composición

### 4.1 Per-symbol tiers (5)

| Tier | `size_factor` | `skip` | Trigger de entrada |
|---|---|---|---|
| **NORMAL** | 1.0 | False | Default inicial |
| **ALERT** | 1.0 | False | WR rolling bajo, velocity cascade, regime score extremo |
| **REDUCED** | 0.5 | False | `pnl_30d < 0` sostenido, o desde ALERT con degradation signal |
| **PAUSED** | 0 | True | N meses consecutivos negativos, regresión severa, third strike |
| **PROBATION** | 0.5 | False | Desde PAUSED post-reactivación; dura N trades (proporcional al tiempo en PAUSED) |

### 4.2 Portfolio tiers (4)

| Tier | `portfolio_factor` | `skip` | Trigger |
|---|---|---|---|
| **NORMAL** | 1.0 | False | Default |
| **WARNED** | 1.0 | False | ≥ N símbolos en ALERT/REDUCED/PAUSED simultáneamente (configurable, default 3) |
| **REDUCED** | 0.5 | False | DD agregado cruza `portfolio_dd_reduced_threshold` (derivado del slider) |
| **FROZEN** | 0 | True | DD agregado cruza `portfolio_dd_frozen_threshold` (más severo) |

### 4.3 Composición multiplicativa

```python
def decide_trade(symbol_state, portfolio_state) -> (bool_skip, float_size):
    skip = symbol_state.skip or portfolio_state.skip
    size = symbol_state.size_factor * portfolio_state.size_factor
    return (skip, size)
```

Ejemplos:
- DOGE PROBATION (0.5) + Portfolio REDUCED (0.5) → opera al **25%** del base.
- ETH NORMAL (1.0) + Portfolio FROZEN (0) → **skip** (no opera).
- XLM REDUCED (0.5) + Portfolio NORMAL (1.0) → opera al **50%**.
- BTC PAUSED (0) + Portfolio NORMAL (1.0) → **skip**.

### 4.4 Velocity triggers (B1) como overlay temporal

No son un tier. Son un **timestamp con expiración** en el estado per-symbol:

```python
per_symbol_state = {
    "tier": "NORMAL",
    "velocity_cooldown_until": datetime | None,
}

effective_skip = (tier_skip) or (
    velocity_cooldown_until is not None and velocity_cooldown_until > now
)
```

Cuando el trigger fire (ej. 5 SL en 12h):
- `velocity_cooldown_until = now + velocity_cooldown_hours` (default 4h).
- Mientras `velocity_cooldown_until > now`: skip = True.
- Expira automáticamente. No requiere intervención.

### 4.5 Auto-recovery según modo configurado

```
kill_switch.v2.auto_recovery_mode: "smart_auto" | "mixed_manual_pause"
```

**`smart_auto`** (default):
- `REDUCED → NORMAL`: auto con evidencia sostenida (N trades consecutivos OK) + cooldown 7 días.
- `PAUSED → PROBATION`: auto tras N días + portfolio NORMAL + no third strike.
- `PROBATION → NORMAL`: auto tras N trades en PROBATION sin regresión.
- `PROBATION → PAUSED`: auto si trigger severo fire.

**`mixed_manual_pause`** (safer):
- Mismo que smart_auto PERO `PAUSED → PROBATION` es **solo manual** (operador).

### 4.6 Mecanismos de "smart auto" incluidos en MVP

Orientados a evitar flapping y reactivaciones prematuras:

1. **Cooldown mínimo entre transiciones** (días por tier, ver tabla §4.1 pero los valores concretos se derivan del slider).
2. **Evidencia sostenida**: N trades consecutivos OK + rolling metrics confirman recovery.
3. **Increasing caution**:
   - 2 PAUSED en 90 días → tercer pause requiere manual (aunque mode=smart_auto).
   - Exponential backoff: cada pause sucesivo aumenta el cooldown.
   - Recovery previa <2 semanas antes de re-PAUSE → símbolo "estructuralmente sospechoso", escalar a manual.
4. **Recovery gradual obligatoria**: PAUSED → PROBATION → NORMAL (no directo).
5. **Portfolio gate**: si `portfolio_state ∈ {REDUCED, FROZEN}` → **NO** auto-recovery de símbolos. Evita agregar exposición en momento adverso.

NO incluidos en MVP (candidatos para v2.1):
- Context-aware recovery (usar regime detector para modular).
- Historical learning (base de datos de resultados post-recovery para aprender).
- Minimum stability time before promotion más allá de los cooldowns.

---

## 5. Sistema vivo de recalibración (auto-calibrator daemon)

### 5.1 Arquitectura del daemon

Un thread daemon `kill_switch_calibrator_loop` en el backend (patrón parecido al `health_monitor_loop` del #138). Arranca con `start_scanner_thread()`.

Loop:
```python
def kill_switch_calibrator_loop(cfg_fn, stop_event):
    last_recalibration_ts = None
    while not stop_event.is_set():
        cfg = cfg_fn()
        triggers_fired = check_all_triggers(cfg)
        if triggers_fired:
            if rate_limit_ok(last_recalibration_ts, cfg):
                result = run_optimization(cfg)
                persist_recommendation(result, triggered_by=triggers_fired)
                notify_operator(result)
                last_recalibration_ts = now
        # Check safety net
        if last_recalibration_ts is None or (now - last_recalibration_ts).days > 30:
            result = run_optimization(cfg)
            persist_recommendation(result, triggered_by=["safety_net"])
            last_recalibration_ts = now
        sleep_until_next_hour(stop_event)
```

### 5.2 Triggers específicos

| Trigger | Condición | Check frequency |
|---|---|---|
| `regime_change` | `regime_score` cruza 60 (BULL/NEUTRAL) o 40 (NEUTRAL/BEAR) respecto a última calibración | diariamente 00:00 UTC |
| `portfolio_dd_degradation` | DD agregado actual > 1.5× (DD proyectado por la última calibración aprobada) | cada hora |
| `event_cascade` | ≥ 3 símbolos entraron ALERT/REDUCED/PAUSED en últimas 72h | cada hora |
| `safety_net` | 30 días desde última recalibración | diariamente |
| `manual` | `POST /kill_switch/recalibrate` | inmediato |

### 5.3 Optimización (función objetivo)

```python
def fitness(slider_value: float, cfg: dict) -> float:
    result = backtest_faithful(
        slider=slider_value,
        overrides=cfg["kill_switch"]["v2"]["advanced_overrides"],
        window=cfg["backtest_window"],
    )
    if result.portfolio_dd > cfg["kill_switch"]["v2"]["dd_target"]:
        return -math.inf
    return result.total_pnl

def optimize(cfg: dict) -> dict:
    grid = [0, 5, 10, 15, ..., 100]  # step 5
    results = {s: fitness(s, cfg) for s in grid}
    feasible = {s: r for s, r in results.items() if r != -math.inf}
    if not feasible:
        return {
            "status": "no_feasible",
            "nearest_slider": min(results, key=lambda s: backtest_dd(s)),
            "projected_dd": backtest_dd(nearest_slider),
            "projected_pnl": backtest_pnl(nearest_slider),
        }
    best = max(feasible, key=feasible.get)
    return {
        "status": "ok",
        "slider_optimal": best,
        "projected_pnl": feasible[best],
        "projected_dd": backtest_dd(best),
    }
```

### 5.4 Propuesta persistida a DB

Tabla `kill_switch_recommendations`:

```sql
CREATE TABLE kill_switch_recommendations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    triggered_by    TEXT NOT NULL,              -- JSON array: ["regime_change", "manual"]
    slider_value    REAL,                        -- null si no_feasible
    projected_pnl   REAL,
    projected_dd    REAL,
    status          TEXT NOT NULL,               -- "pending" | "applied" | "ignored" | "superseded" | "no_feasible"
    applied_ts      TEXT,
    applied_by      TEXT,                        -- "operator" (manual) o null
    report_json     TEXT NOT NULL                -- full optimization report
);
```

### 5.5 Notificación al operador

Al persistir una propuesta con `status="pending"`:
- Telegram via notifier (event type `system`): "Kill switch v2: nueva recomendación disponible. Mueve slider a Y%, proyectado +$N P&L con DD -Z%. Ver dashboard."
- Badge pulsante en header del frontend (patrón ya usado por auto-tune en #170).
- Link directo al componente de review.

### 5.6 Curvas del slider → thresholds

Para cada threshold del sistema, rango `[min, max]` interpolado **linealmente**:

```python
def compute_threshold(slider: float, t_min: float, t_max: float) -> float:
    return t_min + (slider / 100.0) * (t_max - t_min)
```

Valores iniciales (editables en `config.defaults.json`):

| Threshold | t_min (slider=0, laxo) | t_max (slider=100, paranoid) |
|---|---|---|
| `alert_win_rate_threshold` | 0.05 | 0.35 |
| `min_trades_for_eval` | 20 | 5 |
| `reduce_pnl_window_days` | 45 | 7 |
| `pause_months_consecutive` | 4 | 1 |
| `portfolio_dd_reduced_threshold` | -0.08 | -0.03 |
| `portfolio_dd_frozen_threshold` | -0.15 | -0.06 |
| `velocity_sl_count` | 10 | 3 |
| `velocity_window_hours` | 24 | 6 |

Thresholds enteros se redondean al entero más cercano.

### 5.7 Advanced overrides

Cuando `advanced_overrides.X != null`, el valor override se usa en lugar del derivado del slider principal:

```python
def effective_slider_for(dimension: str, cfg: dict) -> float:
    override = cfg["kill_switch"]["v2"]["advanced_overrides"].get(f"{dimension}_sensitivity")
    if override is not None:
        return override
    return cfg["kill_switch"]["v2"]["aggressiveness"]
```

Con dimensiones: `velocity`, `portfolio_dd`, `calibration`, etc. Permite al usuario power (dev) sobrescribir ejes individuales sin romper el comportamiento por default.

---

## 6. Rollout plan — 4 fases

### 6.1 Fase 1: Observability foundation (2 semanas)

Construir la infraestructura sobre la que v2 corre y se audita. Entregable de valor incluso sin v2.

Componentes:

1. **Tabla `kill_switch_decisions`** (append-only):
   ```sql
   CREATE TABLE kill_switch_decisions (
       id              INTEGER PRIMARY KEY AUTOINCREMENT,
       ts              TEXT NOT NULL,
       scan_id         INTEGER,
       symbol          TEXT NOT NULL,
       engine          TEXT NOT NULL,       -- "v1" | "v2_shadow" | "v2_live"
       per_symbol_tier TEXT NOT NULL,
       portfolio_tier  TEXT NOT NULL,
       velocity_active INTEGER DEFAULT 0,
       size_factor     REAL NOT NULL,
       skip            INTEGER NOT NULL,
       reasons_json    TEXT,
       slider_value    REAL
   );
   CREATE INDEX idx_decisions_ts ON kill_switch_decisions(ts);
   CREATE INDEX idx_decisions_symbol_ts ON kill_switch_decisions(symbol, ts);
   ```

2. **Endpoints** (read-only, `verify_api_key`):
   - `GET /kill_switch/decisions?symbol=X&since=Y&engine=Z&limit=N`
   - `GET /kill_switch/current_state` — snapshot de tier actual por símbolo + portfolio
   - `GET /kill_switch/recommendations` — propuestas del auto-calibrator

3. **Frontend `KillSwitchDashboard.tsx`**:
   - Grid de símbolos con tier + razón breve + siguiente threshold para transition.
   - Card portfolio-level con tier + DD actual vs peak.
   - Panel de propuestas pendientes del auto-calibrator.
   - (En fase 1, muestra v1 decisions. Post fase 3, muestra v2 también.)

4. **Wiring del v1 actual al decision log** — sin cambio funcional.

**Criterio de fin**: dashboard operativo mostrando v1 decisions en vivo; queries performanceando; operador reporta utilidad.

### 6.2 Fase 2: Features v2 en shadow (2-3 semanas)

Cada feature B1-B6 se implementa con target único: generar una decisión v2 para cada scan en paralelo a v1, y escribirla con `engine="v2_shadow"`.

```python
def scan(symbol):
    # ... setup ...
    v1_decision = kill_switch.v1.decide(symbol, ...)
    if cfg["kill_switch"]["v2"]["shadow_enabled"]:
        v2_decision = kill_switch.v2.decide(symbol, ...)
        log_decision(engine="v2_shadow", decision=v2_decision)
    log_decision(engine="v1", decision=v1_decision)
    # Only v1 decision affects trading
    final_decision = v1_decision
    return final_decision
```

Orden de implementación (priorizado por impacto DD esperado):

| Orden | Feature | Rationale |
|---|---|---|
| 1 | B2 portfolio-level circuit breaker | Máximo impacto sobre DD target |
| 2 | B4 auto-calibración per-symbol | Mayor signal-to-noise en tiers |
| 3 | B1 velocity triggers | Cortar rachas rápidas |
| 4 | B3 regime-aware thresholds | Smoothing por contexto |
| 5 | B5 tier PROBATION | Safety layer post-reactivación |
| 6 | Auto-calibrator daemon completo | Integra todos los features |

### 6.3 Fase 3: Shadow period (2-4 semanas)

v1 operativo; v2 en shadow. Ambos streams acumulan decisiones.

Análisis:
- **Daily diff report** automático (ejecutado por cron interno, no confundir con auto-calibrator): Telegram digest "v2 habría PAUSADO ETH 3 días antes que v1. Habría mantenido DOGE en NORMAL mientras v1 la redujo. Proyecta +$X P&L en el período."
- **Weekly review**: operator + developer revisan discrepancias.

**Exit criteria (todos deben cumplirse para pasar a fase 4)**:

1. **Cobertura**: v2 tomó decisiones en ≥ 2 semanas de mercado real.
2. **No symbol loss**: v2 no habría dejado fuera a ningún símbolo que v1 mantuvo y fue rentable.
3. **DD projection holds**: aplicando v2 retroactivamente, DD agregado ≤ 10% (o dentro del target del slider óptimo recomendado).
4. **No regressions**: ningún caso donde v2 habría abierto trades que v1 correctamente rechazó.
5. **Operator approval**: Samuel revisa reporte final y aprueba.

Si falla cualquier criterio: iterate en v2 code, extender shadow, re-evaluar.

### 6.4 Fase 4: Live activation (gradual con feature flags)

Feature flags en `config.defaults.json` → `kill_switch.v2.features.*` (ver §7).

Rollout:
- **Semana 1 live**: activar solo `portfolio_breaker`. Monitor.
- **Semana 2**: activar `per_symbol_calibration`.
- **Semana 3**: activar `velocity`.
- **Semana 4**: activar `regime_aware` + `probation_tier`.

Si en cualquier punto surge problema → flip el flag → revierte a v1 en segundos.

`shadow_enabled` continúa escribiendo en paralelo (para los features aún no-live), mantiene validation continua.

**Fin de fase 4**: todos los features live 4+ semanas estables; shadow puede desactivarse (o mantenerse como audit log).

---

## 7. Config schema v2 + migration

### 7.1 Shape completo

```json
{
  "kill_switch": {
    "enabled": true,
    "min_trades_for_eval": 10,
    "alert_win_rate_threshold": 0.30,
    "reduce_pnl_window_days": 14,
    "reduce_size_factor": 0.5,
    "pause_months_consecutive": 2,
    "auto_recovery_enabled": true,

    "v2": {
      "enabled": false,
      "shadow_enabled": true,
      "aggressiveness": 50,
      "auto_recovery_mode": "smart_auto",
      "dd_target": 0.10,
      "concurrent_alert_threshold": 3,
      "probation_trades_base": 10,
      "probation_per_pause_day": 0.2,
      "third_strike_days": 90,

      "features": {
        "velocity": false,
        "portfolio_breaker": false,
        "regime_aware": false,
        "per_symbol_calibration": false,
        "probation_tier": false
      },

      "advanced_overrides": {
        "velocity_sensitivity": null,
        "portfolio_dd_sensitivity": null,
        "calibration_strictness": null,
        "regime_adjustment_enabled": true
      },

      "thresholds": {
        "alert_win_rate":           { "min": 0.05,  "max": 0.35  },
        "min_trades_for_eval":      { "min": 20,    "max": 5     },
        "reduce_pnl_window_days":   { "min": 45,    "max": 7     },
        "pause_months_consecutive": { "min": 4,     "max": 1     },
        "portfolio_dd_reduced":     { "min": -0.08, "max": -0.03 },
        "portfolio_dd_frozen":      { "min": -0.15, "max": -0.06 },
        "velocity_sl_count":        { "min": 10,    "max": 3     },
        "velocity_window_hours":    { "min": 24,    "max": 6     }
      },

      "auto_calibrator": {
        "enabled": true,
        "max_per_day": 1,
        "min_cooldown_hours": 6,
        "safety_net_days": 30,
        "triggers": {
          "regime_change": true,
          "portfolio_dd_degradation": true,
          "event_cascade": {
            "enabled": true,
            "min_symbols": 3,
            "window_hours": 72
          }
        }
      },

      "regime_adjustments": {
        "bull_bonus": 10,
        "bear_penalty": 10
      },

      "cooldowns": {
        "reduced_to_normal_days": 7,
        "paused_to_probation_days": 14,
        "probation_trades_min": 10
      }
    }
  }
}
```

### 7.2 Nueva tabla para operator overrides

Frontend NO escribe a `config.json`. Escribe a tabla SQL para audit trail y concurrency safety:

```sql
CREATE TABLE kill_switch_config (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    key         TEXT NOT NULL,       -- "v2.aggressiveness", "v2.features.velocity", etc.
    value_json  TEXT NOT NULL,
    set_by      TEXT                  -- "operator" | "auto_calibrator" | "system"
);
CREATE INDEX idx_ks_config_key_ts ON kill_switch_config(key, ts);
```

### 7.3 Load config extendido

`btc_api.load_config()` se extiende — tras el deep-merge actual (hardcoded < defaults.json < secrets.json < legacy config.json < ENV), aplica overlay final desde la DB:

```python
def load_config() -> dict:
    cfg = existing_load_config()  # deep merge de files + env
    db_overrides = _load_kill_switch_db_overrides()
    for key, value in db_overrides.items():
        _set_nested(cfg, key.split("."), value)
    return cfg
```

Una sola query SQL adicional por load — negligible impact.

### 7.4 Migration v1 → v2

**Principio**: Simon no hace nada manual. Su config.json local sigue funcionando.

Flujo:
1. Post-merge del B0 spec + implementación: `config.defaults.json` incluye todos los v2 keys con `v2.enabled=false`. Cero efecto operativo.
2. `load_config()` deep-merge ya cubre el case.
3. Si Simon tiene keys locales que override, se respetan.
4. Simon nunca edita config a mano. Todas las configs v2 se hacen desde frontend, que escribe a DB.

### 7.5 Cleanup post-rollout

Cuando v2 estable 4+ semanas y operador firma off:
- PR separado: delete keys legacy (`kill_switch.min_trades_for_eval`, etc.) y re-nombra `kill_switch.v2` → `kill_switch`.
- Tests legacy actualizados o eliminados.

---

## 8. Features B1-B6 — detalles operativos

Cada feature es un sub-issue del epic #187. El spec detalla qué hace cada uno; la implementación concreta se define en los tickets.

### 8.1 B1 — velocity triggers

- Detector: N stop losses en M horas (N=3-10, M=6-24h, derivado del slider).
- Efecto: setea `velocity_cooldown_until = now + cooldown_hours` en el estado per-symbol.
- Reanudación: automática cuando `velocity_cooldown_until` expira.
- Integrado con composition multiplicativa (§4.3-4.4).

### 8.2 B2 — portfolio-level circuit breaker

- Track `portfolio_equity_peak` y `portfolio_equity_current` (computado agregando equity curves de todos los símbolos).
- Compute `portfolio_dd = (current - peak) / peak`.
- Triggers:
  - `portfolio_dd < portfolio_dd_reduced_threshold` → portfolio_state = REDUCED.
  - `portfolio_dd < portfolio_dd_frozen_threshold` → portfolio_state = FROZEN.
  - `count_symbols_in_alert >= concurrent_alert_threshold` → portfolio_state ≥ WARNED.
- Recovery: automática cuando DD mejora (con cooldown).

### 8.3 B3 — regime-aware thresholds

Leer `regime_score` (daily) y ajustar efectivamente el slider para cálculos:

```python
def adjusted_slider(cfg, regime_score):
    base = cfg.aggressiveness
    if regime_score >= 60:  # BULL
        return min(100, base + regime_bonus_bull)
    elif regime_score < 40:  # BEAR
        return max(0, base - regime_penalty_bear)
    return base  # NEUTRAL
```

Con `regime_bonus_bull = 10`, `regime_penalty_bear = 10` (configurables). Disable-able via `advanced_overrides.regime_adjustment_enabled`.

### 8.4 B4 — auto-calibración per-symbol

- Base de datos de métricas históricas por símbolo (WR histórica, σ, trades totales).
- Cuando `trades_total >= 100`: thresholds en términos de desvíos sobre el baseline propio.
  - ALERT: `WR_rolling_20 < baseline_WR - N*σ`.
- Cuando `trades_total < 100`: fallback a thresholds globales del slider (§5.6).
- Recalibración del baseline: weekly o triggered.

### 8.5 B5 — tier PROBATION

- Entrada: solo desde PAUSED tras reactivación.
- `size_factor = 0.5`.
- Duración: `probation_trades_base + probation_per_pause_day * days_paused`. Ej. si estuvo 15 días en PAUSED: 10 + 0.2*15 = 13 trades de PROBATION.
- Salida NORMAL: tras N trades consecutivos sin regresión.
- Salida PAUSED: regresión severa (WR<10% en 10 trades, por ejemplo).

### 8.6 B6 — dashboard observability

Componentes del dashboard:
- Grid de símbolos: columnas "Tier actual", "Razón", "Siguiente threshold", "Tiempo en tier".
- Card portfolio-level: DD actual, peak, tier.
- Panel propuestas auto-calibrator: pending con [Apply]/[Ignore]/[Ver detalle].
- Historial (tab): gráfico de slider_value + proyecciones P&L/DD a lo largo del tiempo.
- Alertas agregadas (Telegram): "3 símbolos en ALERT en las últimas 24h", "Portfolio DD 5% — REDUCED_PORTFOLIO activo".

---

## 9. Validation gate

### 9.1 Gate feature-level (por cada B1-B6 antes de entrar a shadow)

Cada feature en su PR debe:
1. Unit tests verdes específicos.
2. Backtest fiel parity: misma decisión backtest = misma decisión producción.
3. Micro-backtest per-feature: feature solo activo, compara vs baseline. Delta P&L + DD en PR body.

### 9.2 Gate epic-level (antes de salir fase 3 → fase 4)

Script `scripts/gate_kill_switch_v2.py`:

**Walk-forward validation**:
- Calibration window: 2021-01-01 → 2024-12-31 (3 años).
- Validation window: 2025-01-01 → 2026-04-18 (15 meses OOS).
- Encuentra `slider_value_optimal` en calibration.
- Aplica ese slider en validation.
- Mide P&L y DD.

**Criterios**:

| Criterio | Target |
|---|---|
| Portfolio DD agregado en validation | ≤ 10% |
| P&L agregado en validation vs baseline sin kill switch | ≥ 95% |
| No symbol pause sin razón | ningún símbolo rentable pausado en validation |
| Robustez: re-corre con slider ±10% del óptimo | DD ≤ 12%, P&L ≥ 90% |
| Shadow dominance | v2_shadow ≥ v1 en cada métrica de validation |

**Output**: exit 0 PASS, exit 1 FAIL. JSON report en `/tmp/kill_switch_v2_gate_report.json`.

### 9.3 Gate operational-level (fase 4 live)

Monitoreo continuo. Alertas si v2 live se desvía de proyecciones:

- Portfolio DD live > 12% → Telegram crítico + botón "Pause v2".
- 3+ símbolos entran en PAUSED en 7 días por v2 → warning + review.
- P&L mensual real < proyección backtest por 25% → sugerir recalibración.

Rollback: flip `kill_switch.v2.enabled=false` en la UI → 1 click.

---

## 10. Timeline estimado

Asumiendo epic #186 (refactor) completado previamente:

| Fase | Duración | Acumulado |
|---|---|---|
| Phase 1 — Observability foundation | 2 semanas | 2 |
| Phase 2 — Features en shadow | 2-3 semanas | 4-5 |
| Phase 3 — Shadow period | 2-4 semanas | 6-9 |
| Phase 4 — Gradual live activation | 4 semanas | 10-13 |
| **Total** | — | **~10-13 semanas** |

Si el refactor toma adicional (1-2 semanas extra), el total es 11-15 semanas.

---

## 11. Risks y mitigations

| Riesgo | Probabilidad | Mitigation |
|---|---|---|
| v2 destruye demasiado alpha | Media | Gate de ≥95% P&L vs baseline + feature flag rollback |
| Backtest no predice bien la vida real | Media | Shadow period 2-4 semanas compara lado a lado |
| Operator no entiende el slider | Baja | Sistema recomienda; operador aprueba; minimal cognitive load |
| Overfitting a datos históricos | Media | Walk-forward explícito en gate epic-level |
| v1 y v2 inconsistencias durante rollout | Baja | Compositional logic + feature flags granulares + shadow-mode audit |
| Dependency chain con epic #186 rompe | Media | A6 (rewire backtest) es el cuello de botella explícito |
| Decision log crece sin bound | Baja | Partition por ts; queries usan índices; retention policy futura si pasa 100M rows |

---

## 12. Referencias

- Epic #138 — Kill switch v1 (base)
- Epic #162 — Notifier centralizado (canal para alertas v2)
- Epic #186 — Refactor shared logic scanner+backtest (**dependency bloqueador**)
- Epic #187 — este epic
- PR #202 — Tighten v1 thresholds (bridge ya merged)
- PR #183 — Doc correction DD aggregate (merged)
- Spec canónico: `docs/superpowers/specs/es/2026-04-18-documento-completo-sistema-trading.md`
- FORMULA GANADORA: `docs/superpowers/specs/es/2026-04-17-formula-ganadora-resultados-finales.md`

---

## 13. Próximos pasos

1. **Samuel review** este spec.
2. Si approved → invocar `writing-plans` skill para producir implementation plan task-by-task.
3. Plan se descompone en tickets B0/B1/B2/B3/B4/B5/B6 existentes del epic #187.
4. Implementación subagent-driven sobre los tickets.
5. Gate checks automatizados antes de cada merge.
