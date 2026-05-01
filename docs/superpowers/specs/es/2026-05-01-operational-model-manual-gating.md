# Modelo operacional: gating manual de señales

**Fecha:** 2026-05-01
**Issue:** #283
**Tono:** descriptivo (captura el estado al 2026-05-01, no propone cambios)

---

## 1. Contexto y propósito

El sistema genera señales de trading de forma automática (scanner → score → exclusiones → notificación), pero las decisiones de **entrada** y **cierre** de posiciones requieren aprobación manual del operador via CLI o frontend autenticado.

Este spec materializa una decisión operacional que hasta ahora vivía implícita en el código (`btc_scanner.py:305-335`) y en convenciones no escritas. No introduce comportamiento nuevo: documenta el modelo vigente para que cualquier trabajo futuro sobre validación, automatización o comparación backtest-vs-live tenga una referencia explícita.

## 2. Pipeline determinista vs gating manual

**Lo que el scanner hace por sí solo (determinista):**
- Fetch OHLCV (Binance, Bybit fallback) en 4 timeframes (5m, 1h, 4h, 1d).
- Cálculo de indicadores (LRC, RSI, BB, SMA100, ATR, ADX, divergencias, engulfings).
- Regime detector (composite F&G + funding + price).
- Score multi-timeframe (0–9).
- Evaluación booleana de exclusiones automáticas (E1, E6) e informativas (E7).
- Persistencia a `signals.db` y emisión de notificación a Telegram.

**Lo que requiere intervención humana:**
- Verificación de las exclusiones marcadas `VERIFICAR_MANUAL` (E2, E3, E4, E5).
- Decisión final de abrir la posición.
- Decisión de cerrar manualmente antes de SL/TP.

## 3. Exclusiones E1–E7 — clasificación auto vs manual

Tabla extraída literal de `btc_scanner.py:305-335` (commit `4214ca8`):

| ID | Campo `activo` | Tipo | Bloquea entrada |
|----|---------------|------|-----------------|
| E1_BullEngulfing | `bull_eng` (boolean) | Auto | Sí, si `True` |
| E2_Noticias_Macro | `"VERIFICAR_MANUAL"` | Manual | Operador decide |
| E3_RachaPerdedora | `"VERIFICAR_MANUAL"` | Manual | Operador decide |
| E4_Capital_Min | `"VERIFICAR_MANUAL"` | Manual | Operador decide |
| E5_Cooldown | `"VERIFICAR_MANUAL"` | Manual | Operador decide |
| E6_Divergencia_Bajista | `bear_div` (boolean) | Auto | Sí, si `True` |
| E7_Tendencia_Fuerte | `"INFORMATIVO"` | Informativo | No |

Resumen: 4 manual-check + 2 auto + 1 informativo.

## 4. Flujo de aprobación

1. Scanner emite señal: score + estado de cada exclusión + indicadores resumidos.
2. Notificación sale por Telegram (push al chat configurado en `config.json`).
   - **Telegram es outbound only.** No hay bot de entrada que reciba aprobaciones. Las decisiones nunca se confirman vía Telegram.
3. Operador revisa la señal en el frontend (`http://localhost:5173` en dev, `https://trading.sdar.dev` en prod) o vía la CLI.
4. Operador resuelve manualmente las exclusiones marcadas `VERIFICAR_MANUAL` (E2–E5) consultando el contexto necesario (calendar de noticias para E2, historial de trades para E3, balance para E4, último exit para E5).
5. Si las manual-checks pasan, operador ejecuta entrada via `POST /positions`.
6. Cierre manual (cuando aplica) via `POST /positions/{id}/close`. SL/TP automáticos siguen activos en background y disparan cierres aun sin intervención.

## 5. Implicancias para validación (backtest vs live)

El backtest enforcea **automáticamente** la exclusión E5_Cooldown porque es una simulación cerrada, sin operador. El código vive en `backtest.py:486-490`:

```python
# ── Cooldown check ────────────────────────────────────────────────
if last_exit_time is not None:
    hours_since = (bar_time - last_exit_time).total_seconds() / 3600
    if hours_since < COOLDOWN_H:
        continue
```

`COOLDOWN_H = 6` se importa desde `btc_scanner.py:131`. La constante es **idéntica** entre scanner y backtest; lo que cambia es **quién la enforcea**:

| Contexto | Quién enforcea E5 | Cómo |
|----------|-------------------|------|
| Backtest | Simulador | Skip de la barra si `hours_since < COOLDOWN_H` |
| Live | Operador | Inspección manual del `nota` reportada por scanner |

**Esto NO es drift.** Es la diferencia esperada entre simulación cerrada y operación supervisada. El `COOLDOWN_H = 6` es la misma fuente de verdad para ambos contextos; sólo el mecanismo de enforcement difiere.

**Implicancia para A.4 (#250) y cualquier evaluación holdout:** los resultados del backtest reflejan el comportamiento bajo enforce automático del cooldown. Trasladar conclusiones a producción asume que el operador es disciplinado en el manual-check de E5. Si en algún momento se observa divergencia material entre la densidad de trades del backtest y la real, este spec es el lugar a citar para encuadrar el análisis.

El mismo razonamiento aplica a E2, E3 y E4: el backtest no las simula (no tiene calendar de noticias, no rastrea racha psicológica del operador, no modela balance externo). Cualquier comparación backtest-vs-live debe asumir que estas exclusiones operan asimétricamente.

## 6. Lectura del diseño actual

Las 4 exclusiones manual-check (E2–E5) están agrupadas en el mismo dict de `btc_scanner.py:305-335`, todas con `"activo": "VERIFICAR_MANUAL"` y un `nota` describiendo qué consultar. E5_Cooldown es técnicamente auto-enforceable (la información necesaria — timestamp del último exit — vive en `signals.db` y es accesible por el scanner), pero está al lado de E2–E4 que sí requieren información externa al sistema.

Si esta agrupación fue decisión deliberada o evolución histórica no está documentado en código ni en commits previos. Este spec captura el **estado**, no infiere intent.

## 7. Promotion to auto-enforcement (open)

No existen criterios definidos para migrar exclusiones manual-check a auto-enforcement. Cualquier propuesta — auto-enforce E5 leyendo `signals.db`, integrar un proveedor de calendar para E2, derivar E3 de la última racha en `positions`, leer balance via API de exchange para E4 — requiere ticket nuevo con:

- Scope explícito (qué exclusión, en qué condiciones).
- Criterios de aceptación (cómo se valida que el auto-enforcement coincide con la decisión humana actual).
- Plan de validación (backtest + paper trading + golden-path manual antes de promover a default).

Este spec **no define esos criterios**. Su rol es servir de baseline contra el cual cualquier propuesta futura pueda compararse.

## 8. Referencias

- `btc_scanner.py:305-335` — tabla de exclusiones E1–E7.
- `btc_scanner.py:131` — `COOLDOWN_H = 6` (constante compartida).
- `backtest.py:486-490` — enforce automático de E5 en simulación.
- `btc_api.py` — endpoints `POST /positions` y `POST /positions/{id}/close` que materializan la aprobación manual.
- Issue #283 — modelo operacional: producción manual vs backtest automático (cierra con este spec).
- Issue #284 — análisis previo que confirmó que `COOLDOWN_H = 6` es consistente entre código y docs (cerrado como outdated).
- `docs/strategy-backtest-report.md` — reporte del backtest; menciona "6h cooldown" en §2 Methodology con referencia cruzada a este spec.
