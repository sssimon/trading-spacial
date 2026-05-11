# Carta para revisor externo — A.4 hallazgo de inflexión metodológica

**Para:** revisor externo (humano)
**De:** sssamuelll
**Fecha:** 2026-05-11
**Asunto:** Hallazgo metodológico en A.4 pre-holdout — pre-registración pausada, solicito revisión

---

Hola,

Te escribo porque me topé con un hallazgo metodológicamente significativo en la fase A.4 (re-tune pre-holdout) del epic de validación de trading-spacial y antes de tomar la siguiente decisión necesito un par de ojos externos. Es bala única lo que queda por hacer (A.4-3 holdout), así que prefiero pausar y revisar.

## Contexto en 3 oraciones

1. Llevamos varias semanas haciendo correcciones estructurales al simulador de backtest para que refleje condiciones live-equivalent (phantom-profit fix #223/#224, K=10 overshoot cap #309, per-symbol bankruptcy halt #313). Cada corrección salió de un halt durante un sweep, motivada por evidencia concreta.
2. Hoy corrí los dos sweeps que pre-registramos en spec D9 §2.10 — A.4-1 ATR y A.4-1.5 regime — sobre el pre-holdout window con TODAS las correcciones activas. Ambos retornaron lo que, en lectura literal, significa "no hay edge en el grid disponible para ningún símbolo del basket curado de 10 monedas".
3. Antes de quemar la bala única del A.4-3 holdout (o tomar cualquier acción sobre el finding), me gustaría que alguien externo a la cadena de decisiones validation pre-registers que me condujeron hasta aquí.

## Documentos en orden de lectura

**Lectura mínima (~20 min):**

1. `docs/superpowers/specs/es/2026-05-11-a4-hallazgo-inflexion-metodologica.md` — el spec central. §1 resumen ejecutivo, §2 evidencia, §3 interpretación, §4 preguntas explícitas, §7 lista de preguntas concretas para ti.

**Lectura para contexto adicional (~40 min más):**

2. `CLAUDE.md` — secciones "Validation Methodology" y "Caveats heredados — A.4 (#250)" #1 y #4. Estas son las pre-registraciones contra las que se mide el hallazgo.
3. `docs/superpowers/specs/es/2026-05-03-asunciones-tecnicas-pre-holdout.md` (D9) — los pre-registers explícitos que estábamos siguiendo.
4. `data/retune/2026-05-11-pre-holdout-regime-evidence/README.md` y `data/retune/2026-05-11-pre-holdout-atr-evidence/README.md` — evidencia cruda de los dos sweeps de hoy.

**Profundización si tenés inclinación técnica:**

5. `backtest.py` líneas alrededor de `_close_position`, `BANKRUPTCY_THRESHOLD`, `MAX_OVERSHOOT_RATIO` — el simulador.
6. `tools/retune_pre_holdout.py` y `tools/regime_retune_pre_holdout.py` — los harnesses.
7. PR history: #309, #313, #287, #315 — las correcciones estructurales y la evidencia.

## Lo que necesito de vos

Las 7 preguntas en §7 del spec central. Si solo podés responder algunas, las más críticas:

1. **¿`cfg + symbol_overrides` es el path correcto para el ATR re-tune?** (Si la respuesta es "no, el legacy kwargs path es defendible para ATR tuning specifically", ese feedback solo invalidaría el hallazgo de hoy entero.)
2. **¿El grid actual es suficiente, o necesitamos expandir antes de aceptar NO_DATA?** Mi instinto dice expandir 5-10x antes de declarar "no edge en el grid". Quiero contraste.
3. **¿Vale la pena correr A.4-3 ahora?** Es bala única. Pre-registrar el hallazgo como predicción y testarlo en holdout vs simplemente parar son opciones que defendí ambas en distintos momentos.

## Lo que NO necesito

No necesito que valides cada uno de los fixes estructurales individualmente. Los reviewers anteriores (incluyendo vos, si participaste de #309 o #313) ya los validaron. Lo que necesito es **revisión meta-metodológica** sobre el orden de operaciones y la interpretación.

## Tiempo aproximado

Si solo querés responder las 3 preguntas críticas: ~30-45 min. Si querés engagement completo con el spec: ~2-3 horas. Cualquier nivel ayuda.

## Constraint operativa

El proyecto está pausado en este nodo. No mergeo nada de A.4-3 ni promotion ni código nuevo hasta tener tu input. PR #315 y el PR que abriré después de este mensaje son docs/evidencia archivada — no afectan main code path. Mergearlos no requiere tu aprobación.

Gracias,
sssamuelll
