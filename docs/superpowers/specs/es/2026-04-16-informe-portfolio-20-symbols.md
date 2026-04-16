# Informe de Portfolio: Backtest de 20 Symbols

**Fecha:** 16 de Abril 2026
**Autor:** Samuel Ballesteros
**Para:** Simon Ballesteros

---

## 1. Resumen Ejecutivo

Ejecutamos la estrategia Spot V6 (con ATR dinamico y detector de regimen) en los 20 symbols que el sistema monitorea en produccion. Los resultados revelan un problema critico:

**La estrategia que gana +62% en BTC, PIERDE -$14,655 cuando opera los 20 symbols con los mismos parametros.**

Solo 4 de 20 symbols son rentables. Los parametros ATR optimizados para BTC no funcionan para altcoins.

---

## 2. Resultados Completos

### Ranking de Rentabilidad (mayor a menor)

| # | Symbol | Trades | Win Rate | Profit Factor | Retorno | Max DD | P&L Neto |
|---|--------|--------|----------|---------------|---------|--------|----------|
| 1 | **DOGEUSDT** | 270 | **43.7%** | **1.82** | **+128.5%** | -9.6% | **+$12,854** |
| 2 | **BTCUSDT** | 337 | 18.7% | 1.24 | +62.4% | -15.2% | +$6,243 |
| 3 | **XLMUSDT** | 210 | 34.8% | 1.24 | +24.0% | -15.7% | +$2,400 |
| 4 | **ADAUSDT** | 250 | 22.8% | 1.09 | +13.4% | -20.9% | +$1,341 |
| 5 | AVAXUSDT | 261 | 16.1% | 0.99 | -1.6% | -22.8% | -$161 |
| 6 | UNIUSDT | 259 | 13.9% | 0.99 | -2.2% | -29.6% | -$220 |
| 7 | OPUSDT | 230 | 13.9% | 0.90 | -11.9% | -29.8% | -$1,189 |
| 8 | ATOMUSDT | 204 | 16.2% | 0.91 | -12.3% | -29.4% | -$1,231 |
| 9 | FILUSDT | 209 | 12.9% | 0.87 | -16.5% | -33.7% | -$1,652 |
| 10 | XRPUSDT | 256 | 18.8% | 0.87 | -17.2% | -29.2% | -$1,720 |
| 11 | MATICUSDT | 127 | 13.4% | 0.71 | -19.8% | -29.3% | -$1,977 |
| 12 | SOLUSDT | 326 | 12.6% | 0.86 | -22.3% | -36.5% | -$2,231 |
| 13 | BNBUSDT | 306 | 13.7% | 0.85 | -23.8% | -47.8% | -$2,376 |
| 14 | ARBUSDT | 190 | 13.7% | 0.77 | -23.9% | -25.6% | -$2,394 |
| 15 | APTUSDT | 215 | 11.2% | 0.72 | -29.3% | -31.7% | -$2,932 |
| 16 | NEARUSDT | 269 | 12.6% | 0.77 | -30.6% | -42.1% | -$3,064 |
| 17 | DOTUSDT | 246 | 13.8% | 0.74 | -35.5% | -37.7% | -$3,552 |
| 18 | ETHUSDT | 293 | 12.6% | 0.81 | -39.3% | -53.7% | -$3,928 |
| 19 | LINKUSDT | 274 | 12.4% | 0.71 | -39.9% | -45.4% | -$3,987 |
| 20 | LTCUSDT | 266 | 11.3% | 0.66 | -48.8% | -53.5% | -$4,880 |

### Totales

| Metrica | Valor |
|---------|-------|
| **Symbols rentables** | **4 de 20** |
| **Symbols con perdida** | **16 de 20** |
| **Total trades** | 4,998 (~128/mes) |
| **P&L ganadores** | +$22,837 |
| **P&L perdedores** | -$37,492 |
| **P&L NETO PORTFOLIO** | **-$14,655** |

---

## 3. Por Que Falla en la Mayoria de Altcoins

### Razon 1: Los Parametros ATR Fueron Optimizados Solo Para BTC

Los multiplicadores ATR (SL=1.0x, TP=4.0x) salieron de un grid search de 105 combinaciones **usando solo datos de BTC**. BTC tiene un perfil de volatilidad unico:

- Menor volatilidad relativa que la mayoria de altcoins
- Movimientos mas predecibles y menos erraticos
- Ciclos de mean-reversion mas limpios

Las altcoins como SOL, LINK, DOT, NEAR tienen volatilidad 2-5x mayor que BTC. Un TP de 4x ATR en estas monedas es un objetivo extremadamente lejano que rara vez se alcanza.

### Razon 2: El Ratio 4:1 Es Demasiado Ambicioso Para Altcoins

Con TP = 4x ATR, necesitas que el precio recorra una distancia enorme para ganar. En BTC esto funciona porque los rebotes son fuertes. En altcoins, los rebotes son debiles y erraticos — el precio sube un poco y vuelve a caer, activando el SL antes de llegar al TP.

Dato: En los 16 symbols perdedores, el **win rate promedio es 13.5%** — ganan 1 de cada 7 trades. No hay ratio R:R que compense eso.

### Razon 3: No Todas Las Altcoins Tienen Ciclos de Mean-Reversion

Nuestra estrategia es mean-reversion: compra cuando el precio cae a zona baja del canal y espera que vuelva al promedio. Esto funciona en activos que:
- Oscilan alrededor de un promedio (BTC, DOGE, XLM, ADA)
- Tienen soporte/resistencia respetados

Muchas altcoins en 2023-2025 tuvieron **tendencias unidireccionales sostenidas** (DOT, MATIC cayendo continuamente) donde el mean-reversion compra en la bajada pero el precio sigue bajando.

---

## 4. Los 4 Ganadores — Por Que Funcionan

### DOGE (+$12,854, 43.7% WR)
El mejor resultado del portfolio. DOGE tiene ciclos de pump/dump extremos impulsados por redes sociales (Elon Musk). Estos ciclos crean **oportunidades perfectas de mean-reversion**: cae rapido al cuartil inferior del canal, y rebota con fuerza. El TP de 4x ATR se alcanza durante los pumps.

### BTC (+$6,243, 18.7% WR)
Para lo que fue disenada la estrategia. Ciclos de mean-reversion limpios, volatilidad predecible, rebotes fuertes desde soporte.

### XLM (+$2,400, 34.8% WR)
XLM tiene un patron similar a ADA — oscila en rangos amplios con buen respeto de soporte/resistencia. Win rate alto sugiere que los parametros se ajustan bien a su perfil.

### ADA (+$1,341, 22.8% WR)
Cardano oscila en rangos definidos con menos tendencias unidireccionales que otras altcoins. Los rebotes son lo suficientemente fuertes para alcanzar el TP.

---

## 5. Propuesta de Optimizacion Per-Symbol

### El Enfoque

En vez de usar los mismos parametros para todos, cada symbol necesita sus propios multiplicadores ATR optimizados. Los frameworks profesionales (Freqtrade, Jesse) hacen exactamente esto: **hyperopt por par de trading**.

### Como Funciona

1. Para cada symbol, correr el grid search de multiplicadores ATR (como hicimos con BTC)
2. Encontrar el SL/TP optimo para cada uno
3. Solo operar symbols donde el backtest sea rentable
4. Guardar los parametros optimizados en config

### Resultados Esperados

- Eliminar los 16 symbols perdedores (o encontrar parametros que los hagan rentables)
- Mantener los 4 ganadores con sus parametros optimales
- Posiblemente rescatar 3-5 symbols que estan cerca del breakeven (AVAX, UNI, ATOM)

### Referencia: Como Lo Hacen Los Mejores

**Freqtrade (hyperopt):** Corre miles de combinaciones por par, selecciona los mejores parametros por profit factor, aplica walk-forward validation.

**Jesse (optimization):** Parametros por symbol en la clase Strategy, optimizacion integrada.

**OctoBot:** Evaluadores por activo con pesos diferentes.

**NostalgiaForInfinity (NFIX):** Diferentes "buy tags" con parametros distintos por tipo de senal — efectivamente parametros per-symbol.

**Hummingbot:** Configuracion por par de trading con spreads y niveles independientes.

---

## 6. Siguiente Paso Recomendado

Correr el grid search de multiplicadores ATR para cada uno de los 20 symbols. El resultado sera un mapa de:

```
BTCUSDT:  SL=1.0x  TP=4.0x  → +62.4%  ✅ OPERAR
DOGEUSDT: SL=??    TP=??    → ???     ✅/❌
ETHUSDT:  SL=??    TP=??    → ???     ✅/❌
...
```

Solo se operan los symbols donde el backtest optimizado sea rentable. Los demas se monitorean pero no se generan senales.

**Tiempo estimado:** ~2 horas de computo (105 combinaciones × 20 symbols).
**Impacto esperado:** Portfolio que pasa de -$14,655 a potencialmente +$20,000-$40,000.

---

## 7. Benchmark: Las 5 Herramientas Mas Potentes de GitHub

| # | Herramienta | Estrellas | Como Maneja Multi-Asset |
|---|-------------|-----------|-------------------------|
| 1 | **Freqtrade** | ~28,000 | `HyperoptResolver` optimiza parametros por par. `custom_stoploss()` recibe el nombre del par, permite ATR diferente por symbol. `VolumePairList` filtra automaticamente que altcoins operar. |
| 2 | **Jesse** | ~6,000 | Cada ruta es `(exchange, symbol, timeframe, strategy)` — naturalmente corre estrategias diferentes por activo. Optimizacion genetica per-route integrada. |
| 3 | **Hummingbot** | ~8,000 | `VolatilityIndicator` ajusta spreads por activo automaticamente. Excelente para market-making en altcoins. |
| 4 | **OctoBot** | ~3,000 | Evaluadores modulares con pesos configurables por activo. Optimizador AI integrado. |
| 5 | **Superalgos** | ~4,000 | Editor visual multi-timeframe, multi-asset nativo. Bots conectan a multiples exchanges simultaneamente. |

**Patron comun en TODOS:** Ningun sistema serio usa los mismos parametros para todos los symbols. Cada uno tiene mecanismo de parametros per-asset.

---

## 8. Las 5 Mejores Estrategias Para Altcoins

### Estrategia 1: Mean-Reversion Normalizado por Volatilidad
**La mas relevante para nosotros.** En vez de usar `LRC_LONG_MAX = 25%` para todos:

```
threshold = base_threshold × (BTC_ATR_pct / symbol_ATR_pct)
```

Altcoins con baja volatilidad (XLM) obtienen zonas mas tight. Altcoins con alta volatilidad (DOGE) obtienen zonas mas amplias. Esto adapta la estrategia al perfil de cada activo.

### Estrategia 2: Rotacion por Fuerza Relativa (RSR)
Rankear altcoins por momentum relativo a BTC en 14 dias y 3 dias. Solo operar el cuartil superior (las mas fuertes relativamente).

```
RS = retorno_14d_symbol / retorno_14d_BTC
```

Solo operar symbols con RS alto — estan en momentum propio.

### Estrategia 3: Filtro de Correlacion
Altcoins con correlacion < 0.6 contra BTC en los ultimos 30 dias estan operando con narrativa propia (noticias del ecosistema, token unlocks). Estas son mejores candidatas.

### Estrategia 4: Acumulacion por Volumen (Volume Profile)
Cuando el precio esta debajo del VWAP por >24h con volumen decreciente, y luego sube sobre VWAP con 2x volumen promedio → senal de acumulacion. Mas fiable en altcoins que en BTC.

### Estrategia 5: RSI Divergencia Multi-TF con ADX
Similar a lo que ya tenemos, pero:
- ADX < 20 (no 25) para altcoins — rangean mas seguido
- RSI(7) en vez de RSI(14) para alts de alta volatilidad (SOL, DOGE, AVAX) — ciclos mas rapidos

---

## 9. Diferencias Clave: BTC vs Altcoins

| Caracteristica | BTC | Altcoins |
|----------------|-----|----------|
| **Volatilidad diaria** | ~2-4% | ~5-15% (2-5x mas) |
| **Ciclos de mean-reversion** | Limpios, predecibles | Erraticos, mas rapidos |
| **Correlacion** | Es el lider del mercado | Alta (>0.8) pero se desacoplan en pumps |
| **Pumps** | Graduales (semanas) | Explosivos (horas/dias) |
| **Dumps** | Moderados | Profundos y rapidos |
| **Respeto de soporte** | Alto | Variable |

**Implicacion:** No puedes usar los mismos parametros. Un SL de 1.0x ATR en BTC puede ser $700. En DOGE puede ser $0.02 (equivalente a un movimiento normal de 5 minutos).

---

## 10. Plan de Accion: Optimizacion Per-Symbol

### Fase 1: Grid Search por Symbol
Para cada uno de los 20 symbols, correr el grid search de multiplicadores ATR (como hicimos con BTC). Encontrar el SL/TP optimo para cada uno.

### Fase 2: Filtro de Rentabilidad
Solo incluir en el portfolio symbols donde la mejor combinacion de parametros sea rentable (PF > 1.1 y return > 0%).

### Fase 3: Implementar Symbol Overrides
Agregar a `config.json`:

```json
{
  "symbol_overrides": {
    "BTCUSDT":  {"atr_sl_mult": 1.0, "atr_tp_mult": 4.0},
    "DOGEUSDT": {"atr_sl_mult": 0.8, "atr_tp_mult": 2.5},
    "ETHUSDT":  {"atr_sl_mult": 1.2, "atr_tp_mult": 3.0},
    "SOLUSDT":  false
  }
}
```

`false` = no operar ese symbol. Cada symbol tiene sus parametros optimales. El scanner lee estos overrides.

### Fase 4: Validacion
Re-correr el backtest de portfolio con los parametros optimizados. Verificar que el portfolio consolidado es rentable.

---

## 11. Conclusion

La estrategia Spot V6 **funciona excelente en BTC** (+62.4%) pero **no es multi-asset por defecto**. Esto no es un defecto — es la realidad de como funcionan los mercados. Ningun sistema profesional usa parametros identicos para todos los activos.

El siguiente paso es optimizar los parametros por symbol, filtrar los no-rentables, y operar un portfolio curado de 5-10 symbols con parametros individuales.

**La diferencia entre perder $14,655 y ganar potencialmente $40,000+ es simplemente: parametros correctos para cada activo.**
