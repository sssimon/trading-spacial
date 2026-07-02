# Curar el azar — Veredicto: **LA CURA FUNCIONA (positivo, robusto, walk-forward)**

Tesis (de Samuel): la selección de entrada es nula (probado), así que **no se vence el azar — se cura.**
Aceptar la entrada random y extraer valor de la GESTIÓN: exit asimétrico (escalera) + circuit breaker
(kill_switch_v2 que re-arma) + diversificación. `curar_azar.py`, panel anti-survivorship, walk-forward
train(2021-23)/validate(2024→2025-04), score = Calmar (CAGR/maxDD). No toca holdout.

## Resultado

| | TRAIN | VALIDATE (OOS) |
|---|---|---|
| **NAIVE** (random, SIN cura) | 0.43x, **84% DD** | ~1.0x, 47-52% DD (plano/perdedor) |
| **CURADO** (random + cura completa) | 1.27x, 20% DD | **1.16-1.6x, 16-27% DD, Calmar +0.6 a +2.7** |

- **7 de 8** de las mejores configs de train siguen **positivas** en validate → generaliza, no fitea.
- La config robusta (M=20, kill-switch agresivo re-armante, diversificado): **5/5 semillas positivas**
  en validate (term 1.47-1.59x, med 1.52, Calmar +1.5 a +2.6). No fue una rotación afortunada.
- Dentro del MISMO validate, la cura le gana al naive (1.5x vs 1.0x, mitad del DD) → es alfa de
  GESTIÓN, no beta del bull (a diferencia del filtro momentum, que era beta falsa por selección).

## Por qué funciona

No hay selección — la entrada es una moneda al aire. El valor sale de tres palancas de manejo:
1. **Convexidad del exit** (escalera): pérdida acotada a −50%, ganancia con runner → skew positivo.
2. **Circuit breaker re-armante**: corta el sangrado de drawdown (clusters correlacionados), re-entra
   cuando pasa. El pico RODANTE (180d) evita el artefacto de congelar-para-siempre.
3. **Diversificación** (M=20): ninguna moneda hunde el libro.

Es cosecha de convexidad sobre draws aleatorios diversificados, con un freno de drawdown. Clase de
estrategia conocida que funciona — no fantasía.

## Endurecimiento (`harden_cura.py`) — caveat de bear CERRADO

- **(A) Multi-fold:** la config robusta en 5 cortes × 5 semillas = **25/25 positivas**. No es artefacto
  de un solo split; aguanta en todo el eje temporal (med por corte 1.14-1.74x, Calmar +0.47 a +1.55).
- **(B) Stress-test de bear OOS:** la cura aplicada al 2021-2022 (incluye el bear 2022), con config
  elegida SIN ver 2022 → **CURADO 1.48x, maxDD 9%** vs **NAIVE 0.54x, maxDD 71%**. La protección de
  bear **generaliza out-of-sample** — "corta cuando pierdes" es regla general, no fit al 2022.

**Caveat que queda (chico):** sigue siendo **un solo bear histórico**. Lo probamos ciego y protegió,
pero un bear de forma distinta (goteo lento vs crash) es teoría no probada. En vivo, el slippage en un
crash empeoraría algo el 9%. No tumba la tesis; solo dice "no prometas el 9% exacto en el próximo bear".

## Conclusión

**La tesis de Samuel se valida.** El edge no está en escoger (nulo) sino en curar el azar vía gestión.
Contrasta con [[edge-mecanico-agotado-day-matched]]: eso enterró la SELECCIÓN; esto resucita la
GESTIÓN. La cura ES la estrategia; Valles + la mano del operador es la capa discrecional encima.
Siguiente: endurecer (multi-fold, stress-test del bear OOS invirtiendo el split, afinar knobs).
