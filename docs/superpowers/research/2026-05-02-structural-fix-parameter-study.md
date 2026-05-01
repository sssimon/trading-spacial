# Structural Fix Parameter Study — `time_limit_hours` × `max_participation_rate` × `cooldown_hours`

**Fecha:** 2026-05-02
**Issue:** [#292](https://github.com/sssimon/trading-spacial/issues/292)
**Branch:** `research/structural-fix-parameter-study-292`
**Status:** ready for review
**Time-bounded:** ~5h research + writing
**Bloqueante de:** los 3 PRs estructurales (exit logic redesign, sizing cap, cooldown parity scanner ↔ backtest)
**No bloquea:** nada operacional este momento

---

## 0 · Framing y restricciones

Este doc produce **30 valores** (10 símbolos × 3 parámetros) — `time_limit_hours`, `max_participation_rate`, `cooldown_hours` — cada uno con derivation + benchmark anchor + confidence tier. El propósito es alimentar los 3 PRs estructurales con valores defendibles ante un reviewer técnico (incluido yo en 6 meses), no con guesses.

**Restricciones operativas heredadas del [issue #292](https://github.com/sssimon/trading-spacial/issues/292):**

- Sin código de producción modificado.
- Sin backtests corridos para "verificar" valores. La validación empírica es responsabilidad de los 3 PRs estructurales subsecuentes (smoke tests).
- Patterns sin evidencia clara → tier `low` o `needs validation`. No inventar números de alta confianza.
- Definiciones ambiguas → §4 "Decisions to surface", no se cierran unilateralmente.
- **NO basket reduction.** Los 30 valores cubren los 10 símbolos del basket actual (`DEFAULT_SYMBOLS` en `btc_scanner.py`), incluso los 6 marcados Mundo C local en [#281](https://github.com/sssimon/trading-spacial/issues/281).

**Premisas heredadas del diagnóstico [#281](https://github.com/sssimon/trading-spacial/issues/281), benchmark [#282](https://github.com/sssimon/trading-spacial/issues/282) y pivot plan ([`a4-strategic-pivot-plan.md`](../plans/2026-05-01-a4-strategic-pivot-plan.md)):**

- Peak predictivo a `h=+5` (1H bars). Solid (t≥2.5): PENDLE 2.60, ADA 2.55. AVAX 2.68 reclasificado C-like por SL widening test (#281 §3).
- Winner holding median 14h en BTC/ETH; 0 winners en 8 small-caps bajo el exit logic actual (#281 §6).
- Cost spectrum 4 órdenes de magnitud: BTC `participation_p50 = 0.0003` → PENDLE `8.0` (#281 §1).
- Cooldown actual `COOLDOWN_H = 6` global ([`btc_scanner.py:131`](../../../btc_scanner.py)).
- Cluster classification: B-like sólido (PENDLE, ADA), B-like parcial (BTC, ETH), Mundo C local (DOGE, JUP, RUNE, XLM, AVAX, UNI). Per [pivot plan §1.2](../plans/2026-05-01-a4-strategic-pivot-plan.md#12-cluster-classification-del-basket-de-10-per-281).

**Tier framework para cada valor (per #292 spec):**

- **high**: 2+ sources independientes convergen + derivable de datos del diagnóstico para el símbolo específico.
- **medium**: 1 source clara o derivación válida con assumptions explicitadas.
- **low**: best-guess ante evidencia ambigua, flagged como `needs empirical validation post-PR`.

---

## 1 · Área 1 — `time_limit_hours` per-symbol

### 1.1 Pregunta y método

¿Qué `time_limit_hours` (vertical barrier de Triple Barrier) propone la literatura + benchmark crypto retail per-symbol, dado el peak predictivo h=+5 y winner-holdtime asimétrico (14h en BTC/ETH, indeterminado en small-caps)?

### 1.2 Framework defaults (concrete numerical findings)

| Framework | Typical time-limit | Source URL | Notes |
|---|---|---|---|
| Freqtrade sample (5m tf) | 60 min ≈ 12 bars | [sample_strategy.py](https://github.com/freqtrade/freqtrade/blob/develop/freqtrade/templates/sample_strategy.py) | Last `minimal_roi` step doubles as soft time-stop |
| Freqtrade Solipsis (1h tf) | tiers 4h / 24h / 72h | [Solipsis-v1.py](https://github.com/werkkrew/freqtrade-strategies/blob/main/strategies/archived/Solipsis-v1.py) | Multi-tier; first hard barrier at 4h |
| Freqtrade HourBased (1h tf) | ~30h hard | [HourBasedStrategy.py](https://github.com/freqtrade/freqtrade-strategies/blob/main/user_data/strategies/HourBasedStrategy.py) | Soft barrier at 169 min ≈ 2.8h |
| Freqtrade FSample / NFI X2 | None (signal-driven) | [FSampleStrategy](https://github.com/freqtrade/freqtrade-strategies/blob/main/user_data/strategies/futures/FSampleStrategy.py), [NFIX2](https://github.com/iterativv/NostalgiaForInfinity/blob/main/NostalgiaForInfinityX2.py) | Anti-pattern para nuestro caso (no tenemos exit signal) |
| Hummingbot MACD-BB (intraday) | 55 min | [MACD-BB blog](https://hummingbot.org/blog/directional-trading-with-macd-and-bollinger-bands/) | σ-scaled SL/TP, intraday cadence |
| Hummingbot PositionExecutor prompt | 2700s = 45 min | [PositionExecutor](https://hummingbot.org/v2-strategies/executors/positionexecutor/) | Default value en config prompt |
| Hummingbot DCA Bollinger | 43,200s = 12h | [walkthrough controller](https://hummingbot.org/v2-strategies/walkthrough-controller/) | Slower DCA grid context |
| Jesse day-trading example | 24h | [Jesse docs](https://docs.jesse.trade/docs/backtest) | Coarse end-of-day semantics |
| NautilusTrader | GTC default; GTD opcional | [Orders](https://nautilustrader.io/docs/latest/concepts/orders/) | No bar-count primitive nativo |
| VectorBT | None default | [Stop signals](https://deepwiki.com/polakowo/vectorbt/4.2-stop-based-exit-signals) | Time-stop = exit signal DIY |

**Síntesis:** Dos regímenes dominantes. **(a) Intraday signal strategies** usan ~45–60 min (Hummingbot 55min, Freqtrade sample 60min). **(b) 1H-bar swing strategies** usan o ningún time-stop o tiers 4h–72h con primer hard barrier en **2–8h**. Nuestro sistema es tipo (b): 1H signal cadence, sin exit-signal. Intersect → **3–8h** para primer hard barrier; 12–24h para outer cap generoso.

### 1.3 Academic anchors

- **López de Prado (AFML, Ch. 3)** — vertical barrier en bars, independiente de los horizontal SL/TP. Recomendación: vertical ≥ peak predictive horizon + buffer para path expression. Sources: [Reasonable Deviations](https://reasonabledeviations.com/notes/adv_fin_ml/), [Quantreo Triple Barrier](https://www.newsletter.quantreo.com/p/the-triple-barrier-labeling-of-marco), [Mlfin.py](https://mlfinpy.readthedocs.io/en/latest/Labelling.html).
- **Moskowitz, Ooi & Pedersen (2012)** — momentum half-life ≈ 12 meses cross-asset; principio scale-invariant: "hold ≥ horizon-of-edge × O(1), pero no >> 2× horizon." Source: [PDF](http://docs.lhpedersen.com/TimeSeriesMomentum.pdf).
- **Jegadeesh & Titman (1993)** — optimal momentum hold = 0.25–0.5 × signal horizon. Source: [PDF](https://www.bauer.uh.edu/rsusmel/phd/jegadeesh-titman93.pdf).
- **OU half-life (Chan / Avellaneda)** — `halflife = ln(2)/θ`; max-holding-period proxy. Sources: [flare9x OU](https://flare9xblog.wordpress.com/2017/09/27/half-life-of-mean-reversion-ornstein-uhlenbeck-formula-for-mean-reverting-process/), [arbitragelab half-life](https://hudson-and-thames-arbitragelab.readthedocs-hosted.com/en/latest/cointegration_approach/half_life.html). NB: per-symbol OU fit no se ejecutó en este research (requeriría código) — surfaced en §4.
- **Crypto-specific empirical** — BTC hourly momentum decae ~½ trading day (~12h); altcoins similar pero más débil. Hurst ≈ 0.53–0.55 → persistencia de orden single-digit hours. Sources: [Shen 2022 Bitcoin intraday](https://onlinelibrary.wiley.com/doi/abs/10.1111/fire.12290), [Intraday predictability ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S1062940822000833), [Stylized Facts arxiv 2402.11930](https://arxiv.org/html/2402.11930v2).

### 1.4 Calibration rule

1. **Lower bound:** `peak_predictive_horizon = 5h` (AFML "give signal room to express").
2. **Upper bound (B-like parcial):** `winners_median_holding` (no truncar winners validados).
3. **B-like sólido (no winners observados):** `peak_horizon` directo (5h) — honest about train-set noise.
4. **Mundo C local:** basket default 5h (intersección Hummingbot ~50min × scale + Freqtrade 1H first-tier ~3–4h, escalado a h=+5 cadence). Confidence baja por construcción.

### 1.5 Per-symbol proposals (10 filas)

| Symbol | `time_limit_hours` | derivation | benchmark_anchor | confidence_tier |
|---|---:|---|---|---|
| **BTCUSDT** | **14** | Winners-median 14h (#281 §6, B-like parcial, t=1.83). Truncar a h=+5 chopearia winners; AFML "vertical ≥ horizon" ✅; M-O-P "no >> 2× horizon" → 14h ≤ 28h ✅. | [Solipsis 24h tier](https://github.com/werkkrew/freqtrade-strategies/blob/main/strategies/archived/Solipsis-v1.py); [Shen 2022 ½-day decay](https://onlinelibrary.wiley.com/doi/abs/10.1111/fire.12290) | high |
| **ETHUSDT** | **14** | Idéntico estructural a BTC (winners-median 14h). t=1.37 marginal → algo de riesgo de chopear edge si TL menor. | Mismo que BTC | medium |
| **ADAUSDT** | **5** | B-like sólido t=2.55, 0 winners en train → no winner-anchor. Peak h=+5 directo (AFML mínimo). Loser-median 2.5h confirma SL exits tempranos; 5h da signal 2× loser-median. | [Hummingbot MACD-BB 55min](https://hummingbot.org/blog/directional-trading-with-macd-and-bollinger-bands/); [Freqtrade sample 60min](https://github.com/freqtrade/freqtrade/blob/develop/freqtrade/templates/sample_strategy.py) | medium |
| **PENDLEUSDT** | **5** | B-like sólido t=2.60, 0 winners. Mismo logic que ADA: peak h=+5; loser-median 3h → 5h ≈ 1.7× loser-median. | Mismo que ADA | medium |
| **AVAXUSDT** | **8** | t=2.68 fuerte pero reclassified C bajo SL widening (#281 §3) — edge existe pero path es wider. Loser-median 10h anómalamente largo; 8h sits entre h=+5 y loser-median. | [Solipsis 4h tier](https://github.com/werkkrew/freqtrade-strategies/blob/main/strategies/archived/Solipsis-v1.py) | medium-low |
| **XLMUSDT** | **5** | t=2.16 marginal, 0 winners, loser-median 2h. h=+5 floor; loser-median below floor consistent with SL chop. | Mismo que ADA | low-medium |
| **DOGEUSDT** | **5** | Mundo C local, t=0.16 noise. No symbol-specific evidence; basket default. | [Freqtrade 60min](https://github.com/freqtrade/freqtrade/blob/develop/freqtrade/templates/sample_strategy.py); [Hummingbot 45-55min](https://hummingbot.org/v2-strategies/executors/positionexecutor/) | low |
| **UNIUSDT** | **5** | Mundo C local, t=1.13 noise. Basket default. | Mismo que DOGE | low |
| **JUPUSDT** | **5** | Mundo C local, t=0.07 noise. Basket default. | Mismo que DOGE | low |
| **RUNEUSDT** | **5** | Mundo C local, t=-0.18 noise (slightly negative — strongest "no edge" signal). Basket default. | Mismo que DOGE | low |

**Confidence summary Área 1:** 1 high · 4 medium · 1 medium-low · 4 low.

---

## 2 · Área 2 — `max_participation_rate` per-symbol

### 2.1 Pregunta y método

`participation_rate = position_notional_usd / 1H_bar_volume_usd`. Cap: si R-multiple sizing yieldea `notional > cap × bar_volume`, scale-down o skip (ver §2.5 D2). El cost spectrum brutal (#281 §1) — PENDLE p50=8.0 = 800% de un bar — exige cap per-symbol.

### 2.2 Framework / academic anchors

| Source | Recommended POV | Context | URL |
|---|---|---|---|
| Almgren & Chriss (2001) | Modelo calibrado con `eta` a 1% daily volume, `gamma` a 10% | Equities institucional | [PDF](https://www.smallake.kr/wp-content/uploads/2016/03/optliq.pdf) |
| Almgren-Chriss practitioner | Retail-scale << 1% daily ≈ regime lineal puro | Equity execution | [Quantum-blog](https://quantum-blog.medium.com/almgren-chriss-optimal-execution-model-5a85b66555d2) |
| Bouchaud (square-root law) | Validez ≈ 0.5%–20% POV; bajo 0.5% noisy ~4 bps; sobre 20% breaks | All asset classes incl BTC | [Substack](https://bouchaud.substack.com/p/the-square-root-law-of-market-impact) |
| Donier & Bonart (2014) — Bitcoin | I(Q) ∝ Q^0.5 confirmado en BTC across 4 décadas | BTC spot | [arxiv](https://arxiv.org/abs/1412.4503) |
| Talos (crypto institucional) | Algos cripto típicamente max POV 10–30% con price protection | Crypto multi-asset | [Talos blog](https://www.talos.com/insights/understanding-market-impact-in-crypto-trading-the-talos-model-for-estimating-execution-costs) |
| Cube Exchange (VWAP) | Targets comunes 5–10% POV | Equities/crypto VWAP | [Cube](https://www.cube.exchange/what-is/vwap-order) |
| Easley et al. (crypto microstructure) | Crypto tiene mayor toxicity (Kyle's λ) que futures → caps retail más tightos | BTC + altcoins | [SSRN PDF](https://stoye.economics.cornell.edu/docs/Easley_ssrn-4814346.pdf) |
| Kaiko (altcoin liquidity) | Altcoin slippage materially > BTC/ETH; >50% top-50 tiene <$200M ADV | Altcoins | [Moving Markets](https://research.kaiko.com/insights/moving-markets-liquidity-and-large-sell-orders); [Concentration](https://research.kaiko.com/insights/the-crypto-liquidity-concentration-report) |
| Binance LiquidityBoost | Tier thresholds 0.5% y 1% pair volume | Altcoins on Binance | [PRNewswire](https://www.prnewswire.com/news-releases/binance-creates-first-altcoin-focused-spot-liquidity-program-to-meet-demand-for-deeper-liquidity-and-more-market-diversity-302473092.html) |
| Hummingbot inventory skew | `inventory_target_base_pct` (range, no POV) | Crypto MM | [docs](https://hummingbot.org/strategies/v1-strategies/strategy-configs/inventory-skew/) |
| Freqtrade | Portfolio-level (`stake_amount × max_open_trades`); no per-symbol POV | Crypto retail | [docs](https://www.freqtrade.io/en/stable/configuration/) |

**Síntesis crítica:** Ningún framework retail (Freqtrade/Hummingbot/Jesse) shippea per-symbol POV cap. Práctica institucional clusterea 5–30% **daily POV**. Academic normaliza a 1% / 10% **daily**. Crypto-specific (Talos/Kaiko/Easley) flag **higher toxicity** en altcoins → caps tightos.

**Translation note crítica:** El cost model de #277 mide participation contra **1H bar volume** (~1/24 del daily). 10% institucional daily POV ≠ 10% bar POV. Single-shot fill at 10% bar es mucho más agresivo que 10% daily sliced sobre 6h. **Caps sobre bar volume deben ser materially lower que daily-POV equivalents.**

### 2.3 Liquidity tier mapping

| Tier | Definición (`cost_bps_mean` proxy) | Símbolos | Cap propuesto (1H bar POV) | Rationale |
|---|---|---|---:|---|
| **Major (deep liq)** | <150 bps | BTC, ETH | **0.010 (1.0%)** | A-C anchor; bajo Bouchaud 5% "small impact"; observed p99 ≤ 0.024 ya cerca |
| **Mid (Tier-1 alt)** | 150–250 bps | JUP, DOGE, AVAX | **0.005 (0.5%)** | Mitad del major; matches Binance LiquidityBoost lower tier; toxicity Easley justifica < equity defaults |
| **Mid-thin (Tier-2 alt)** | 250–700 bps | RUNE, ADA | **0.003 (0.3%)** | Sub-Bouchaud-floor (0.5%) intencional; book frágil; observed p50 250–500× cap → muchos trades skip |
| **Small** | 700–2000 bps | UNI | **0.002 (0.2%)** | Book thin; single trade puede mover el book |
| **Small (worst)** | >2000 bps | XLM, PENDLE | **0.0015 (0.15%)** | Floor cap. Acknowledges que a este nivel muchos trades se skipean — eso es el structural fix |

**Cap floor rationale:** 0.0015 es el practical floor. Bajo eso → notional < exchange minimum, trivial expected R, y en ese punto el símbolo debería removerse del basket (out of scope).

### 2.4 Per-symbol proposals (10 filas)

| Symbol | `max_participation_rate` | derivation | benchmark_anchor | confidence_tier |
|---|---:|---|---|---|
| **BTCUSDT** | **0.010** | observed_p99=0.0185 → cap halves worst-case sizing, p90=0.0018 << cap. Matches A-C `eta` anchor. | A-C; [Donier-Bonart BTC sqrt](https://arxiv.org/abs/1412.4503) | high |
| **ETHUSDT** | **0.010** | observed_p99=0.0236 → cap halves tail; major-tier idéntico a BTC. | A-C; [Talos institucional](https://www.talos.com/insights/understanding-market-impact-in-crypto-trading-the-talos-model-for-estimating-execution-costs) | high |
| **JUPUSDT** | **0.005** | observed first-trade ~0.098 → 20× tighter. Mid-tier; matches [Binance LiquidityBoost](https://www.prnewswire.com/news-releases/binance-creates-first-altcoin-focused-spot-liquidity-program-to-meet-demand-for-deeper-liquidity-and-more-market-diversity-302473092.html) lower tier. | Binance LB; Kaiko | medium |
| **DOGEUSDT** | **0.005** | observed first-trade ~0.17 → 35× tighter. cost_bps=149 borderline; depth shallower que BTC/ETH. | [Kaiko top-50 altcoin](https://research.kaiko.com/insights/moving-markets-liquidity-and-large-sell-orders) | medium |
| **AVAXUSDT** | **0.005** | observed first-trade 0.32 → 64× tighter. cost_bps=174 firmly mid. | Kaiko; [Easley](https://stoye.economics.cornell.edu/docs/Easley_ssrn-4814346.pdf) | medium |
| **RUNEUSDT** | **0.003** | observed first-trade 0.75 → 250× tighter. cost_bps=515 past mid line. | [Bouchaud sqrt validity floor](https://bouchaud.substack.com/p/the-square-root-law-of-market-impact) | medium |
| **ADAUSDT** | **0.003** | observed first-trade 1.48 → 493× tighter. ADA volume profile más profundo que UNI/XLM pero cost_bps=692 refleja CEX fragmentation. | Kaiko; A-C 0.1% reference | low-medium |
| **UNIUSDT** | **0.002** | observed first-trade 1.85 → 925× tighter. cost_bps=893 firmly small. | Bouchaud breakdown zone; Easley | low-medium |
| **XLMUSDT** | **0.0015** | observed first-trade 5.87 → ~3900× tighter. cost_bps=3098. Floor cap. | [Kaiko thin-altcoin](https://research.kaiko.com/insights/moving-markets-liquidity-and-large-sell-orders) | low |
| **PENDLEUSDT** | **0.0015** | observed first-trade 8.00 → ~5300× tighter. cost_bps=4961. Worst del basket. **Most signals will be skipped — that is the intent.** | Floor cap; canonical structural-fix symbol | low |

**Confidence summary Área 2:** 2 high · 4 medium · 2 low-medium · 2 low.

---

## 3 · Área 3 — `cooldown_hours` per-symbol

### 3.1 Pregunta y método

Cooldown actual = 6h global ([`btc_scanner.py:131`](../../../btc_scanner.py)). Con `time_limit_hours` per-symbol asimétrico (5h B-sólido, 14h B-parcial, 5h C-local), el cooldown 6h global entra en conflicto: para BTC/ETH (TL=14h), re-entry sería posible 8h *antes* de que el trade previo hit time-limit.

Constraint per pivot plan §R1: cooldown debe ser **deterministic prior**, NO grid search (preserva DSR N integrity).

### 3.2 Framework defaults

| Framework | Typical cooldown | Source URL | Notes |
|---|---|---|---|
| Freqtrade `CooldownPeriod` | `stop_duration_candles: 2` (doc example) | [Protections](https://www.freqtrade.io/en/stable/includes/protections/) | Pair-level only. 1H tf → 2h |
| Freqtrade community | 2–12 candles | [strategies repo](https://github.com/freqtrade/freqtrade-strategies) | 1H-tf examples cluster ~5 candles |
| Hummingbot V2 directional | 15s–3600s (Bollinger V1) | [examples](https://hummingbot.org/v2-strategies/examples/), [PositionExecutor](https://hummingbot.org/v2-strategies/executors/positionexecutor/) | Two-decade range; intraday HFT bias |
| Academic momentum (J&T 1993) | 1-month skip on 3–12mo formation | [Quantpedia](https://quantpedia.com/strategies/consistent-momentum-strategy) | Cooldown ≈ 1/3–1/12 horizon |

**Takeaway:** Ningún framework prescribe fórmula cuantitativa. Defaults clusteran "tf × small integer" heurística.

### 3.3 Academic / theoretical anchors

- **Information half-life AR(1):** `T_half = ln(0.5)/ln(|φ|)`. Con |φ|<0.2 (nuestro caso, #281 §2 lag-1 ACs all <0.16), T_half < 1 bar → signal decae dentro de 1H. Sources: [MagPi](https://medium.com/@magpiai/stop-guessing-the-quant-science-of-signal-half-life-and-market-context-ba934a13dd21), [arbitragelab](https://hudson-and-thames-arbitragelab.readthedocs-hosted.com/en/latest/cointegration_approach/half_life.html).
- **Lo–MacKinlay (1988) VR null:** Returns serially uncorrelated. Critical |ρ| ≈ 1.96/√190 ≈ 0.142 a N=190. Max observed |ρ| (XLM 0.155) borderline; ningún otro símbolo statistically significant. Source: [Sewell mirror PDF](https://finance.martinsewell.com/stylized-facts/dependence/LoMacKinlay1988.pdf).
- **Crypto-specific autocorrelation:** Negative AC pequeño-magnitud pero persistente en BTC 1H. Sources: [Tartakovsky (2020)](https://arxiv.org/pdf/2003.13517), [ScienceDirect intraday](https://www.sciencedirect.com/science/article/pii/S1059056024006506), [Stylized Facts arxiv 2402.11930](https://arxiv.org/html/2402.11930v2).
- **Newey-West HAC bandwidth:** `m = ⌊4(T/100)^(2/9)⌋`. Para T=190, m≈4 lags → "serial-dependence horizon" ≈ 4h. Source: [Wikipedia](https://en.wikipedia.org/wiki/Newey%E2%80%93West_estimator).
- **Backtest-vs-live parity:** cooldown 6h global tuned en backtest; deviar sin re-validation = leakage risk.

### 3.4 Derivation rule (deterministic)

```
cooldown_hours = max(
    time_limit_hours,   # never re-enter before previous trade resolves
    NW_horizon = 4,     # cover serial-dependence horizon
    global_floor = 6    # backtest-tuned legacy parity
)
```

**Por qué NO `TL × 1.5`:** ningún source en Freqtrade/Hummingbot/academia endorses multiplicative buffer. Lag-1 AC <0.2 → T_half ≪ 1 bar → buffer cosmético. Pivot plan A7 mencionó "TL × 1.5" como una opción pero sin ancla — la rechazamos aquí explícitamente y la surfacemos en §4 para confirmación.

**Por qué NO `cooldown < TL`:** allow re-entry mientras trade previo todavía corre → viola anti-clustering (alpha decay) + serial correlation en P&L. NW horizon ~4h es lower bound inválido en cualquier caso.

**DOF cost:** rule es **deterministic en (TL, tier, floor=6, NW=4)**. No se grid-searchea. **0 DOF nuevo** vs holdout. Tier assignment es deterministic desde diagnóstico #281 ya publicado, no desde holdout fitting.

### 3.5 Per-symbol proposals (10 filas)

Aplicando rule (NW=4 dominado siempre por floor=6 → effectively `cooldown = max(TL, 6)`):

| Symbol | `cooldown_hours` | derivation | benchmark_anchor | confidence_tier |
|---|---:|---|---|---|
| **BTCUSDT** | **14** | TL=14h binds; ρ=−0.135 mild reversal, no extra buffer; no clustering risk past TL. | Hummingbot 3600s for 1H setups; cooldown=TL pattern | medium |
| **ETHUSDT** | **14** | TL=14h binds; ρ=+0.117 → T_half ≈ 0.32 bars ≪ 1h → cooldown=TL suficiente. | Mismo que BTC | medium |
| **ADAUSDT** | **6** | TL≈5h; floor 6h binds (parity legacy backtest); ρ=+0.008 effectively independent. | Freqtrade 5–6 candles | high |
| **AVAXUSDT** | **8** | TL uncertain 5–8h → upper bound 8h; ρ=−0.062 no carryover; t-stat solid 2.68 respeta TL upper. | Conservador dentro 5–8h band; > floor 6h | low |
| **DOGEUSDT** | **6** | basket TL default; floor binds; ρ=−0.149 (largest |neg|) → mild reversal already present; extender costaría edge. | Legacy global; Freqtrade 6-candle convention | high |
| **UNIUSDT** | **6** | basket TL; floor binds; ρ=+0.090 negligible. | Same | high |
| **XLMUSDT** | **6** | basket TL; floor binds; ρ=+0.155 (largest |pos|) — borderline Lo–MacKinlay critical 0.142 — pero N=190 + multiple-comparison across 10 → still inside noise band. | Same | medium |
| **PENDLEUSDT** | **6** | TL≈5h; floor 6h binds; ρ=+0.101 negligible; t=2.60 strong → keep gate minimal para preservar setup count. | Freqtrade 5–6 candles | high |
| **JUPUSDT** | **6** | basket TL; floor binds; ρ=−0.078 negligible; t=0.07 noise → cooldown es conservatism, not edge. | Same | high |
| **RUNEUSDT** | **6** | basket TL; floor binds; ρ=+0.021 ≈ 0; t=−0.18 noise. | Same | high |

### 3.6 Interaction con time_limit (critical analysis)

**B-like sólido (PENDLE, ADA), TL≈5h:** Decisión = **6h** (floor). Match-TL 5h violaría anti-clustering; TL × 1.5 = 8h sin anchor empírico → cosmético, costaría setup count.

**B-like parcial (BTC, ETH), TL≈14h:** Decisión = **14h** (= TL). El 6h global actual es **structurally broken** — re-entry posible 8h antes de que TL del trade previo se resuelva. 14h = primer valor logically consistent. TL × 1.5 = 21h sin anchor → unnecessary dead-time.

**Mundo C, sin clear TL:** Decisión = **6h** (legacy floor). Sin TL defendable per-symbol, no hay base para variar; preserva parity y evita inventar free params.

**AVAX outlier:** TL uncertainty 5–8h → cooldown 8h conservative pick; confidence low precisely porque TL itself uncertain.

**DSR-N integrity:** rule deterministic en (TL, tier, floor, NW). 0 grid expansion. Defendable como prior, no como search result.

**Confidence summary Área 3:** 6 high · 3 medium · 1 low.

---

## 4 · Decisions to Surface (cross-area)

Cosas donde la evidencia es ambigua, donde sources contradicen, o donde el mapeo generic → our-symbols requiere assumption explícita. **Surface, no se cierran unilateralmente.**

### D1 (Área 1) — BTC/ETH 14h vs basket-default 5h

Un global TL=5h (recomendación inicial #282) chopearía BTC/ETH winners (median 14h). Per-symbol 14h es la lectura honest del data, pero introduce heterogeneidad que complica el Triple Barrier benchmark. **Open question:** aceptar per-symbol heterogeneidad, o aceptar que BTC/ETH winners se trunquen bajo 5h unified.

### D2 (Área 1) — AVAX reclassification (B → C bajo SL widening, #281 §3)

8h splits diferencia entre t-stat strength (B-like) y SL widening evidence (C-like). **Open question:** treat AVAX as B-like (5–8h con TP) o C-like (5h, no TP, accept noise). 8h compromise puede no satisfacer ninguno.

### D3 (Área 1) — Mundo C local symbols (DOGE/JUP/RUNE/XLM/UNI) all 5h con low confidence

5/10 símbolos con basket-default driven por absence-of-evidence, no presence-of-evidence. Per #292 spec ("give value, low confidence" preferido). **Open question downstream:** si A.4 validation muestra que estos 5 siguen perdiendo bajo cualquier TL, el next loop debe revisitar basket reduction (deferred — out of scope here).

### D4 (Área 1) — PENDLE & ADA t=2.55–2.60 con 0 train winners

Edge statistically real pero train sample insufficient para observar winner-holding distribution. 5h es AFML mínimo pero puede ser tight si PENDLE/ADA winners actually behave como BTC/ETH. **Open question:** correr holdout sensitivity at {5, 8, 14h} para estos 2 antes de freeze.

### D5 (Área 1) — No OU/AR(1) half-life fit per-symbol

Anchors academic pero per-symbol τ no estimado (requeriría código). **Open question:** ticket follow-up para fit OU half-life sobre log-returns per-symbol on train antes de A.4 freeze.

### D6 (Área 1) — Framework anchor scale-mismatch

Hummingbot MACD-BB usa 55min sobre ≤5m bars; Freqtrade sample 60min sobre 5m. Ambos ≈ 12 bars. Aplicado a 1H system: "12 bars" = 12h, not 5h. El 5h elegido es **predictive-horizon anchor** (h=+5), no **framework-bar-multiplier anchor** (12h). **Open question:** which anchor wins para C-local symbols? 12h interpretation pushearía DOGE/JUP/RUNE/UNI/XLM up a 12h.

### D7 (Área 2) — Skip vs scale-down al hit del cap

Mecánicas viables cuando R-multiple sizing exceeds cap:
- **(a) Scale down**: clamp notional a `cap × bar_volume`; realized risk < 1% intended (R distorted).
- **(b) Skip**: `desired_notional > cap × bar_volume` → drop signal.
- **(c) Hybrid**: scale down si `desired_notional ≤ K × cap × bar_volume` (K=2–3); else skip.

Voto inicial del operador (#292): skip ("no entrar si no podés salir limpio"). Research subagent recomienda surface el hybrid. **Decisión pendiente del reviewer.**

### D8 (Área 2) — Cap reference window

Single 1H bar volume hace cap reactive a dead bars (overnight → tiny notional → trade siempre skip) y a volume spikes (false permission). Rolling 24H o 7-day median bar volume smoothearía. #292 specifica 1H bar — surfacing for explicit confirmation.

### D9 (Área 2) — "Small (worst)" floor cap 0.0015 partly arbitrario

Anchored loosely en Bouchaud "below 0.5% law breaks" (su 0.5% es daily POV, no bar POV). 0.0015 es practitioner heuristic para keep notional > exchange min order size para ~$50K capital. Si basket reduction estuviera en scope, PENDLE/XLM se removerían directamente; cap es la within-scope alternative.

### D10 (Área 2) — Mismatch cost model linearity (#277) vs sqrt reality (#279)

Caps propuestos correctos *given* costs scale steeply con participation. Si #279 sqrt v2 lands, mid-thin caps could relax (RUNE/ADA → 0.005). **Open question (versioning):** tie cap revision al rollout del v2 cost model.

### D11 (Área 2) — Empirical caveat de single-trade-bankruptcy artifact

Para thin symbols, observed `p50=p90=p99` (artifact #281 §1 caveat). Proposals para RUNE, ADA, UNI, XLM, PENDLE rest en cost_bps + tier-mapping, no en real participation distribution. **Cap itself es necessary infra para generar la data que validaría el cap** — circular en arranque.

### D12 (Área 2) — Holdout sequencing constraint

Per CLAUDE.md, A.4 must re-tune `atr_sl_mult/tp/be` over `[earliest, holdout_start - 1 bar]` antes de evaluating. Participation cap es sizing-side parameter que debe set *antes* de re-tuning, ya que cambia realized R distribution que el SL/TP/BE tuner ve. **Surfacing como sequencing constraint, no numeric decision.**

### D13 (Área 2) — Sin retail framework precedent

Freqtrade/Hummingbot/Jesse no shippean per-symbol POV cap. Implementar uno es **novel control** — incrementa implementation risk slight (no reference impl).

### D14 (Área 3) — Per-symbol vs uniform cooldown

Pure 6h global = max-parity option (0 DOF, 0 new params). Tabla propuesta introduce variation only via TL (BTC/ETH 14h, AVAX 8h, otros 6h), so DOF effective beyond TL grid es **0**. **Open question:** si reviewer prioritiza parity-above-all, fall back a 6h global y aceptar BTC/ETH "cooldown < TL" pathology.

### D15 (Área 3) — XLM lag-1 ρ=+0.155 borderline

Único lag-1 AC que flirta con Lo–MacKinlay 5% critical (~0.142 a N=190). Defensible (a) hold at 6h (current proposal) since multiple-comparison across 10, o (b) bump a 8h one-off. **Surfaced.**

### D16 (Área 3) — `TL × 1.5` rejection

Pivot plan A7 mencionó "8h (TL × 1.5)" para PENDLE/ADA como opción. Aquí rejected: ningún Freqtrade/Hummingbot/academic source endorses multiplicative buffer; ρ ≈ 0 a h=+5 → no statistical justification; costaría setup count para high-conviction tier. **Si pivot plan still wants it, requiere su own justification.**

### D17 (Área 3) — Volatility autocorrelation 0.66–0.72 lag-1 alta

Pero es *return* AC la que importa para entry-cooldown. Vol-AC matters para sizing en high-vol regimes — **out of scope, flagged for sizing review**.

---

## 5 · Tabla maestra (30 valores consolidados)

| Symbol | Cluster | `time_limit_hours` | `max_participation_rate` | `cooldown_hours` |
|---|---|---:|---:|---:|
| BTCUSDT | B-like parcial | 14 (high) | 0.010 (high) | 14 (medium) |
| ETHUSDT | B-like parcial | 14 (medium) | 0.010 (high) | 14 (medium) |
| ADAUSDT | B-like sólido | 5 (medium) | 0.003 (low-medium) | 6 (high) |
| PENDLEUSDT | B-like sólido | 5 (medium) | 0.0015 (low) | 6 (high) |
| AVAXUSDT | Mundo C local | 8 (medium-low) | 0.005 (medium) | 8 (low) |
| DOGEUSDT | Mundo C local | 5 (low) | 0.005 (medium) | 6 (high) |
| UNIUSDT | Mundo C local | 5 (low) | 0.002 (low-medium) | 6 (high) |
| XLMUSDT | Mundo C local | 5 (low-medium) | 0.0015 (low) | 6 (medium) |
| JUPUSDT | Mundo C local | 5 (low) | 0.005 (medium) | 6 (high) |
| RUNEUSDT | Mundo C local | 5 (low) | 0.003 (medium) | 6 (high) |

**Confidence aggregate (30 valores):**

- Área 1 (TL): 1 high · 4 medium · 1 medium-low · 1 low-medium · 4 low.
- Área 2 (cap): 2 high · 4 medium · 2 low-medium · 2 low.
- Área 3 (cooldown): 6 high · 3 medium · 1 low.

**Total:** 9 high · 11 medium · 5 low-medium · 7 low (de 30).

**Lectura honest:** los 4 símbolos viables (BTC, ETH, PENDLE, ADA) tienen los anchors más fuertes en time_limit y cooldown pero **los más débiles en participation cap** (ADA low-medium, PENDLE low). Eso refleja que el cap es el grado de libertad menos validado por literatura per-symbol — el structural fix lo necesita pero la confidence se ganará empíricamente post-PR.

---

## 6 · Estado de completitud

| Área | Estado | Notas |
|---|---|---|
| Área 1 — `time_limit_hours` | ✅ completa | 10 símbolos × 4 cols (proposed/derivation/anchor/tier); calibration rule explícita; 6 decisions surfaced |
| Área 2 — `max_participation_rate` | ✅ completa | 10 símbolos; tier mapping + per-symbol; 7 decisions surfaced (incluida sub-decisión skip-vs-scale-down) |
| Área 3 — `cooldown_hours` | ✅ completa | 10 símbolos; deterministic rule (no grid); interaction con TL analizada; 4 decisions surfaced |
| §4 Decisions to Surface | ✅ 17 decisiones cross-area | D1–D6 Área 1; D7–D13 Área 2; D14–D17 Área 3 |
| §5 Tabla maestra | ✅ 30 valores consolidados | con tier per cell |

**Time spent:** ~5h research (3 parallel agents) + writing.

**No se ejecutó (out of scope):**
- Backtests para "verificar" valores (delegated to los 3 PRs estructurales).
- OU/AR(1) half-life fits per-symbol (D5 — surfaced as follow-up ticket candidate).
- Code changes a `config.json` o cualquier production file.

---

## 7 · References (consolidated)

### Issues / PRs
- [#281](https://github.com/sssimon/trading-spacial/issues/281) — A.0.2.diag deep-dive (cluster classification + forward-return + holding-period data)
- [#282](https://github.com/sssimon/trading-spacial/issues/282) — exit logic benchmark crypto frameworks
- [#277](https://github.com/sssimon/trading-spacial/issues/277) — A.0.2 realistic transaction costs
- [#279](https://github.com/sssimon/trading-spacial/issues/279) — participation-rate cap on R-multiple sizing
- [#250](https://github.com/sssimon/trading-spacial/issues/250) — A.4 epic (re-evaluate parameters against holdout)
- [#292](https://github.com/sssimon/trading-spacial/issues/292) — esta research

### Internal docs
- [`docs/superpowers/specs/es/2026-04-30-a02-diag-deep-dive.md`](../specs/es/2026-04-30-a02-diag-deep-dive.md) — diagnóstico
- [`docs/superpowers/plans/2026-05-01-a4-strategic-pivot-plan.md`](../plans/2026-05-01-a4-strategic-pivot-plan.md) — pivot plan
- [`docs/superpowers/research/2026-04-30-exit-logic-benchmark-crypto.md`](2026-04-30-exit-logic-benchmark-crypto.md) — exit logic benchmark (no en main todavía, branch `research/exit-logic-benchmark-281`)

### Frameworks (Área 1 + Área 2 + Área 3)
- Freqtrade: [docs](https://www.freqtrade.io/en/stable/), [strategies repo](https://github.com/freqtrade/freqtrade-strategies), [sample_strategy.py](https://github.com/freqtrade/freqtrade/blob/develop/freqtrade/templates/sample_strategy.py), [Solipsis-v1](https://github.com/werkkrew/freqtrade-strategies/blob/main/strategies/archived/Solipsis-v1.py), [HourBased](https://github.com/freqtrade/freqtrade-strategies/blob/main/user_data/strategies/HourBasedStrategy.py), [Protections](https://www.freqtrade.io/en/stable/includes/protections/)
- Hummingbot: [PositionExecutor](https://hummingbot.org/v2-strategies/executors/positionexecutor/), [MACD-BB blog](https://hummingbot.org/blog/directional-trading-with-macd-and-bollinger-bands/), [walkthrough](https://hummingbot.org/v2-strategies/walkthrough-controller/), [examples](https://hummingbot.org/v2-strategies/examples/), [inventory skew](https://hummingbot.org/strategies/v1-strategies/strategy-configs/inventory-skew/)
- Jesse: [docs](https://docs.jesse.trade/docs/backtest)
- NautilusTrader: [Orders](https://nautilustrader.io/docs/latest/concepts/orders/)
- VectorBT: [Stop signals](https://deepwiki.com/polakowo/vectorbt/4.2-stop-based-exit-signals)

### Academic / quant lit
- Almgren & Chriss (2001), "Optimal Execution" — [PDF](https://www.smallake.kr/wp-content/uploads/2016/03/optliq.pdf), [calibration commentary](https://quantum-blog.medium.com/almgren-chriss-optimal-execution-model-5a85b66555d2)
- Bouchaud, "Trades, Quotes, and Prices" (Cambridge, 2018) — [book](https://www.cambridge.org/core/books/trades-quotes-and-prices/A06CE8CD7F1E40E1A8DCD71EDFD4FB44), [Substack square-root](https://bouchaud.substack.com/p/the-square-root-law-of-market-impact)
- Donier & Bonart (2014), "Million Metaorder BTC" — [arxiv](https://arxiv.org/abs/1412.4503), [published](https://www.worldscientific.com/doi/10.1142/S2382626615500082)
- Easley et al., "Microstructure and Market Dynamics in Crypto" — [SSRN PDF](https://stoye.economics.cornell.edu/docs/Easley_ssrn-4814346.pdf)
- López de Prado, *Advances in Financial Machine Learning* (Wiley, 2018), Ch. 3 Triple Barrier — [Reasonable Deviations notes](https://reasonabledeviations.com/notes/adv_fin_ml/), [Quantreo](https://www.newsletter.quantreo.com/p/the-triple-barrier-labeling-of-marco), [mlfin.py](https://mlfinpy.readthedocs.io/en/latest/Labelling.html)
- Moskowitz, Ooi & Pedersen (2012), "Time Series Momentum", *JFE* — [PDF](http://docs.lhpedersen.com/TimeSeriesMomentum.pdf), [DOI](https://doi.org/10.1016/j.jfineco.2011.11.003)
- Jegadeesh & Titman (1993), "Returns to Buying Winners and Selling Losers", *JF* — [PDF](https://www.bauer.uh.edu/rsusmel/phd/jegadeesh-titman93.pdf), [DOI](https://doi.org/10.1111/j.1540-6261.1993.tb04702.x)
- Lo & MacKinlay (1988), variance-ratio test — [PDF mirror](https://finance.martinsewell.com/stylized-facts/dependence/LoMacKinlay1988.pdf)
- Newey-West HAC estimator — [Wikipedia](https://en.wikipedia.org/wiki/Newey%E2%80%93West_estimator)
- Avellaneda & Lee (2010), "Statistical Arbitrage" — [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1153505)
- OU half-life: [flare9x](https://flare9xblog.wordpress.com/2017/09/27/half-life-of-mean-reversion-ornstein-uhlenbeck-formula-for-mean-reverting-process/), [arbitragelab](https://hudson-and-thames-arbitragelab.readthedocs-hosted.com/en/latest/cointegration_approach/half_life.html)

### Crypto-specific empirical
- Shen et al. (2022), "Bitcoin intraday time series momentum" — [Wiley](https://onlinelibrary.wiley.com/doi/abs/10.1111/fire.12290)
- "Intraday return predictability in cryptocurrency markets" (2022) — [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S1062940822000833)
- "Stylized Facts of High-Frequency Bitcoin Time Series" — [arxiv 2402.11930](https://arxiv.org/html/2402.11930v2)
- Tartakovsky et al. (2020), "Auto-Correlation of Returns in Major Crypto" — [arxiv 2003.13517](https://arxiv.org/pdf/2003.13517)
- "Intraday and daily dynamics of cryptocurrency" — [ScienceDirect](https://www.sciencedirect.com/science/article/pii/S1059056024006506)
- Verma, Sharma & Sam (2022), "Random Walk in Cryptocurrency" — [Sage](https://journals.sagepub.com/doi/10.1177/23197145221101238)

### Crypto-specific institutional
- Talos: [market impact](https://www.talos.com/insights/understanding-market-impact-in-crypto-trading-the-talos-model-for-estimating-execution-costs), [VWAP/TWAP](https://www.talos.com/insights/vwap-or-twap-for-crypto-execution-a-market-impact-perspective)
- Cube Exchange: [VWAP order](https://www.cube.exchange/what-is/vwap-order)
- Kaiko: [moving markets](https://research.kaiko.com/insights/moving-markets-liquidity-and-large-sell-orders), [concentration](https://research.kaiko.com/insights/the-crypto-liquidity-concentration-report), [asset ranking](https://research.kaiko.com/insights/liquidity-lowdown-asset-ranking)
- Binance: [LiquidityBoost program](https://www.prnewswire.com/news-releases/binance-creates-first-altcoin-focused-spot-liquidity-program-to-meet-demand-for-deeper-liquidity-and-more-market-diversity-302473092.html)

### Practitioner / blog
- MagPi AI — [Signal half-life](https://medium.com/@magpiai/stop-guessing-the-quant-science-of-signal-half-life-and-market-context-ba934a13dd21)
- Maven Securities — [Alpha decay](https://www.mavensecurities.com/alpha-decay-what-does-it-look-like-and-what-does-it-mean-for-systematic-traders/)
- Quantpedia — [Consistent Momentum](https://quantpedia.com/strategies/consistent-momentum-strategy)
