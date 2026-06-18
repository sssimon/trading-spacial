# Estudio: ¿el setup de musikito tiene edge fuera de su régimen? (multi-régimen 2020–2025)

**Fecha:** 2026-06-18
**Por qué:** El estudio de 2019 (`uploads/musikito/setup_features.csv`) midió solo
correlaciones **univariadas** sobre **n=89** en **un solo régimen** (alt-bull 2019). El roster
(Serrano, BLOCKER metodológico) exige medir el **setup conjunto** vs baseline, con n grande y
varios regímenes, antes de reorientar el producto. Este estudio congela la regla y la mide.

**Pregunta decisiva:** ¿La regla-setup (no las llamadas individuales) supera a una baseline
(a) en general, (b) en régimen alt-bull, (c) FUERA de ese régimen? ¿El edge es
**condicional al régimen** (lo que predijo §1.2) o no existe?

**Fuente de datos:** klines diarias de Binance (API pública, fresca). NO se toca
`data/holdout/`, NO se llama `simulate_strategy`, NO `open_holdout`. Estudio independiente.

---

## Definiciones CONGELADAS (no se mueven a mitad del estudio)

### Universo
- Todos los pares **spot USDT** de Binance con ≥ 250 barras diarias antes de la fecha `t`
  (para SMA200 + ventanas de rango). Es el universo que Valles realmente escanea.
- Excluir stablecoins (USDC, BUSD, TUSD, DAI, FDUSD, USDP, GUSD) y tokens apalancados
  (sufijos UP/DOWN/BULL/BEAR).
- Solo fechas donde el símbolo ya tiene la historia requerida (los pares listados después de
  2020 entran al estudio recién cuando acumulan ≥250 barras).

### Features en `t` (todas ex-ante, calculadas con datos ≤ t)
- `pos_in_30d_range = (close_t − min(low,30d)) / (max(high,30d) − min(low,30d))`, con
  denominador clampeado a `max(hi−lo, 1e-9·close)`.
- `rsi14` (Wilder).
- `pct_vs_sma20`, `pct_vs_sma50` = (close − SMA)/SMA · 100.
- `consol_30d` = (max(high,30d) − min(low,30d)) / mediana(close,30d) · 100.
- `vol_ratio = mediana(quote_vol, 3d) / mediana(quote_vol, 30d)`.

### Gate de vida (proxy de `classify_liveness`)
- mediana(quote_vol, 30d) ≥ 500_000 USDT; no agonizante (mediana últimos 90d ≥ 0.5×
  mediana 90d previos); < 50% velas planas en 90d.

### La regla-setup (DOS variantes, para responder a Serrano)
- **MÍNIMA (solo el gate nuevo del roster):** vivo AND `pos_in_30d_range ≤ 0.25`.
- **CONJUNTA (el setup completo, AND):** vivo AND `pos_in_30d_range ≤ 0.25` AND `rsi14 < 40`
  AND `close < SMA20` AND `close < SMA50` AND `consol_30d ≤ 45` AND `vol_ratio ≤ 0.85`.
  (Umbrales de §1.1: picks pos=0.165, rsi=38.7, consol=40.6, vol=0.71.)

### Baselines (dos, ambas matcheadas por fecha → misma exposición de régimen)
- **B1 — control de estado:** para cada fecha con hits, muestra aleatoria de pares VIVOS ese
  día que **NO** cumplen el gate de posición (`pos_in_30d_range > 0.25`). Mismo día, distinto
  estado de coin. Es el control honesto.
- **B2 — universo vivo (continuidad con 2019):** cualquier par vivo en `t`,
  independiente del estado.

### Retorno forward (CONGELADO)
- Entrada = `open` en `t+1`.
- `max_fwd_7d / 14d / 30d` = (max high en k días − entrada) / entrada.
- `rule_return` = lo primero que ocurra: +20% TP, −12% SL, o cierre en `t+14`
  (imita la escalera de targets + "risk trade" del canal).
- `win15` = `max_fwd_14d ≥ 0.15` (≈ primer target de musikito).

### Régimen (ex-ante, disponible en `t`)
- `breadth_t` = fracción del universo vivo con `close > SMA50` en `t`.
- Buckets: **alt-bull** (breadth ≥ 0.6), **neutral** (0.4–0.6), **bear** (< 0.4).
- Reportar también por año calendario (2020…2025) como cross-check.

---

## Salidas exigidas
1. `results.json` — tablas: {regla-mínima, regla-conjunta} × {B1, B2}, global + por bucket de
   régimen + por año. Cada celda: n, mediana y media de `max_fwd_14d`, mediana `rule_return`,
   `win15`.
2. Delta setup−baseline por bucket. **Mann-Whitney U** (p-value) de `max_fwd_14d` setup vs B1
   por bucket (n grande lo permite).
3. `findings.md` — veredicto honesto en 1–2 párrafos: ¿la regla-conjunta supera a B1
   (a) global, (b) en alt-bull, (c) fuera de alt-bull? ¿El edge es condicional al régimen?
   Caveats (supervivencia de símbolos, quote en USDT incluye el movimiento de BTC).
