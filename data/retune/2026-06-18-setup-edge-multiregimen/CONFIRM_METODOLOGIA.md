# Estudio de confirmación: ¿el momentum sobrevive al exit REAL de musikito? (responde P1 con número)

**Fecha:** 2026-06-20
**Por qué:** El roster dictaminó que el "edge" de momentum era de EXCURSIÓN (`max_fwd`), no de retorno
realizable (`rule_return` con stop −12% tocaba el stop), y levantó 3 BLOCKERS de Serrano: (1) n
inflado por autocorrelación de hits contiguos del mismo símbolo; (2) el stop −12% NO es como tradeaba
musikito (él usaba targets agresivos + runner); (3) supervivencia no cuantificada (los breakouts que
murieron están excluidos del cache → inflan el momentum). Este estudio mide el momentum con el exit
REAL + de-dup + stress de supervivencia. Resultado binario: ¿sobrevive el edge o no?

**Reuso:** mismo cache (386 símbolos) + `edge_study.compute_features` + las features de breakout de
`momentum_study`. Cero re-fetch.

---

## Definiciones CONGELADAS

### Regla-momentum (igual que momentum_study, conjunta)
`vivo AND breakout_20d AND vol_ratio ≥ 1.5 AND pct_vs_sma20 > 0`. Período de señales 2020–2025.

### (1) De-duplicación por episodio
Por símbolo, se recorren las fechas-hit en orden; se conserva un hit solo si han pasado **≥ 14 días**
desde el último hit conservado del MISMO símbolo. Esto colapsa un breakout que persiste/se repite en
días contiguos a UNA señal por episodio (corrige la autocorrelación → n efectivo honesto).

### (2) Exit REAL de musikito — escalera de targets + runner (NO un stop ajustado)
Entrada = `open` en t+1. Horizonte H = **30 días** (espacio para los targets + el runner).
- **Escalera:** TPs en [+15%, +30%, +50%, +90%], fracciones vendidas [0.25, 0.25, 0.20, 0.15].
  Un TP se "cobra" si el `high` en [t+1, t+H] alcanza `entrada × (1+TP)` (fill intrabar optimista —
  es el modelo de "vender en el target", como musikito).
- **Runner:** la fracción restante (0.15) cierra al `close` de t+H.
- **Piso de desastre (no es un stop ajustado, es un corte de catástrofe):** si el `low` toca
  `entrada × 0.50` (−50%) ANTES de cobrar TP1, toda la posición sale a −50%. Modela un colapso real
  (y aproxima una muerte in-sample). Es MUCHO más ancho que el stop −12% que mató el rule_return.
- `realized_return` = Σ(fracción_i × TP_i de los cobrados) + (fracción_no_vendida × ret al cierre t+H),
  o −50% si dispara el piso.

### (3) Control B1 (matcheado, de-dupeado, MISMO exit)
Por cada fecha con hits, muestra de pares VIVOS que NO rompieron (`breakout_20d == False`), de-dupeados
igual (≥14d/símbolo), con el MISMO exit-escalera. Determinista. Ratio hasta 1:3.

### (4) Stress de supervivencia (cuantifica el BLOCKER 3)
Los breakouts que murieron (delisted) no están en el cache. Se inyecta el peor caso paramétrico:
asumir que una fracción `p` de los hits-momentum habría muerto (runner + no-vendido → retorno total
−100% para ese hit). Se halla el **breakeven p**: el % de muertes-ocultas que iguala la MEDIA de
`realized_return` del setup con la de B1. Si un `p` chico ya lo borra → frágil; si requiere un `p`
grande → robusto. (La mediana es robusta a inyecciones; el stress se reporta sobre la MEDIA, donde
las catástrofes muerden.)

### Régimen, stats
Régimen ex-ante por breadth (igual). Por hit se reporta `realized_return` (escalera) y `max_fwd_14d`
(referencia de excursión). Medianas Y medias setup vs B1, global + por régimen. Mann-Whitney sobre
`realized_return`.

---

## Salidas
- `confirm_results.json` + `confirm_findings.md`.
- **Veredicto binario:** ¿la `realized_return` del setup-momentum (con el exit real + de-dup) supera a
  B1 (a) global, (b) en alt-bull, (c) fuera? ¿Y el breakeven-p de supervivencia es chico (frágil) o
  grande (robusto)?

## Después
Pasar el binario al roster / a Samuel. Si SOBREVIVE → el momentum es edge real y reabre el diseño
(detector/jugada). Si NO sobrevive → confirma definitivo el "no hay edge de selección", la costura AC7
de SP2 se sostiene, y cerramos la pregunta para siempre.
