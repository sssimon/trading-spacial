# Exit Logic Benchmark — Crypto Trading Frameworks

**Fecha:** 2026-04-30
**Branch:** `research/exit-logic-benchmark-281`
**Issue:** [#282](https://github.com/sssimon/trading-spacial/issues/282)
**Bloqueante de:** A.4 (#250) decisión de path — exit logic redesign vs widening de ATR multipliers
**Output:** este doc + comment en #282
**Status:** ready for review (martes 2026-05-04 AM)
**Time-bounded:** ~4h research total. No bloqueante de A.0.2 PR (#277) ni A.0.3 (#278).

---

## 0 · Framing y restricciones

Este doc es un **panorama de opciones**, no una recomendación cerrada. La decisión final sobre qué pattern adoptar en A.4 sale de cruzar este benchmark con el contexto específico del sistema (scoring multi-timeframe, h=+5h horizonte predictivo, basket de 10 símbolos, R-multiple sizing, capital constraint, holdout fijo).

**Premisa heredada del diagnóstico A.0.2.diag (#281):**

- Exit logic actual = ATR-based: `SL = entry ± atr·atr_sl_mult`, `TP = entry ± atr·atr_tp_mult`, `BE = mover SL a entry cuando precio mueve atr·atr_be_mult favorable`. Sin trailing post-BE, sin time-stop. Lógica concreta: [`_close_position` en `backtest.py:266-322`](../../../backtest.py) + evaluación bar-a-bar SL/TP/BE en [`backtest.py:323-657`](../../../backtest.py).
- En PENDLE/AVAX/ADA, **fixed-horizon h=+5h captura `+0.46–0.55%` gross per-trade** vs `~0%` del ATR-based (medición real, train 18m). El exit logic ATR está destruyendo ~100% de la edge predictiva, *antes* de aplicar costos. Ver §2 + §6 de [`docs/superpowers/specs/es/2026-04-30-a02-diag-deep-dive.md`](../specs/es/2026-04-30-a02-diag-deep-dive.md) y la adenda en [#281](https://github.com/sssimon/trading-spacial/issues/281#issuecomment-4353692842).
- **Fixed-horizon h=+5 simple es baseline validado en train.** El research busca variantes *superiores* a fixed-horizon, no si fixed-horizon vale la pena (eso ya se demostró).
- **Análisis #8 (robustez temporal, dentro de [#281](https://github.com/sssimon/trading-spacial/issues/281)) quedó inconclusivo.** No está cerrado si la edge en train es real o artifact. Cualquier pattern que "casualmente coincida con la estructura del overrides previo" hereda el mismo riesgo de leakage que motivó re-tune de A.4. Aclaración: este "Análisis #8" es el §8 del thread de #281, **no** el GH issue #8 (que es otro tema, cerrado).
- **Nota AVAX:** este doc se refiere a AVAX como parte del cluster con edge predictiva porque la medición original del fixed-horizon h=+5 lo trataba como tal. La reclasificación a Mundo C local — basada en el SL widening test del [Análisis #3 del diagnóstico](../specs/es/2026-04-30-a02-diag-deep-dive.md) (rescate ≤6.6%) — vive en el [pivot plan §1.2](../plans/2026-05-01-a4-strategic-pivot-plan.md). El benchmark **no la pre-asume**; futuras decisiones operacionales sobre AVAX deben referirse al pivot plan + parameter study, no a este doc.

**Restricciones explícitas observadas en este research:**

- Sin código de producción modificado.
- Sin backtests corridos para "verificar" patterns. Las validaciones empíricas se delegan a A.4.
- Patterns sin evidencia clara → etiqueta `promising, needs validation`.
- Claims sin link → descartados antes de tabular.
- Definiciones ambiguas → surface en §"Decisions to surface", no se cierran unilateralmente.

---

## 1 · Tabla comparativa

Filas = frameworks. Columnas = primitivas de exit, marcadas como:
- ✅ `nativo`: primer-class, parameterizable directamente.
- 🟡 `idiomatic`: no nativo, pero patrón estandarizado en docs/templates oficiales.
- ➕ `extensible`: hook documentado para implementarlo con código de usuario.
- ❌ `not found`: no documentado / no encontrado en el time-budget.

| Framework | Static SL/TP (%) | ATR-stop (volatility-aware) | Trailing stop | Time / horizon stop | Signal-decay exit | Triple-barrier | Notes |
|---|---|---|---|---|---|---|---|
| **Freqtrade** | ✅ `stoploss` + `minimal_roi` | ➕ vía `custom_stoploss` | ✅ nativo (% offset, no ATR) | ✅ `minimal_roi: {N: -1}` o `custom_exit` | 🟡 `custom_exit` con `current_profit < threshold` | 🟡 combinable: SL + ROI(time) + custom_exit | ROI-table = TP que decae con tiempo. Templates oficiales usan ROI-decay + static SL. |
| **Hummingbot v2** | ✅ `TripleBarrierConfig` | ➕ controller pasa `Decimal` arbitrario | ✅ nativo (`TrailingStop(activation, delta)`) | ✅ `time_limit: int` (segundos) | ➕ via Controller logic | ✅ **default executor** | Único framework retail donde Triple-Barrier es la opción canónica, no un pattern. |
| **Jesse** | ✅ `self.stop_loss/take_profit` | 🟡 ATR helper en `update_position` | ➕ DIY en `update_position` | ➕ DIY (track entry index) | ➕ DIY en `update_position` | ➕ DIY | Filosofía: solo SL/TP estáticos como datos; todo lo dinámico es código de usuario. |
| **Backtrader** | ✅ bracket orders | ➕ ATR indicator + custom Stop en `next()` | ✅ `Order.StopTrail` (% / abs, sin ATR) | 🟡 idiom `bar_executed + N` en quickstart | ➕ via `next()` close-on-signal | ➕ DIY | Sin chandelier nativo. ATR-trail es pattern comunitario. |
| **NautilusTrader** | ✅ `OrderList` + `ContingencyType.OCO/OTO` | ➕ `Strategy.modify_order` por bar | ✅ `TrailingStopMarket/Limit` (PRICE/BPS/TICKS, sin ATR) | ✅ `clock.set_time_alert` + `close_position` | ➕ via `on_bar` | ➕ via OCO + time alert | Quant-grade event-driven. ATR-trail no nativo. |
| **vectorbt (OSS)** | ✅ `sl_stop`, `tp_stop` (Decimal %) | ➕ `adjust_sl_func_nb` Numba callback | ✅ `sl_trail=True` (anchor en HWM) | ❌ **`td_stop` no existe en OSS** — se construye boolean exits array | ✅ `Portfolio.from_signals(exits=...)` first-class | 🟡 combinable vía exits + sl/tp | "Stop signal has priority" sobre signal exit. `td_stop`/`dt_stop` son **vectorbtpro paid**. |
| **OctoBot** | ✅ `stop_loss_offset`, `take_profit_offset` | ❓ time-budget | ❓ time-budget | ❓ time-budget | 🟡 evaluator-driven exit | ❓ time-budget | Cobertura limitada por 403 al loadear docs (ver §2.7); ❓ = no encontrado en time-budget, no asserción de ausencia. |

**Observación cruzada de la tabla:**
- **Tres frameworks tienen time-stop nativo de primer nivel**: Freqtrade (`minimal_roi: -1`), Hummingbot (`time_limit`), NautilusTrader (`set_time_alert`). vectorbt OSS lo soporta con un boolean array DIY.
- **Solo Hummingbot v2** trata Triple-Barrier (SL + TP + time-limit + trailing) como la **default option** y no como un pattern opcional.
- **Ningún framework tiene ATR-trail nativo** (ni siquiera NautilusTrader): ATR siempre es código de usuario que computa la distancia y modifica el order.
- **vectorbt** es único en darle prioridad explícita a los stop signals sobre signal-based exits ("stop signal has priority"), lo que es exactamente la patología que vimos en nuestro sistema.

---

## 2 · Sección por framework

### 2.1 Freqtrade

Stack: Python, ~28k stars, retail-focused, gran comunidad cripto.

**Exit primitives:**
- `stoploss` (float estático, mandatorio): floor que `custom_stoploss` no puede violar a la baja. Docs: <https://www.freqtrade.io/en/stable/stoploss/>.
- `minimal_roi` (dict `{minutes_since_open: min_profit_ratio}`): TP que **decae con el tiempo**. Docs: <https://www.freqtrade.io/en/stable/strategy-customization/#minimal-roi> y <https://www.freqtrade.io/en/stable/configuration/#understand-minimal_roi>.
- `custom_stoploss` (callback per-iteration): puede mover SL solo en dirección favorable (ratchet). Docs: <https://www.freqtrade.io/en/stable/strategy-callbacks/#custom-stoploss>.
- `custom_exit` (callback per-iteration): exit ad-hoc por trade-state. Docs: <https://www.freqtrade.io/en/stable/strategy-callbacks/#custom-exit-signal>.
- `trailing_stop` + `trailing_stop_positive` + `trailing_stop_positive_offset`: trailing porcentual con activación opcional por offset. Docs: <https://www.freqtrade.io/en/stable/stoploss/#trailing-stop-loss>.
- Helpers: `stoploss_from_open`, `stoploss_from_absolute` para convertir target absoluto al % relativo que `custom_stoploss` requiere. Docs: <https://www.freqtrade.io/en/stable/strategy-callbacks/#stoploss-helper-functions>.

**ROI table = time-decay TP:** dict keyed by minutos desde `trade.open_date`, valor = ratio mínimo para exit. Bot pickea el largest key ≤ elapsed_minutes y exitea cuando profit ≥ ese ratio. Caso especial documentado: `{"<N>": -1}` fuerza exit después de N minutos *no matter qué* — es el **time-stop forzado canónico** del framework.
> "A special case presents using `'<N>': -1` as ROI. This forces the bot to exit a trade after N Minutes, no matter if it's positive or negative, so represents a time-limited force-exit." — [Freqtrade docs, Understand minimal_roi](https://www.freqtrade.io/en/stable/configuration/#understand-minimal_roi).

**Time-based exit idiomático para "close after N candles":**
```python
timeframe_mins = timeframe_to_minutes(timeframe)
minimal_roi = {"0": 0.05, str(timeframe_mins * 3): 0.02, str(timeframe_mins * 6): 0.01, str(timeframe_mins * 10): -1}
```
Docs: <https://www.freqtrade.io/en/stable/strategy-customization/#using-calculations-in-minimal-roi>.

**Templates oficiales:** [`sample_strategy.py`](https://github.com/freqtrade/freqtrade/blob/develop/freqtrade/templates/sample_strategy.py) usa `minimal_roi` (time-decaying TP) + `stoploss` estático + `use_exit_signal=True`. Trailing está deshabilitado por default. Comunidad: [`freqtrade-strategies/Strategy001_custom_exit.py`](https://github.com/freqtrade/freqtrade-strategies/blob/main/user_data/strategies/Strategy001_custom_exit.py), `Supertrend.py`, `BreakEven.py`, `FixedRiskRewardLoss.py`, `CustomStoplossWithPSAR.py`.

**Tradeoffs documentados:**
- Docs explícitamente recomiendan `custom_stoploss` sobre `custom_exit` para stop-style exits — preserva la opción de `stoploss_on_exchange`. <https://www.freqtrade.io/en/stable/strategy-callbacks/#custom-exit-signal>.
- Trailing + custom_stoploss puede entrar en conflicto: docs recomiendan deshabilitar trailing si hay custom_stoploss. <https://www.freqtrade.io/en/stable/strategy-callbacks/#custom-stoploss>.
- Rate-based `custom_exit` es **inaccurate en backtesting** (vs vector-based exit). Docs: <https://www.freqtrade.io/en/stable/strategy-callbacks/>.

**Para nuestro caso:** Freqtrade es el framework **más cercano a un "fixed-horizon con TP que decae" canónico**. Si quisiéramos prototipar un horizon=5 con TP-decay, sería literalmente `minimal_roi = {"0": 0.04, "150": 0.01, "300": -1}` (5 candles 1H ≈ 300 min).

### 2.2 Hummingbot v2 (Strategies V2)

Stack: Python + Cython, ~9k stars, originalmente market-making, Strategies V2 abre directional/momentum.

**Exit primitives — Triple-Barrier es la default:**

Hummingbot v2 expone `PositionExecutor` con `TripleBarrierConfig`, donde **los cuatro exit tipos son first-class** y co-existen:

```python
@dataclass
class TripleBarrierConfig:
    stop_loss: Optional[Decimal]   # net PnL fraction
    take_profit: Optional[Decimal] # net PnL fraction
    time_limit: Optional[int]      # seconds since fill
    trailing_stop: Optional[TrailingStop]  # activation_price + trailing_delta
    # + per-barrier order types (LIMIT vs MARKET)
```

Source: [`data_types.py`](https://github.com/hummingbot/hummingbot/blob/master/hummingbot/strategy_v2/executors/position_executor/data_types.py) (canonical — verified live, matches the 4 fields above), [`position_executor.py`](https://github.com/hummingbot/hummingbot/blob/master/hummingbot/strategy_v2/executors/position_executor/position_executor.py).

`close_type` enum: `STOP_LOSS | TAKE_PROFIT | TIME_LIMIT | TRAILING_STOP` — atribución first-class para diagnóstico.

**Inspiración explícita:** los docs citan a López de Prado *Advances in Financial Machine Learning* — Triple Barrier es la **default exit philosophy**, no opcional.

**Ejemplo canónico de momentum:** [Directional MACD-BB](https://hummingbot.org/blog/directional-trading-with-macd-and-bollinger-bands/) usa Triple Barrier dinámica donde TP/SL salen del rolling stdev de 100 bars (×1.5 TP, ×0.75 SL) con `time_limit = 55 min` — exits escalan con volatilidad, no con un % fijo.

**Para nuestro caso:** Hummingbot v2 es el **único framework retail donde la combinación SL_ATR + TP_ATR + time_limit_5h es trivial de configurar**. Es la implementación retail más cercana a triple-barrier de López de Prado. Si fuéramos a portar el sistema, esta sería la arquitectura natural.

### 2.3 Jesse

Stack: Python, ~6k stars, crypto-native, opinionado en backtesting/live unificado.

**Exit primitives:** `self.stop_loss = (qty, price)`, `self.take_profit = (qty, price)` (o lista de tuplas para staged exits), `self.liquidate()`, `update_position()` (callback per-bar). Docs: [entering-and-exiting trades](https://docs.jesse.trade/docs/strategies/entering-and-exiting.html). Lifecycle hooks: `on_increased_position`, `on_route_open_position`, `on_stop_loss`. Source: [`Strategy.py`](https://github.com/jesse-ai/jesse/blob/master/jesse/strategies/Strategy.py).

**Filosofía:** Jesse expone solo SL/TP estáticos como datos; todo lo dinámico (trailing, BE, time-stop, signal-decay) vive imperativamente en `update_position()`. **No hay framework de exit opinionado** — no hay equivalente Triple-Barrier nativo.

**Trailing stop / break-even:** **NO built-in.** Oficialmente DIY:
> "Does Jesse support trailing stop-loss or some kind of break-even functionality?" — la respuesta del FAQ es: implementarlo dentro de `update_position()`. — [Jesse FAQ](https://jesse.trade/help/faq/does-jesse-support-trailing-stop-loss-or-some-kind-of-break-even-functionality).

**Time-stop:** No primitive. Pattern: trackeá `entry_index` en `go_long()`, comparalo en `update_position()`, llamá `liquidate()`.

**Template momentum (TurtleRules):** SL = `entry - atr_mult * atr` en `go_long()`; exit primary es **signal-based via `update_position()`** (`if entry_signal == "entry_short": liquidate()`). Sin TP fijo, sin trailing, sin time-stop. Source: [`example-strategies/TurtleRules`](https://github.com/jesse-ai/example-strategies/blob/master/TurtleRules/__init__.py).

**Blog oficial:** ["4 practical methods to set your stop-loss when algo trading Bitcoin"](https://jesse.trade/blog/tutorials/4-practical-methods-to-set-your-stop-loss-when-algo-trading-bitcoin) enumera (1) %-fijo, (2) swing high/low, (3) ATR-multiple, (4) indicator-derived (Supertrend) — sin recomendar uno explícitamente.

**Para nuestro caso:** un port a Jesse del sistema actual sería 1:1 dentro de `update_position()`. No hay scaffolding adicional que aporte. Más útil como referencia de **filosofía** ("exits son strategy-author concern") que como pattern.

### 2.4 Backtrader (+ `bt`)

Stack: Python, ~14k stars, multi-asset (no crypto-specific), event-driven.

**Exit primitives:**
- ExecTypes nativos: `Market, Close, Limit, Stop, StopLimit, StopTrail, StopTrailLimit, Historical`. Source: [`order.py`](https://github.com/mementum/backtrader/blob/master/backtrader/order.py). **Ninguno es volatility-aware nativamente.**
- `Order.StopTrail` / `StopTrailLimit`: dos modos solamente — `trailamount` (precio absoluto) y `trailpercent` (%). **Sin ATR-multiple nativo.** Docs: <https://www.backtrader.com/docu/order-creation-execution/stoptrail/stoptrail/>.
- Bracket orders: `buy_bracket(limitprice=..., price=..., stopprice=...)` con OCO entre children. Docs: <https://www.backtrader.com/docu/order-creation-execution/bracket/bracket/>.
- `bt.indicators.AverageTrueRange`: ATR como indicador, **no como stop type**. Usuario computa nivel y submite `Stop` regular.
- Time-based exit idiom (Quickstart oficial): track `self.bar_executed = len(self)` en `notify_order`, en `next()` `if len(self) >= self.bar_executed + N: self.close()`. Docs: <https://www.backtrader.com/docu/quickstart/quickstart/>.

**Sin chandelier nativo, sin BE-move automático.** Bracket orders solo soportan precios fijos al submit; mover SL a entry requiere `cancel()` + resubmit en `next()`.

**`bt` (pmorissette/bt):** framework de **portfolio rebalancing**, no trading event-driven. Algos son scheduling (`RunDaily`, `RunIfOutOfBounds`) y weighting (`WeighEqually`, `WeighInvVol`, `TargetVol`). **Sin SL/TP/trailing primitives** — exits implícitos en rebalanceo. Source: [`bt/algos.py`](https://github.com/pmorissette/bt/blob/master/bt/algos.py). Poco relevante para nuestro caso de signals direccionales por símbolo.

**Para nuestro caso:** Backtrader confirma que **time-stop por bar count es el idiom canónico** en frameworks event-driven (no inventamos nada). Pero no aporta primitives nuevos.

### 2.5 NautilusTrader

Stack: Python + Rust core, ~4k stars, event-driven, low-latency, backtest/live unificado, quant-grade.

**Exit primitives:**
- `StopMarketOrder`, `StopLimitOrder` con `TriggerType` (`DEFAULT, BID_ASK, LAST_PRICE, DOUBLE_BID_ASK, MARK_PRICE, INDEX_PRICE`).
- `TrailingStopMarketOrder`, `TrailingStopLimitOrder` con `trailing_offset` + `TrailingOffsetType` enum: `NO_TRAILING_OFFSET, PRICE, BASIS_POINTS, TICKS, PRICE_TIER`. **Sin `ATR` enum value.** Source: [`enums.py`](https://github.com/nautechsystems/nautilus_trader/blob/master/nautilus_trader/model/enums.py).
- `OrderList` + `ContingencyType` (`OTO, OCO, OUO`) para brackets parent-child.
- Time-based exits: `self.clock.set_time_alert(...)` dispatch a `TimeEvent` en `on_event` → strategy emite `cancel_order` / `close_position`. Docs: <https://nautilustrader.io/docs/latest/concepts/strategies/>.
- ATR-trailing es DIY: subclass strategy, computar ATR en `on_bar`, `modify_order(new_trigger_price=...)` por bar. Sin enum value `ATR` en el Rust core.

**Para nuestro caso:** NautilusTrader es el framework **más quant-grade** de los retail-friendly y el único con multi-trigger-type a nivel exchange. Pero no aporta exit logic *conceptualmente nuevo* vs Hummingbot v2 — ambos cubren bracket + trailing + time. La diferencia es de plumbing/latencia, no de policy.

### 2.6 vectorbt (OSS)

Stack: Python + Numba, ~5k stars, vectorizado masivo, paradigma de batch backtesting.

**Exit primitives confirmados en OSS (free):**
- `Portfolio.from_signals(close, entries, exits, sl_stop=, sl_trail=, tp_stop=, ...)`. Source: [`base.py`](https://github.com/polakowo/vectorbt/blob/master/vectorbt/portfolio/base.py). Docs: <https://vectorbt.dev/api/portfolio/base/#vectorbt.portfolio.base.Portfolio.from_signals>.
- `sl_stop`, `tp_stop`: % de acquisition price. `sl_trail=True` convierte SL en trailing (anchor en HWM long / LWM short).
- `OHLCSTX` Stop Exit Indicator: produce `(exits, stop_price, stop_type)` arrays. Tipos: `StopLoss=0, TrailStop=1, TakeProfit=2`. Source: [`generators.py`](https://github.com/polakowo/vectorbt/blob/master/vectorbt/signals/generators.py).
- Hook de extensión: `adjust_sl_func_nb` Numba callback, recibe `AdjustSLContext`, retorna `(new_stop_value, trailing_flag)`. **Es la ruta canónica para implementar ATR-trailing en vectorbt OSS.**
- Signal-based exit: `entries`/`exits` boolean arrays, **first-class**. `Portfolio.from_signals` los acepta directamente.

**`td_stop` y `dt_stop` NO existen en vectorbt OSS.** Verificado:
- `vectorbt/portfolio/base.py` master branch: solo `sl_stop`, `sl_trail`, `tp_stop`.
- `StopType` enum tiene exactamente 3 miembros (`StopLoss, TrailStop, TakeProfit`). Source: [`enums.py`](https://github.com/polakowo/vectorbt/blob/master/vectorbt/signals/enums.py).
- `td_stop`/`dt_stop` son features de **vectorbtpro paid** (<https://vectorbt.pro/tutorials/stop-signals/>), API no indexada públicamente.

**Time-stop en vectorbt OSS:** patrón canónico = construir un `exits` boolean array que sea `True` en `entry_idx + N` (e.g. via `signals.vbt.fshift(N)` o un Numba routine custom) y pasarlo a `from_signals`. **No hay primitive nativo.**

**Semántica crítica:** "Stop signal has priority" — un SL/TP setup tight preempte un signal-based exit en la misma bar. **Esto es exactamente el patrón que destruye edge en nuestro sistema** — es la versión vectorbt del finding del [addendum del operador a #281](https://github.com/sssimon/trading-spacial/issues/281#issuecomment-4353692842) (timer fijo h=+5 captura `+0.46–0.55%` vs ATR ~0%).

**Para nuestro caso:** vectorbt es el framework **más eficiente para barrer un grid de patterns alternativos**. Si A.4 quisiera comparar h=+5 vs ATR-only vs h=+5+ATR híbrido vs signal-decay, vectorbt podría hacerlo en minutos sobre el mismo dataset. Pero el time-stop nativo no existe en OSS — habría que construir el array manualmente.

### 2.7 OctoBot

Stack: Python, ~3k stars, retail-focused, modes: DCA, Daily, TradingView signals.

Investigación limitada por error 403 en docs (web fetch). [WebSearch summary](https://www.octobot.cloud/en/guides/octobot-trading-modes/trading-modes) muestra:
- Orders soportan `stop_loss_offset` y `take_profit_offset` (offsets porcentuales).
- "Take profit and stop loss orders pueden split en múltiples exit orders con diferentes precios."
- Modes principales: DCA, Daily, TradingView (signal-driven externo).

Sin documentación accesible de trailing-stop, ATR-stop, ni time-stop nativo en este time-budget. **Cobertura limitada — flag para revisión si OctoBot es prioridad.** Aporte marginal vs frameworks anteriores.

---

## 3 · Patterns que nuestro sistema NO implementa pero podrían atacar el finding del addendum a #281

Los siguientes patterns son **candidatos para A.4 prototyping**. Cada uno se evalúa contra la edge predictiva conocida (h=+5h, gross +0.46–0.55% en PENDLE/AVAX/ADA — *ver nota AVAX en §0*) y el contexto del sistema (10 símbolos, R-multiple sizing, capital constraint, holdout intacto).

Tratamos **fixed-horizon h=+5 simple** como baseline validado; los siguientes son **variantes que podrían superarlo**.

### 3.1 — Triple Barrier puro (ATR-SL + ATR-TP + time-limit at h=+5)

**Definición:** Mantener SL+TP basados en ATR pero **agregar un time-limit hard a t+5h**, cerrando lo que sea que esté abierto al hit del timer. Implementación trivial en Hummingbot v2 (`time_limit=18000` segundos) o como `minimal_roi: {"300": -1}` en Freqtrade.

**Pros para nuestro contexto:**
- Mínimo cambio estructural sobre el código actual: un `if duration >= 5h: close()` adicional en el loop de `backtest._close_position`.
- Preserva el R-multiple sizing y la fixed-1% risk policy (#121) sin modificación.
- Captura la edge en h=+5 sin abandonar la asimetría SL-TP.
- Atribución natural en `exit_reason`: agrega un cuarto bucket (`TIME_LIMIT`) que permite diagnóstico per-trade ("¿el time-limit cerró un winner que iba a hit TP, o cerró un loser que iba a hit SL?").

**Cons / riesgos:**
- Si la edge real está en h=+10h ó h=+15h (no medido en train por falta de tiempo en #281 §6), un time-limit a h=+5 podría dejar dinero en la mesa.
- **Time-limit global a t+5h activamente daña BTC/ETH** — winners hold 14h (#6 ratio 3.5x / 2.8x). Un t+5h global no es "mitigable", es **incompatible** con la heterogeneidad observada del basket. Ver §5 Gate 2 para el decoupling per-cluster (que es a su vez una hipótesis, no una asunción).
- **Time-limit determinista clusterea exits en momentos fijos** (e.g., overnight UTC, sesiones de bajo volumen). Si la liquidity-by-hour-of-day correlaciona con el bucket de exits TIME_LIMIT, la atribución por `close_type` se ensucia: el bucket TIME_LIMIT podría tener spread implícito sistemáticamente más ancho que SL/TP. Worth medir en A.4 cruce de exit_reason × hour-of-day × cost.
- Hereda riesgo de leakage si los ATR multipliers actuales fueron tuned con la holdout incluida (caveat #1 de [holdout provenance](../specs/es/2026-04-30-a1-holdout-dataset-provenance.md)).

**Etiqueta:** **strong candidate, validation needed in A.4 — pero con basket-decoupling tratado como hipótesis y cost-survival check pre-registrado (ver §5).**

**Citas relevantes:**
- López de Prado, *Advances in Financial Machine Learning*, Ch. 3 (Triple Barrier Method): <https://github.com/hudson-and-thames/mlfinlab>.
- Hummingbot v2 implementación canónica: [`TripleBarrierConfig`](https://github.com/hummingbot/hummingbot/blob/master/hummingbot/strategy_v2/executors/position_executor/data_types.py).
- Freqtrade time-stop forzado: <https://www.freqtrade.io/en/stable/configuration/#understand-minimal_roi>.

### 3.2 — Time-decaying TP (Freqtrade ROI table-style)

**Definición:** SL static (puede mantener atr_sl_mult). TP decae con el tiempo:
```
t=0: TP = +5.0%
t=2h: TP = +3.0%
t=4h: TP = +1.5%
t=5h: TP = 0.0% (cualquier ganancia exitea)
t=6h: TP = -1.0% (cierre forzado)
```

**Pros para nuestro contexto:**
- **Muy aligned con el patrón empírico observado**: la edge predictiva se concentra en h=+5h y decae después. Una TP que se relaja hasta -1% post-h=+5 captura naturalmente el signal-decay sin necesidad de un time-stop binario.
- Es el pattern *default* de Freqtrade — el más probado en cripto retail.
- Captura un winner que **acaba de entrar pero retrocedió**: a t=+1h con +0.5% no exitea (TP=4.5% lejos), a t=+5h con +0.5% sí exitea.

**Cons / riesgos:**
- Más parametrizable que fixed-horizon → **más superficie de overfit**. Cada paso de la curva ROI es un grado de libertad adicional. Deflated Sharpe (A.0.3) penaliza esto.
- **Definición ambigua de "decay shape":** lineal vs exponencial vs step-function. Tres definiciones distintas, todas defensibles. Surface en §6.
- Asimetría TP-decay-only deja al SL ATR intacto → si el SL es el problema (no el TP), no rescata.

**Etiqueta:** **promising, needs validation con cuidado anti-overfit.**

**Citas:**
- Freqtrade `minimal_roi`: <https://www.freqtrade.io/en/stable/strategy-customization/#minimal-roi>.
- Sample strategy: [`sample_strategy.py`](https://github.com/freqtrade/freqtrade/blob/develop/freqtrade/templates/sample_strategy.py).

### 3.3 — Fixed-horizon adaptativo por régimen / símbolo

**Definición:** `horizon_hours` no es constante 5; varía por (régimen del régime detector × tier de holding-period observado en train).

Variantes razonables:
- `horizon = 5h en bear, 8h en sideways, 12h en bull` (refleja lo observado en #4: bear es donde el strategy es menos malo, bull catastrófico — un horizon corto en bull podría limitar daño).
- `horizon = winners_median_holdtime` per symbol (BTC=14h, ETH=14h; small-caps **no medible** — 0 winners en train, post-#6). El "5h" que el doc anterior atribuía a small-caps era mediana de **losers**, no de winners; el winner holdtime per-símbolo en small-caps es indefinido bajo la exit logic actual.

**Pros:**
- Aprovecha el dato heterogéneo del basket (#6 mostró ratio 3.5x BTC, 2.8x ETH, 0 winners en 8 small-caps).
- Aligned con time-series momentum vol-targeting: holding period = signal forecast horizon (Moskowitz et al. 2012, ver §4).

**Cons / riesgos:**
- **Alto riesgo de overfit a train.** Per-symbol horizons multiplican el grado de libertad por 10. Deflated Sharpe muerde fuerte.
- Régime detector ya está en producción y es ruidoso; segundary keying al exit logic propaga el ruido.
- "Holding-time tier" en small-caps no es medible (0 winners) — solo aplicable a 2 símbolos.

**Etiqueta:** **promising en concepto, alto riesgo en práctica. Solo proponer si A.4 puede demostrar que sobrevive Deflated Sharpe con N=10 trials.**

**Citas:**
- Moskowitz, Ooi & Pedersen (2012), "Time Series Momentum", *JFE* 104(2): <https://doi.org/10.1016/j.jfineco.2011.11.003>.
- López de Prado fixed-horizon labels critique: <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3104816>.

### 3.4 — Híbrido fixed-horizon + post-horizon trailing

**Definición:** Antes de t+5h, **no exit logic activo** — la posición corre libre (modulo un disaster-SL muy ancho). En t=+5h, el horizon "se cumple" y se activa un trailing stop que se mueve con el precio. La idea: el signal predice que a t+5h el precio está más alto; *después* de eso, no sabemos qué pasa, así que el trailing protege ganancia.

**Pros:**
- Conceptualmente coherente con la estructura de la edge: la señal **es válida hasta t+5h**, después es ruido. El trailing reconoce que post-horizon el strategy ya no tiene info y solo está cosechando ganancia residual.
- Permite capturar tail upside cuando el momentum continúa (no caps ganancia en +5%, deja correr).
- Complementario, no contradictorio, con fixed-horizon simple.

**Cons / riesgos:**
- Más estados → más debugging, más superficie de bugs. El sistema actual tiene 3 estados (SL, TP, BE); este agrega un cuarto (post-horizon trail).
- Si el disaster-SL pre-horizon es demasiado ancho, MAE intermedio puede violar el risk budget de 1% (problema observado en #3 con widening de SL).
- "post-horizon trailing" tiene 3 definiciones plausibles: trail at ATR, trail at fixed%, trail at HWM-x%. Cada una con assumptions diferentes.

**Etiqueta:** **promising para tail-capture, needs validation. Probablemente el más complejo del set; recomendar solo si los simples (3.1, 3.2) no rinden.**

**Citas relevantes (no implementación directa, principio):**
- Hummingbot trailing post-activation pattern: [`TrailingStop(activation_price, trailing_delta)`](https://github.com/hummingbot/hummingbot/blob/master/hummingbot/strategy_v2/executors/position_executor/data_types.py).
- Triple Barrier con trailing dentro del timer: implícito en mlfinlab labeling pipeline.

### Cuadro resumen (4 candidatos)

| Candidato | Cambio estructural | Overfit risk | Complejidad código | Captura signal-decay | Recomendación A.4 |
|---|---|---|---|---|---|
| 3.1 Triple Barrier puro | Bajo | Bajo | Bajo | ✅ via timer | **prototipar primero** |
| 3.2 Time-decaying TP | Medio | Medio-alto | Medio | ✅ explícito | prototipar segundo |
| 3.3 Adaptive horizon | Alto | **Alto** | Alto | ✅ vía régimen | solo si 3.1 falla |
| 3.4 Horizon + post-trail | Alto | Medio | Alto | ✅ vía 2-stage | último — tail-capture |

---

## 4 · Quant tradicional addendum

Patterns de la literatura académica + práctica de hedge funds que **no aparecen (o aparecen en forma shallow)** en frameworks retail cripto.

### 4.1 — Triple Barrier Method (López de Prado)

Etiqueta / exit cuando se hit el primer barrier de {profit-take, stop-loss, vertical time}. El **vertical barrier es la pieza clave** que el ATR-trail retail omite. Source: López de Prado, *Advances in Financial Machine Learning* (Wiley, 2018), Ch. 3; mlfinlab implementation: <https://github.com/hudson-and-thames/mlfinlab>; docs: <https://mlfinlab.readthedocs.io/en/latest/labeling/tb_meta_labeling.html>.

**Implicación para el sistema h=+5:** agregar barrier vertical a t+5h junto a SL/TP ATR. La setup actual deja posiciones abiertas más allá del horizon donde la edge ya decayó a ~0.

### 4.2 — Half-life de momentum (Jegadeesh & Titman, 1993)

Retornos de momentum cross-sectional pico y luego decaen — held ≤ 12 meses, reverse después. Holding period **debe matchear signal half-life**. Source: Jegadeesh & Titman, "Returns to Buying Winners and Selling Losers", *Journal of Finance* 48(1), 1993: <https://doi.org/10.1111/j.1540-6261.1993.tb04702.x>.

**Implicación:** si la half-life empírica es ~5h, target hold ≈ 5–10h max. ATR-trails que promedian 30+ horas mecánicamente comen el alpha completo.

### 4.3 — Almgren-Chriss optimal execution (2000)

Schedule óptimo de liquidación trade-off market-impact vs price-volatility risk. No es exit-rule estrictamente, pero es el framework canónico para "cuando size importa, exit on schedule, not trigger". Source: Almgren & Chriss, "Optimal Execution of Portfolio Transactions", *Journal of Risk* 3(2), 2000: <https://www.courant.nyu.edu/~almgren/papers/optliq.pdf> (Almgren's institutional copy at NYU — durable host).

**Implicación:** a retail size, irrelevante. Si una posición fuera grande enough que slippage > 5bps, se schedulea exit en lugar de un único market order al timer.

### 4.4 — López de Prado, "10 Reasons Most ML Quant Funds Fail" (2018)

Reason #4 explícita: fixed-horizon labels son **estadísticamente sesgados** y ignoran path dependency. Triple-barrier labels recomendados. Source: <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3104816>.

**Implicación:** mantener h=+5 como *training label horizon*, ejecutar via triple-barrier para que SL/TP también exitee temprano cuando el path lo amerita.

### 4.5 — Donchian / Turtle exits (CTA tradicional)

Turtle System 1 exitea en 10-day Donchian low (longs) / high (shorts) — **trend-failure exit puro**, no ATR trail. Source: original Turtle rules: <https://www.turtletrader.com/rules/>; Covel, *The Complete TurtleTrader* (2007).

**Implicación:** ortogonal a un sistema con edge a h=+5 (no es un trend-following long-horizon). Pero si la señal *fuera* un trend signal en lugar de mean-reversion 5h, el ATR-trail debería intercambiarse por un Donchian-style structural exit.

### 4.6 — Time-Series Momentum vol-targeted (Moskowitz-Ooi-Pedersen 2012)

Signal TSMOM 12 meses con rebalance mensual y vol-targeting ex-ante. **Principio: holding period = signal forecast horizon, exposure escalada por vol, no por stop distance.** Source: Moskowitz, Ooi & Pedersen, "Time Series Momentum", *JFE* 104(2), 2012: <https://doi.org/10.1016/j.jfineco.2011.11.003>; preprint SSRN: <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2089463>.

**Implicación:** rebalancear/re-evaluar cada 5h sobre la signal misma en lugar de dejar al ATR definir exit; sizing por vol-target (e.g., 1% portfolio vol), no por ancho de stop.

### 4.7 — Ornstein-Uhlenbeck half-life (stat-arb)

Para spreads mean-reverting, half-life = `-ln(2) / ln(1+λ)` del fit AR(1)/OU. Holding period ≈ half-life, exit por reversion a la media en lugar de ATR. Sources: Chan, *Algorithmic Trading: Winning Strategies and Their Rationale* (Wiley, 2013), Ch. 2: <https://www.wiley.com/en-us/Algorithmic+Trading%3A+Winning+Strategies+and+Their+Rationale-p-9781118460146>; Avellaneda & Lee, "Statistical Arbitrage in the U.S. Equities Market", 2010: <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1153505>.

**Implicación:** si la edge a +5h viene de mean-reversion del LRC, fitear OU sobre el residuo, set time-stop = OU half-life, exit on z-score → 0 — no on ATR.

### Hedge-fund notes (CTA / trend-following)

- Turtle rules (Donchian channel exit): <https://www.turtletrader.com/rules/>.
- Dunn Capital ATR-trail: folklore, sin paper canónico público; ver Schwager, *Hedge Fund Market Wizards* (2012): <https://www.wiley.com/en-us/Hedge+Fund+Market+Wizards-p-9781118273043>. Citar con cuidado.
- Vol-targeted hold à la Moskowitz et al.: <https://doi.org/10.1016/j.jfineco.2011.11.003>.

### Qué agrega este addendum vs frameworks retail

Los frameworks retail exponen ATR/percent/trailing-stop primitives pero tratan el exit logic como **strategy-author concern**, no como problema de labeling/horizon-matching. La literatura quant tradicional es explícita en 3 puntos que el tooling retail nunca centra: (1) **time barriers son mandatorios** cuando la edge tiene un horizonte finito (Triple Barrier, OU half-life, Jegadeesh-Titman decay); (2) **holding period = forecast horizon** (Moskowitz, López de Prado); (3) **exits sized/scheduled, not trigger-driven**, una vez que impact importa (Almgren-Chriss). La destrucción ~100% de edge observada en h=+5 cripto es **violación textbook de (1) y (2)** — invisible desde dentro del API de exit-block de cualquier framework retail.

---

## 5 · Recomendación

**Framing crítico:** §3.1 NO es una decisión, es **el siguiente experimento** dentro de una secuencia de gates. La diferencia importa: una "decisión" implica que pasa el experimento se aplica; un "experimento" produce data que alimenta la decisión real, que se toma después de Gate 4. El doc anterior comprimió esto en "Si A.4 hit la barra de A.0.3, está hecho" — eso era prematuro. La secuencia explícita:

### Gate 0 — Resolver Análisis #8 (robustez temporal) conscientemente, no implícitamente

Análisis #8 (robustez temporal, dentro de [#281](https://github.com/sssimon/trading-spacial/issues/281)) quedó **inconclusivo** en el diagnóstico. Hay dos caminos legítimos, NO un default:

- **Camino A: cerrar Análisis #8 ANTES de A.4.** Buscar data pre-train adicional (e.g., backtest exchanges con histórico más largo, datos OOS de proveedor alternativo) hasta que la pregunta "edge real vs artifact" tenga respuesta empírica. Costo: tiempo. Beneficio: cuando holdout falle, sabés POR QUÉ.

- **Camino B: aceptar holdout como test dual-propósito.** El holdout valida simultáneamente (i) la edge predictiva y (ii) la exit logic. Costo: si holdout falla, no podés diagnosticar si fue (i) o (ii) — el experimento queda confundido. Beneficio: tiempo a A.4 ahora.

**Decision pendiente para martes:** ¿Camino A o B? **No es asunción operativa** — debe estar documentada en el ticket de A.4 antes de empezar. Si Camino B, el N de Deflated Sharpe en Gate 3 debe penalizar dos preguntas en paralelo, no una.

### Gate 1 — Re-tune ATR multipliers honest

Re-tunear `atr_sl_mult/tp/be` sobre `[earliest, holdout_start - 1 bar]` ([provenance doc](../specs/es/2026-04-30-a1-holdout-dataset-provenance.md), caveat #1). Sin esto, los multipliers actuales heredan exposición al rango holdout y cualquier evaluación posterior tiene leakage residual.

### Gate 2 — Evaluación contra holdout

Aplicar el pattern §3.1 (Triple Barrier puro: ATR_SL + ATR_TP re-tuned + time_limit) sobre el holdout dataset.

**Sub-gate 2a — Decoupling per-cluster es HIPÓTESIS, no asunción.**

El doc original sugería evaluar §3.1 sobre dos sub-baskets: `{PENDLE, ADA}` con `time_limit = 5h` y `{BTC, ETH}` con `time_limit = 14h`, basándose en la heterogeneidad de holding-period observada en #6 train. **Esa partición se está derivando de una observación post-hoc en train** — exactamente el tipo de "casualmente coincide con la estructura de los overrides previos" que el doc flaguea como riesgo de leakage.

Tratamiento honest:
- Pre-registrar la hipótesis: "los clusters {PENDLE, ADA} y {BTC, ETH} se distinguen significativamente en holding-period óptimo en holdout".
- Test en holdout con basket dividida.
- **Si en holdout los clusters NO se distinguen → colapsar a single-basket-single-horizon** (probablemente sub-óptimo en ambos extremos, y eso ES información válida — significa que la heterogeneidad de train era artifact).
- Si los clusters SÍ se distinguen → la partición se ratifica y queda operativa.

No pre-asumir el decoupling es la diferencia entre un test honesto y curve-fit a train.

### Gate 3 — Deflated Sharpe con N honest

`N` para la corrección de multiple-testing **NO es 10** (cardinalidad del basket). `N` debe contar:
- Símbolos del basket (10).
- Variantes de exit pattern barridas (al menos: Triple Barrier, ATR-only baseline, time-only baseline, time-decay TP si aplica).
- Grid de ATR multipliers explorados en Gate 1.
- Override-history acumulado en epics anteriores (#121, #135, etc.) — cada uno consumió DOF (ver §6.6 para el desglose enumerable y decisión pendiente).
- Si Gate 0 = Camino B: penalty adicional por validar dos preguntas (edge + exit logic) en el mismo holdout.
- Cluster partitions evaluadas (1 si single basket, 2+ si decoupling).

El número honest probablemente está en el rango N=30–100, no N=10. Eso cambia el threshold de Deflated Sharpe materialmente.

**Atribución del threshold:** el valor numérico del DSR vive en [#249](https://github.com/sssimon/trading-spacial/issues/249) (A.3 quantitative bar), **no** en [#278](https://github.com/sssimon/trading-spacial/issues/278) — #278 sólo define **cómo** computar la métrica (`sharpe_deflated`, `n_effective`, `sigma_sr_trials`, `prob_sr_gt_0`). Pre-fijar el threshold en #249 antes de Gate 3 es prerequisito; sin número ex-ante, no hay pass/fail honesto y el gate degenera en post-rationalization.

### Gate 4 — Cost-survival check con pass criterion PRE-REGISTRADO

**El criterion debe definirse ANTES de correr el experimento, no después.** Sin pre-registration, el sesgo de continuar empuja el bar hacia abajo cuando los números lleguen incómodos.

Sugerencia de criterion (a confirmar por reviewer/dev en martes, ANTES de Gate 1):

- **Net edge per-trade ≥ Y bps**, donde `Y` es función del round-trip cost esperado en el cluster:
  - Para `{BTC, ETH}`: `Y_majors ≈ 2 × round_trip_cost_majors_p50` (e.g., si round-trip mediana ≈ 10–15 bps, `Y_majors ≈ 25 bps` = ~2x safety margin).
  - Para `{PENDLE, ADA}`: `Y_smallcaps ≈ 2 × round_trip_cost_smallcaps_p50` (más alto, depende de #279 sqrt v2 calibration).
- **Net Sharpe ≥ X**, con `X` definido contra benchmark de "buy-and-hold el cluster" para el período holdout. Pasar SR del strategy > SR del HODL es bar mínimo; preferiblemente SR ≥ 1.0 anualizado.
- Aplicar el cost model upstream cuando esté mergeado a main: A.0.2 linear v1 ([#277](https://github.com/sssimon/trading-spacial/issues/277), al 2026-04-30 vive solo en `feat/methodology-a02-realistic-costs` — branch local, sin PR abierto, no mergeado) + #279 sqrt v2 ([#279](https://github.com/sssimon/trading-spacial/issues/279), sin PR). **Las métricas en circulación son cost-OFF — Gate 4 no puede declararse pass hasta que A.0.2 esté shipped a main y aplicado upstream a las métricas que el doc cite como evidencia.**

**Compromiso:** los valores numéricos exactos de `Y_majors`, `Y_smallcaps`, `X` se acuerdan martes. Documentados en el ticket de A.4 antes de Gate 1. Si en Gate 4 los números fallan el criterion, A.4 no procede a producción — independiente de cuán "cerca" hayan quedado.

---

### Resumen — qué prototipar y en qué orden

1. **Gate 0–4 sobre §3.1 (Triple Barrier puro).** Es el siguiente experimento, no la decisión.
2. **§3.2 (time-decay TP estilo Freqtrade ROI):** solo si §3.1 falla por dejar dinero en la mesa (i.e., gross edge alto pero TP fija lo trunca).
3. **§3.3, §3.4: NO ahora.** Reservar.

### Lo que NO recomiendo prototipar primero (sin cambio)

- Adaptive horizon per-symbol (§3.3) — N de Deflated Sharpe explota; overfit risk alto. Reservar para iteración futura SI §3.1 + §3.2 no alcanzan.
- Híbrido post-horizon trailing (§3.4) — más complejo, value condicional. Reservar.

---

## 6 · Decisions to surface (no resueltas en este doc)

1. **Definición operacional de "signal-decay-based" exit — DEFER.** Tres definiciones distintas en la práctica, todas válidas:
   - **Académica:** cierre cuando el score de la señal cae por debajo de un umbral (re-evaluar la signal cada bar).
   - **Retail / Freqtrade-style:** TP que decae con tiempo (ROI table) — proxy temporal del decay sin re-evaluar la signal.
   - **Quant cuantitativa:** cierre proporcional al decay de half-life del momentum factor (OU half-life, Jegadeesh-Titman).
   Tabularlo bajo un solo bucket es engañoso. **Resolución diferida:** esta decisión solo es relevante si §3.1 falla y §3.2 entra en juego. Resolverla ahora es overengineering — la edit anterior del doc proponía cerrarla en este review, pero es más honesto dejar la pregunta abierta hasta que la condición que la dispara (fallo de §3.1) ocurra. Si nunca llega, nunca hay que cerrarla.

2. **Time-limit horizon per-symbol vs fijo en h=+5.** El finding #6 muestra winner-holding mediano de 14h en BTC/ETH y 0 winners en 8 small-caps. Un time-limit a 5h global corta winners potenciales en majors. ¿A.4 acepta horizon per-symbol (con cost de overfit) o fija 5h global y acepta que majors quedan sub-óptimos?

3. **Cobertura del benchmark.** OctoBot quedó cubierto solo via WebSearch summary (web fetch falló con 403). Si OctoBot es prioridad para el review, requiere segunda iteración. Lean / QuantConnect no fue cubierto (out-of-scope: backtesting platform multi-asset, no crypto-native). Marcar si reviewer quiere addendum adicional.

4. **vectorbt como herramienta para A.4 grid search.** vectorbt OSS permite barrer combinaciones (ATR-only / horizon-only / híbrido / signal-decay) en minutos sobre el mismo dataset. ¿A.4 considera adoptar vectorbt como motor de grid search en paralelo a `simulate_strategy`, o se queda con el motor actual? Es una decisión de tooling, no de policy.

5. **~~Triple Barrier en Hummingbot v2 como blueprint de implementación~~ — CERRADO.** Re-implementación in-place en `backtest._close_position`. Agregar `time_limit` como kwarg en `_close_position` + check en el loop bar-by-bar (~20 líneas). Hummingbot v2 puede inspirar el shape de `TripleBarrierConfig` (dataclass con campos opcionales), pero **portar/refactor architectural queda fuera de scope para A.4** — es un proyecto de 2 meses disfrazado de exit logic decision. Mínima fricción gana; si en el futuro el sistema crece a múltiples patterns y el switch policy-vs-plumbing se vuelve doloroso, ahí se considera el refactor. No ahora.

6. **Gap auditable: `config.json` `.gitignored` ⇒ provenance de overrides ausente.** Las iteraciones de override por símbolo (atribuídas a epic [#121](https://github.com/sssimon/trading-spacial/issues/121), purga [#135](https://github.com/sssimon/trading-spacial/issues/135), etc.) no tienen historia git — `git log -- config.json` retorna 0 commits porque el archivo está en `.gitignore`. Cero auditabilidad de cuándo se introdujo cada override, en qué experimento, con qué justificación.

   **Impacto cuantitativo en Gate 3.** El cómputo honest de `n_effective` para Deflated Sharpe tiene un piso enumerable y un techo desconocido:

   - **Lower bound auditable: ~1,500–2,000 trials.** Componentes:
     - `auto_tune.py:64-68` GRID = 7×5×3 = **105 combos** × 10 símbolos × {LONG,SHORT} = ~2,100 evaluaciones por corrida (`scripts/tune_per_direction.py`).
     - `grid_search_tf.py:62-69` GRID = 4×4×4×4×3 = **768 combos** trend-following.
     - MR optimization CSVs en `data/backtest/`: 5 símbolos × 105 = **525 trials** registrados.
     - Kill-switch v2 grid (epic [#216](https://github.com/sssimon/trading-spacial/issues/216), commit `faefa22`): sweep adicional, no enumerado.
   - **Upper bound: desconocido.** Cualquier exploración ad-hoc local que mutó `config.json` no dejó artifact.

   **Decisión pendiente para #278 trial registry:** ¿(a) capturar provenance de overrides retroactivamente (re-correr los grids documentados, asociar cada combo ganador a un commit-equivalente), o (b) aceptar el techo desconocido como upper-bound estructural en la primera iteración y aplicar el `N_floor = 50` que el body de #278 propone para los primeros 6 meses? Sin tomar postura, el "N=10 era honestidad incompleta" sigue subestimando el N real por al menos dos órdenes de magnitud.

   **Pre-requisito derivado para Gate 3:** la decisión (a) o (b) debe estar tomada y documentada en el ticket de A.4 antes de correr el experimento. La elección cambia el threshold del DSR (#249) materialmente, no es cosmética.

---

## 7 · Estado de completitud

| Sección | Estado | Notas |
|---|---|---|
| §1 Tabla comparativa | ✅ completa | 7 frameworks |
| §2 Sección por framework | ✅ Freqtrade, Hummingbot, Jesse, Backtrader, NautilusTrader, vectorbt OSS | OctoBot cubierto en cobertura limitada |
| §3 Patterns candidatos | ✅ 4 candidatos con pros/cons aplicados al contexto | |
| §4 Quant tradicional addendum | ✅ 7 patterns con citas verificables | |
| §5 Recomendación | ✅ 5 gates (0-4) con sub-gate 2a decoupling, framing como "siguiente experimento" no decisión | |
| §6 Decisions to surface | ✅ 6 decisiones surfaced | §6.1 deferred, §6.5 cerrada, §6.6 nueva (provenance gap) |

**Frameworks cubiertos del scope #282:** Freqtrade ✅, Hummingbot ✅, Jesse ✅, Backtrader ✅ (+ `bt` flag-only), NautilusTrader ✅, vectorbt ✅, OctoBot 🟡 cobertura limitada.

**Scope reducido — pendiente:** OctoBot deep-dive (web fetch falló). Sin impacto material en la recomendación — los frameworks principales (Freqtrade + Hummingbot v2) ya cubren los patterns relevantes.

**Time spent:** ~4h research + writing.

---

## 8 · Cómo se usó este benchmark para producir la recomendación

- Cruce 1 (#282 ↔ §3): los patterns retail-disponibles que **agregan algo** a fixed-horizon h=+5 simple son §3.1 (time-limit hard a SL+TP existente) y §3.2 (time-decaying TP). Los otros dos (§3.3, §3.4) introducen complejidad/overfit no justificada por la edge medida.
- Cruce 2 (frameworks ↔ academic): la convergencia entre Hummingbot v2's default (Triple Barrier) y López de Prado's 10-Reasons paper (#4 de quant addendum) es el **único punto donde retail tooling y academia coinciden en el patrón a usar**. Eso es señal fuerte para §3.1 sobre §3.2.
- Cruce 3 (sistema-específico): el caveat de holdout (re-tune obligatorio antes de evaluar) y Deflated Sharpe (A.0.3) ambos penalizan grados de libertad adicionales. §3.1 agrega 1 grado (`time_limit`); §3.3 agrega 10+ (per-symbol). El bar de A.3 va a respetar esa diferencia.
