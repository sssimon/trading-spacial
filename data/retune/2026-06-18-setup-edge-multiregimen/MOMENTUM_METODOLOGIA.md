# Estudio: ¿la lectura MOMENTUM de musikito tiene edge? (la pregunta abierta)

**Fecha:** 2026-06-20
**Por qué:** El estudio multi-régimen midió la lectura-DEBILIDAD (las features de ENTRADA que
reverse-engineamos: parte baja del rango, sobreventa, bajo SMA) y la refutó — no tiene edge en
ningún régimen. PERO el template DECLARADO del canal musikito era **momentum/breakout** (zona de
compra + targets agresivos + OPEN TARGET runner, "Risk Trade", horizonte rápido — cazar coins a
punto de explotar). Refutamos la lectura-debilidad; **nunca medimos la lectura-momentum.** Antes
de declarar "no existe NINGÚN edge de selección per-coin", hay que probar lo que el canal *decía*
que hacía.

**Pregunta decisiva:** ¿Un setup de momentum/breakout supera a una baseline (a) global,
(b) en alt-bull, (c) fuera de alt-bull? ¿El edge existe donde la debilidad no lo tuvo?

**Reuso:** mismo harness que `edge_study.py` (cache de 430 símbolos USDT, panel 2020–2025,
mismas baselines matcheadas por fecha, Mann-Whitney, buckets de régimen por breadth). Solo cambia
la REGLA (momentum en vez de debilidad) y su control. Cero re-fetch.

---

## Definiciones CONGELADAS

### Features de momentum nuevas (sobre el panel existente)
- `high_20d_prev = max(high, 20d) .shift(1)` — el máximo de los 20 días PREVIOS (excluye hoy).
- `breakout_20d = close > high_20d_prev` — el cierre rompió por encima del máximo de 20 días
  (breakout = fuerza, lo opuesto a "parte baja del rango").

Ya existen en el panel (de `edge_study.compute_features`): `vol_ratio` (3d/30d), `pct_vs_sma20`,
`above_sma50`, `rsi14`, y los retornos forward (`max_fwd_7d/14d/30d`, `rule_return`).

### La regla-momentum (DOS variantes, espejo de la de debilidad)
- **MÍNIMA (solo el breakout):** `vivo AND breakout_20d`.
- **CONJUNTA (momentum completo):** `vivo AND breakout_20d AND vol_ratio ≥ 1.5 AND
  pct_vs_sma20 > 0` — rompió el máximo de 20d, con **repunte de volumen** (3d ≥ 1.5× el de 30d) y
  **por encima de su SMA20** (fuerza). Es el setup "a punto de explotar" que el template describe.

### Baseline (control de estado, matcheado por fecha)
- **B1 — control momentum:** por cada fecha con hits, muestra aleatoria de pares VIVOS ese día que
  **NO** rompieron (`breakout_20d == False`). Mismo día, distinto estado. Determinista (semilla por
  fecha), ratio hasta 1:3. Es el control honesto para momentum.
- **B2 — universo vivo** (continuidad): cualquier par vivo en `t`.

### Resto (idéntico a edge_study, NO se mueve)
- Universo: derivado del cache (430 símbolos), filtrado a ≥250 barras.
- Retorno forward: entrada `open` en t+1; `max_fwd_7d/14d/30d`; `rule_return` (+20% TP / −12% SL /
  cierre t+14); `win15 = max_fwd_14d ≥ 0.15`.
- Régimen ex-ante: `breadth_t` = fracción del universo vivo con close>SMA50; buckets alt-bull≥0.6,
  neutral 0.4–0.6, bear<0.4. También por año.
- Stats: medianas/medias de `max_fwd_14d` y `rule_return`, `win15`, delta setup−B1, Mann-Whitney
  one-sided (¿setup > baseline?).

---

## Salidas
- `momentum_results.json` — tablas {momentum-mínima, momentum-conjunta} × {B1, B2}, global + por
  régimen + por año.
- `momentum_findings.md` — veredicto honesto: ¿la regla-momentum supera a B1 (a) global,
  (b) en alt-bull, (c) fuera de alt-bull? ¿Hay edge donde la debilidad no lo tuvo? Mismos caveats
  (sesgo de supervivencia: el cache solo tiene símbolos vivos hoy → niveles inflados para todos por
  igual, el DELTA sigue informativo; retorno en USDT incluye beta de BTC).

## Después
Pasar los hallazgos al roster (patrón: medir → roster), para el veredicto final sobre si existe
ALGÚN edge de selección per-coin, o ninguno (debilidad NO + momentum ?).
