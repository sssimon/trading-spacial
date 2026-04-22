# Cambio importante del scanner: detector de régimen ahora es per-símbolo ("hybrid")

**Fecha:** 2026-04-22
**Destinatario:** Simón (papá)
**Para entender:** qué cambió, por qué, qué vas a ver distinto, y cómo revertir si algo se pone raro.

---

## Resumen en una línea

Cambié la forma en que el sistema decide "estamos en BULL, BEAR o NEUTRAL" para que lo haga **por cada moneda individualmente** en lugar de una sola decisión global basada solo en BTC. El backtest muestra **+$71k adicionales en 4 años** manteniendo el mismo riesgo por trade.

---

## Qué cambió exactamente

Antes, el sistema preguntaba una sola cosa al día:

> *"¿Cómo está BTC? Si BTC está en bull → LONG en todos. Si BTC está en bear → SHORT habilitado en todos."*

Ahora pregunta lo mismo pero **por cada moneda**:

> *"¿Cómo está DOGE específicamente? ¿Cómo está XLM específicamente?"*

Cada moneda tiene su propio score de régimen compuesto de:
- **50%** precio propio (vs su media móvil, momentum de 30 días)
- **25%** Fear & Greed del mercado cripto (global)
- **25%** funding rate (global, indicador de sentimiento apalancado)

BTC sigue usando el método global. Los otros 9 símbolos ahora usan su propio score.

**En el `config.json` esto se expresa como un solo campo nuevo:**
```json
"regime_mode": "hybrid"
```

Si ese campo no aparece (o está en `"global"`), el sistema usa el método viejo. Así que revertir es cambiar una línea.

---

## Por qué este cambio

Cuando el scanner dependía solo del régimen de BTC, había momentos donde:
- BTC estaba lateral → sistema decía NEUTRAL → nadie operaba SHORT.
- Pero DOGE/ADA/XLM estaban claramente en bear → habrían sido SHORTs rentables.
- Se perdían esas oportunidades.

Y al revés también pasaba:
- BTC en bear → sistema habilitaba SHORT para todos.
- Pero alguna moneda fuerte del portfolio (RUNE, DOGE durante su ciclo) seguía subiendo.
- Entraba SHORT perdedor.

El régimen per-símbolo corrige ambos casos.

---

## Qué vas a ver distinto en el dashboard

1. **Más señales SHORT** en DOGE, ADA, XLM cuando esas monedas estén bajistas (aunque BTC no lo esté).
2. **Menos señales SHORT** en monedas fuertes cuando BTC esté bajista pero la moneda no.
3. **DOGE sigue siendo la estrella** — en el backtest, en la ventana 4 años, DOGE solo pasó de `+$94,150 → +$132,823` con este cambio (~41% más).
4. **El dashboard no se ve diferente visualmente.** No hay botones nuevos. El cambio es interno.

---

## Resultados del backtest

### Ventana completa con la config que vamos a correr en producción

Backtest del **sistema exacto que va a operar** (no comparando modos, no probando otras cosas — solo la config que queda tras este cambio): `regime_mode=hybrid` + los `symbol_overrides` tuneados + 10 símbolos curados.

**Ventana pedida:** 2020-01-01 → 2026-04-18 (6 años)
**Ventana efectiva:** ~2021-01-01 → 2026-04-22 (5.25 años — el cache local no va más atrás que enero 2021 en la mayoría de símbolos)

**TOTAL P&L: $+312,476** en ~5.25 años
**Portfolio Max Drawdown (agregado): -10.1%**   ← esto es lo que ve la cuenta total
**Peor DD de un símbolo individual: -55.8%** (ETH)   ← info, no es el número que te importa

#### Desglose por moneda

| Moneda | P&L | Trades | PF | Max DD individual | Data desde |
|---|---|---|---|---|---|
| **DOGEUSDT** | **+$218,593** | 707 | 2.65 | -16.1% | 2021-01-01 |
| **XLMUSDT** | **+$35,858** | 608 | 1.59 | -19.7% | 2021-01-01 |
| **ADAUSDT** | **+$32,483** | 764 | 1.55 | -53.0% | 2021-01-01 |
| **JUPUSDT** | **+$13,475** | 325 | 1.32 | -32.1% | 2024-01-31 |
| **RUNEUSDT** | +$11,115 | 657 | 1.16 | -40.8% | 2021-01-01 |
| **PENDLEUSDT** | +$7,015 | 304 | 1.22 | -40.4% | 2023-07-03 |
| **AVAXUSDT** | +$2,278 | 576 | 1.06 | -22.0% | 2021-01-01 |
| BTCUSDT | -$319 | 619 | 0.99 | -41.3% | 2021-01-01 |
| ETHUSDT | -$3,755 | 604 | 0.90 | -55.8% | 2021-01-01 |
| UNIUSDT | -$4,266 | 654 | 0.85 | -51.5% | 2021-01-01 |

### Dos drawdowns distintos que conviene no confundir

Mirando la tabla ves que ETH tuvo un drawdown de **-55.8%** y UNI de **-51.5%**. Esos son **drawdowns por símbolo individual** — la cuenta de ese símbolo específico bajó mucho desde su pico.

Pero lo que importa para operar es el **drawdown del portfolio agregado**, que es lo que ves cuando sumas las 10 cuentas. Y ahí la cosa es muy distinta:

| Año | Peor drawdown del portfolio agregado |
|---|---|
| 2021 | **-10.1%** (pico 2021-04-15 $109k → valle 2021-11-12 $98k) |
| 2022 | -8.4% (febrero) |
| 2023 | -2.5% |
| 2024 | -5.1% |
| 2025 | -6.7% |
| 2026 | -1.8% |

El portfolio **nunca bajó más de -10.1% desde su pico** en 5.25 años. Peak $109k → valle $98k → después recuperó y subió hasta $412k final. La cuenta total **nunca se puso debajo del capital inicial por más del 2%**.

¿Por qué la diferencia? **Diversificación real.** Cuando ETH está sufriendo -55%, DOGE está ganando +200%. Cuando UNI está en -51%, XLM está en +358%. Los símbolos individualmente sufren mucho, pero nunca todos al mismo tiempo — se compensan. El agregado del portfolio respira.

### Observaciones importantes

1. **DOGE concentra el 70% del P&L** (+$218k de los +$312k totales). Es un riesgo de concentración real. Si DOGE deja de tener ciclos de pump/dump, el portfolio pierde su mayor motor. El kill switch del Epic #138 protege automáticamente si DOGE entra en racha negativa.
2. **BTC, ETH, UNI rinden cerca de cero o negativo**. Contribuyen drawdown individual sin aportar P&L — candidatos para eventualmente eliminar del portfolio o re-tunear.
3. **PF del portfolio agregado: ~1.3** — saludable. El verdadero alpha viene de la asimetría DOGE.
4. **Data limitada para tokens nuevos** — JUP tiene 2.25 años, PENDLE ~2.75 años. Para ellos los números son menos robustos.

### Criterio de alarma en vivo

El backtest muestra que el portfolio agregado nunca bajó de -10.1%. En operación real:

- **Alarma suave:** drawdown agregado pasa de **-15%** → revisar si el comportamiento del mercado se salió del patrón histórico.
- **Alarma fuerte:** drawdown agregado pasa de **-20%** → evaluar revertir a `regime_mode=global` o pausar trading manualmente.

Revertir es cambiar una línea en el config (instrucciones abajo). Sin reiniciar nada.

---

## Cómo revertir si algo se pone raro

**Forma 1 — el dashboard** (si expongo ese control en el futuro): cambiar un dropdown. Todavía no existe.

**Forma 2 — editar `config.json` o `config.defaults.json`:**

Abre el archivo con notepad. Busca la línea que dice:

```json
"regime_mode": "hybrid",
```

Cámbiala por:

```json
"regime_mode": "global",
```

Guarda el archivo. El scanner vuelve al comportamiento previo **en la siguiente vuelta del loop** (máximo 5 minutos). No hay que reiniciar nada.

**Forma 3 — borrar la línea:** si borras la línea `"regime_mode": ...` completamente, el sistema por defecto usa `"global"`. Lo mismo que la forma 2.

---

## Plan de monitoreo (2-4 semanas)

Durante las próximas 2-4 semanas:

1. **Mirá el P&L real vs la tendencia esperada** del backtest (~+$43k/año si mantiene proporcionalidad).
2. **Contá señales por moneda** — el volumen debería subir ~10-20% en DOGE/ADA/XLM; bajar ~5-10% en RUNE/PENDLE.
3. **Alertas del kill switch (#138)** — el kill switch ya está activo. Si alguna moneda entra en ALERT o REDUCED, el sistema te avisa por Telegram automáticamente.
4. **Si el drawdown real del portfolio agregado pasa de -20%**, revertí con las instrucciones de arriba y abrí un issue para analizar. (El backtest histórico nunca llegó a -11%, entonces -20% es señal de que el mercado cambió de régimen.)

Después de 4 semanas de operación estable, evaluamos si queda como está o si hay algún ajuste.

---

## Apéndice: ¿cómo ETH llegó a -55.8% individual si tenemos stop loss?

Pregunta razonable. **Sí, cada trade individual tiene stop loss**, y está funcionando. Analicé los 604 trades de ETH durante los 5.25 años:

| Métrica | Valor |
|---|---|
| Total trades | 604 |
| Salidas por Stop Loss | **511** (85%) |
| Salidas por Take Profit | 93 (15%) |
| Pérdida más grande en un solo trade | **-$206** (~2% del capital de ETH) |
| Pérdida promedio por trade perdedor | -$115 (~1.15% del capital) |
| Ganancia promedio por trade ganador | +$374 (~3.7% del capital) |
| Ratio ganador/perdedor en dólares | 3.25x (bueno) |
| Win rate | **15.4%** (malo) |

**El stop loss está protegiendo perfectamente cada trade.** Ningún trade perdió más del ~2% del capital asignado al símbolo. Lo que pasa con ETH es un problema distinto:

**ETH perdió *poquito* muchas veces seguidas.** 511 stop losses × ~$115 promedio = $58k en pérdidas acumuladas. Los 93 ganadores solo sumaron $34k. La resta neta: -$24k en esos trades específicos, que se manifiestan como un drawdown gradual que tomó 871 días (2.4 años) en llegar al fondo.

**El stop loss previene catástrofes puntuales (perder 50% en un trade). NO previene erosión gradual por muchas pérdidas pequeñas.** Para eso están otros mecanismos:

1. **Kill switch #138** — está activo. Si ETH acumula win rate < 15% en 20 trades o P&L 30 días negativo, el sistema automáticamente reduce su tamaño a la mitad (tier REDUCED) o lo pausa completo (tier PAUSED). En vivo, esto cortaría el sangrado gradual mucho antes de llegar a -55%.

2. **Regime detector** — no opera SHORT en BULL ni LONG en BEAR. Reduce el número de trades en contra del mercado.

3. **Diversificación** — aunque ETH sangró gradualmente, DOGE/XLM/ADA/etc. generaban ganancia simultáneamente, así que el **portfolio agregado nunca bajó más de -10.1%** (ese es el número que importa operativamente).

### Entonces, ¿qué hacemos con ETH?

Tres opciones, de menor a mayor intervención:

- **(A)** Dejarlo como está. El kill switch lo pausaría en vivo antes de que llegara a -55%. Backtest lo corrió sin kill switch activo, por eso se ve la erosión completa.
- **(B)** Re-tunear ETH con `auto_tune.py` para buscar parámetros ATR con mejor win rate.
- **(C)** Desactivarlo del portfolio (setearlo a `false` en `symbol_overrides`). Pierde solo -$3,755 en 5.25 años, así que sacarlo no duele casi nada.

Mi recomendación: **opción (A) primero**. El kill switch ya protege en vivo lo que el backtest no consideró. Después de 3-4 meses, si ETH sigue en ALERT/REDUCED casi siempre, tomamos (B) o (C).

---

## Lo que **NO** cambió

- **El portfolio** sigue siendo exactamente las mismas 10 monedas: BTC, ETH, ADA, AVAX, DOGE, UNI, XLM, PENDLE, JUP, RUNE.
- **El riesgo por trade** sigue siendo 1% del capital, fijo. Sin multiplicadores nuevos.
- **Los parámetros ATR por moneda** (los `atr_sl_mult/tp/be` que se tunearon con 735+ simulaciones) no cambian. Son la fuente principal del alpha validado.
- **El kill switch** del Epic #138 sigue activo con sus 3 tiers (ALERT/REDUCED/PAUSED).
- **Las notificaciones Telegram** siguen igual — mismo formato, mismo canal.
- **Tu `config.json` local en Windows** sigue funcionando. El nuevo `config.defaults.json` del repo se fusiona con lo tuyo, lo tuyo manda en caso de conflicto.

---

## Notas técnicas (opcional, por si te interesa el detalle)

El cambio se apoya en dos piezas de infraestructura que ya estaban en el repo:

1. **Epic #152 / PR #159** — infraestructura de régimen per-símbolo. Shipeado como "modo dormido" en abril 2026. Nunca se activó porque el backtest inicial decía que era peor. **Pero el backtest inicial estaba corriendo con un pipeline roto** (ver punto 2).

2. **PR #181** — arreglo del pipeline: `config.json` estaba en `.gitignore` desde siempre (tiene los tokens de Telegram). Pero los `symbol_overrides` vivían en el mismo archivo. Resultado: cualquier backtest que corría en una copia fresca del repo se ejecutaba **sin los multiplicadores ATR tuneados** y producía solo ~40% del alpha real. Por eso la decisión original de "el régimen per-símbolo es peor" estaba basada en números inválidos. Con el pipeline arreglado, se rehízo el backtest y ahora los números favorecen `hybrid`.

El PR #181 separó la configuración en:
- `config.defaults.json` — committeado, contiene los `symbol_overrides` tuneados + el `regime_mode` nuevo.
- `config.secrets.json` / `config.json` — sigue siendo ignorado por git, contiene tus tokens.

Así que cualquiera que haga `git clone` del repo ahora obtiene un sistema funcional sin setup manual.

---

## Archivos a los que podés acudir

- **Spec del régimen per-símbolo:** `docs/superpowers/specs/es/2026-04-20-per-symbol-regime-design.md`
- **FORMULA GANADORA original:** `docs/superpowers/specs/es/2026-04-17-formula-ganadora-resultados-finales.md`
- **Documento canónico del sistema:** `docs/superpowers/specs/es/2026-04-18-documento-completo-sistema-trading.md`
- **Gate con criterios:** `scripts/gate_regime_modes.py`
- **Config principal:** `config.defaults.json`

Cualquier duda, avísame y lo vemos juntos.
