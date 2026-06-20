# Evidencia: la lectura MOMENTUM de musikito SÍ tiene señal (la pregunta abierta, respondida)

**Fecha:** 2026-06-20
**Estado:** brief para el roster. Cierra la última pregunta abierta del arco — y reabre una.
**Origen:** Samuel eligió el lente-momentum del backlog POST-SHIP.

---

## 0. Por qué

El estudio multi-régimen midió la lectura-DEBILIDAD (features de entrada reverse-engineadas: parte
baja del rango, sobreventa, bajo SMA) y la refutó: **no tiene edge en ningún régimen.** SP2 construyó
el detector sobre esa lectura. PERO el template DECLARADO del canal era **momentum/breakout** (zona +
targets agresivos + runner, "Risk Trade"). Nunca medimos la lectura-momentum. Este estudio la mide,
mismo harness (`data/retune/2026-06-18-setup-edge-multiregimen/`, cache de 386 símbolos, panel 455k
filas, baselines matcheadas, Mann-Whitney).

**Regla-momentum (congelada):** vivo AND breakout sobre el máx de 20d AND volumen 3d ≥ 1.5× el de
30d AND sobre la SMA20. n=**6.418** hits (regla conjunta). Control B1 = vivos que NO rompieron,
matcheados por fecha.

---

## 1. El dato — y la divergencia crítica

### 1.1 Métrica optimista (`max_fwd_14d` — el mejor pico en 14 días): momentum GANA en todo

| Régimen | Setup mediana | B1 | Δ | p | win15 setup/B1 |
|---|---|---|---|---|---|
| Global | **20.0%** | 17.3% | +2.8% | 2.7e-20 | 60.2% / 54.8% |
| alt-bull | **21.4%** | 19.7% | +1.8% | 2e-9 | 62.9% / 59.1% |
| neutral | **17.6%** | 13.3% | +4.3% | 3e-4 | 56.9% / 46.2% |
| bear | **13.0%** | 10.1% | +2.8% | 2e-10 | 45.2% / 35.9% |

**Lectura 1:** las coins en breakout alcanzan picos MÁS ALTOS que las que no rompen, en TODOS los
regímenes, con significancia masiva (n=6.418, p~1e-20). El 60% pega +15% en 14 días vs 55% del azar.
**Esto es señal real de selección — justo lo que la lectura-debilidad NO tenía.** Refuta el "no
existe NINGÚN edge per-coin".

### 1.2 Métrica realista (`rule_return`, +20% TP / −12% SL / 14d): momentum PIERDE

| Régimen | Setup rule_return | B1 |
|---|---|---|
| Global | **−12.0%** (toca el stop) | −2.1% |
| alt-bull | **−12.0%** | −0.6% |
| neutral | **−12.0%** | −6.6% |
| bear | **−12.0%** | −7.2% |

**Lectura 2 (la incómoda):** con un stop fijo de −12%, el trade-momentum MEDIANO **toca el stop** —
peor que la baseline. ¿Por qué la divergencia con §1.1? Porque los breakouts son **volátiles en
ambas direcciones**: el precio pega un pico arriba (max_fwd alto) PERO también latiguea abajo y toca
el −12% ANTES de llegar al +20%. El edge vive en el **pico de subida** (max-favorable-excursion), no
en un buy-and-hold con stop ajustado.

---

## 2. La síntesis honesta

- **Hay señal de momentum REAL** que la debilidad no tenía. "No existe ningún edge per-coin" queda
  **refutado.**
- Pero es una señal de **excursión favorable** (el pico), no de retorno realizable con un stop.
  Cosechable solo con el estilo REAL de musikito: **targets agresivos + vender en el pico + runner**
  (NO un stop ajustado). Su método declarado y su exit calzan exactamente con dónde vive el edge.
- O sea: las features de ENTRADA que reverse-engineamos (debilidad) eran un **red herring** — el
  edge nunca estuvo en comprar debilidad; estuvo en cazar el breakout + salir rápido en la subida.

## 3. Caveats (no sobre-interpretar)

- **Sesgo de supervivencia, y aquí corta MÁS contra el momentum:** un breakout que bombeó y murió
  (delisted, pump-and-dump) está EXCLUIDO del cache. Eso infla el edge aparente del momentum más que
  el de la baseline. El edge real es probablemente MENOR; el `rule_return` que toca el stop es la
  señal robusta de que es difícil de tradear.
- `max_fwd` es optimista (asume timing perfecto del pico). Pero los targets agresivos de musikito se
  acercan a cosechar el pico más que un hold de 14d.
- Test confirmatorio, NO p-hacking: momentum era la hipótesis PRE-REGISTRADA (el método declarado del
  canal), no un barrido de datos.

## 4. Las preguntas para el roster

**P1 — ¿Reabre esto "Valles elige ganadores"?** Acabamos de reorientar a "exhibe estado, no elige".
El momentum SÍ tiene señal. Pero el `rule_return` dice que es difícil de cosechar y la supervivencia
lo infla. ¿Es edge tradeable o edge de papel?

**P2 — ¿El detector está al revés?** SP2 construyó "parte baja del rango" (debilidad, sin edge). El
momentum (breakout) tiene la señal. ¿Hay que invertir el detector — de "parte baja" a "breakout"? ¿O
exhibir AMBOS como hechos? Implicaciones para SP2 ya shippeado.

**P3 — ¿Cómo se honra la doctrina anti-veredicto si ahora SÍ hay una señal con edge?** Exhibir
"breakout + volumen" como hecho es seguro; pero si el sistema sabe que eso tiene edge medido, ¿puede
seguir diciendo "no firmo veredicto"? ¿O el edge se expresa solo en la pieza de régimen (cuándo) +
los targets/runner (cómo cosechar), nunca en "compra esta"?

---

## 5. RESOLUCIÓN — el estudio de confirmación (2026-06-20)

El roster (junta 2026-06-20) dictaminó que el "edge" de §1.1 era EXCURSIÓN, no retorno realizable, y
pidió un estudio de confirmación: exit REAL de musikito + de-dup + stress de supervivencia. Hecho
(`CONFIRM_METODOLOGIA.md`, `confirm_study.py`, `confirm_findings.md`):

- **De-dup:** los hits cayeron de 6.418 → **3.133** (confirma la autocorrelación que infló el n).
- **Con el exit real** (escalera +15/+30/+50/+90% + runner, piso −50%, NO un stop −12%): el momentum
  pasa de "toca el stop" a un edge **positivo pero diminuto** — Δmedia realized = **+1.6%** global
  (+1.0% alt-bull, +1.4% neutral, **−0.9% bear**). El exit de musikito SÍ servía; la selección apenas.
- **Stress de supervivencia: `breakeven_p = 1.5%`.** Si apenas el 1.5% de los breakouts hubiera
  muerto (−100%, excluidos del cache), la media del setup igualaría a B1. Los alts de bajo cap en
  breakout mueren muy por encima del 1.5% → el edge diminuto está **enteramente dentro del margen de
  supervivencia.** Confirma a Serrano: la supervivencia probablemente explica TODO.

**Veredicto final:** NO hay edge de selección per-coin robusto — ni debilidad (sin señal) ni momentum
(frágil, dentro del ruido de supervivencia, se invierte en bear). Lo real de musikito fue el **exit**
(vender en el pico) + el **timing de ciclo** (alt-season), NUNCA la elección de coin.

- **P1:** No reabre "elige ganadores". Confirmado con número.
- **P2:** El detector NO se invierte (el breakout también es frágil). SP2 queda intacto.
- **P3:** La doctrina se sostiene; AC7 de SP2 sigue honesto. Producción NO se toca.

**Pregunta cerrada.** El arco de la reorientación (SP1+SP2+SP3) se mantiene entero.
