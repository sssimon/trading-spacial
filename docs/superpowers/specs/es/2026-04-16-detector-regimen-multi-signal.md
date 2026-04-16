# Detector de Regimen Multi-Signal

**Fecha:** 2026-04-16
**Estado:** Implementado y en produccion

---

## Que Es

Un modulo inteligente que detecta automaticamente si el mercado esta en fase alcista (BULL), bajista (BEAR), o neutral. Combina tres fuentes de informacion independientes para tomar la decision.

El scanner consulta este modulo antes de generar senales. Si el mercado es BULL, solo genera senales LONG. Si es BEAR, activa senales SHORT. Si es NEUTRAL, opera conservadoramente solo LONG.

---

## Como Funciona

### Las 3 Senales

#### 1. Estructura de Precio (40% del peso)

Analiza la tendencia de BTC usando medias moviles diarias:

- **Death Cross:** Cuando la SMA de 50 dias cruza por debajo de la SMA de 200 dias. Es la senal clasica de mercado bajista. Resta 40 puntos.
- **Precio debajo de SMA200:** Si BTC esta por debajo de su media de 200 dias, la tendencia macro es bajista. Resta 30 puntos.
- **Retorno de 30 dias:** Si BTC ha caido mas de 10% en el ultimo mes, resta 20 puntos. Si ha caido algo pero menos de 10%, resta 10.

Score de precio: empieza en 100 y se restan puntos por cada senal bajista.

#### 2. Sentimiento del Mercado (30% del peso)

Usa el **Fear & Greed Index** de alternative.me — un indice ampliamente reconocido que agrega:
- Actividad en redes sociales (Twitter, Reddit)
- Encuestas a traders
- Volatilidad del mercado
- Momentum de precios
- Dominancia de BTC
- Google Trends

Escala de 0 a 100:
- 0-24: Miedo extremo (la gente esta vendiendo en panico)
- 25-49: Miedo
- 50-74: Codicia
- 75-100: Codicia extrema (la gente compra sin pensar)

#### 3. Datos del Mercado de Futuros (30% del peso)

Usa el **Funding Rate** de Binance Futures — la tasa que pagan los traders de futuros:

- **Funding positivo:** Los que apuestan a que sube (longs) pagan a los shorts. Significa que hay mas optimismo → bullish.
- **Funding negativo:** Los shorts pagan a los longs. Significa que hay mas pesimismo → bearish.
- **Funding cerca de 0:** Equilibrio.

---

## La Formula

```
Score Compuesto = (Precio × 0.40) + (Sentimiento × 0.30) + (Funding × 0.30)

Si score > 70  →  BULL  (solo senales LONG)
Si score < 30  →  BEAR  (activa senales SHORT)
Si 30-70       →  NEUTRAL (solo LONG, conservador)
```

---

## Ejemplo Real (16 Abril 2026)

```
Precio:      Score 30/100
             Death Cross ACTIVO (SMA50 $69,835 < SMA200 $87,178)
             BTC $74,713 debajo de SMA200
             Pero retorno 30 dias +4.9% (recuperandose)

Sentimiento: Fear & Greed = 23 "Miedo Extremo"
             La gente tiene miedo, estan vendiendo
             Score: 23/100

Funding:     Rate = -0.003%
             Ligeramente bearish, casi neutro
             Score: 49/100

COMPUESTO:   30 × 0.40 + 23 × 0.30 + 49 × 0.30 = 33.6

RESULTADO:   NEUTRAL (entre 30 y 70)
             → El sistema opera solo LONG (conservador)
             → Si el score baja a <30, activaria SHORTs automaticamente
```

---

## Frecuencia de Actualizacion

El detector se ejecuta **una vez al dia** y guarda el resultado en cache por 24 horas. Esto significa:

- **3 llamadas API por dia** (no por ciclo de scan)
- El scanner lee el cache instantaneamente (0 milisegundos)
- Si el sistema se reinicia, recalcula automaticamente

Antes: 20 llamadas extra por ciclo × 288 ciclos/dia = **5,760 llamadas/dia**
Ahora: **3 llamadas/dia** — reduccion del 99.95%

---

## Cuando Activaria SHORT

El sistema activaria senales SHORT automaticamente cuando:

1. **Death Cross activo** (SMA50 < SMA200 daily) — la tendencia de mediano plazo confirma bear
2. **Precio debajo de SMA200** — el mercado esta estructuralmente bajista
3. **Fear & Greed < 25** — miedo extremo, la gente esta vendiendo
4. **Funding negativo** — los traders de futuros apuestan a la baja

Todas estas condiciones juntas producirian un score compuesto < 30, activando el modo BEAR.

---

## Notas Tecnicas

- **Archivo:** `btc_scanner.py` — funciones `detect_regime()` y `get_cached_regime()`
- **Cache:** Variable global `_regime_cache` con TTL de 24 horas
- **Proxy para todo crypto:** Solo analiza BTCUSDT (crypto tiene >90% correlacion con BTC)
- **Backtest:** Usa solo el componente de precio (no puede llamar APIs de sentimiento historicas)
- **Configurable:** Los pesos y umbrales se pueden ajustar en el codigo
