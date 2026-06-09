# VERDICT: FAIL — el Fear&Greed (contrarian) no porta edge extraíble net-of-v3

Sondeo de tractabilidad pre-celda C1 (candidato Edición 2). Criterios congelados
ANTES de correr: `PREREGISTRO.md` (commit `04dfab8`). Una sola corrida, determinista
(seed 42). NO se tocó `data/holdout/`.

## Resultado (POST-2021, bien powered: 127 episodios pooled)

| Leg | n_trades | n_episodios | mean $/trade | CI95 ($) | gross dir. | gate |
|---|---|---|---|---|---|---|
| **POOLED** | 5840 | **127** | **−21.90** | **[−39.14, −5.11]** | −1.64% | **FAIL** |
| fear→long | 3899 | 78 | −9.12 | [−18.83, +2.80] | −0.36% | FAIL (CI incluye cero) |
| greed→short | 1941 | 49 | −47.58 | [−80.66, −0.93] | −4.19% | FAIL (pierde signif.) |

PRE-2021: UNDERPOWERED (7 episodios, CI incluye cero; el fear→long PRE = 1 solo
episodio, la recuperación de marzo-2020, n=1 sin valor).

## Qué significa

La estrategia contrarian del F&G (LONG en miedo extremo / SHORT en codicia extrema,
H=5d, $1000, net-of-v3) **pierde plata de forma significativa post-2021**. El CI95
del pooled excluye cero **por el lado negativo**.

Y el punto duro: **es gross-flat-o-negativa incluso a costo cero** en la dirección
contrarian (fear→long gross −0.36%, greed→short gross −4.19%). No es "había edge y
el costo se lo comió" — es que **no hay edge contrarian** en el sentiment, igual que
la celda 1 (direccional) era gross-flat. El costo solo lo empeora.

## Observación post-hoc (NO es una puerta — leer con cuidado)

El greed→short tiene gross −4.19%, lo que implica que tras días de codicia extrema
el precio **subió** en promedio (5d). Un momentum-long-greed habría tenido gross
positivo. PERO esto **NO se persigue**, por tres razones:
1. Es **post-hoc** — el pre-registro era contrarian; flipear la dirección tras ver
   el resultado es data-dredging (prohibido por el runbook F).
2. Es exactamente el **Tipo A** ("estoy greedy → presiono") = la falacia del jugador
   que el proyecto rechaza estructuralmente (q3_pass:false; J2 06-07/08).
3. Está casi seguro **confundido con régimen**: los extremos de codicia se agrupan
   en bull-markets; "long cuando hay codicia" ≈ "long en bull-market" — los 49
   episodios están clusterizados, no son evidencia de timing.

## Decisión

**NO abrir celda de sentiment en Edición 2.** El sondeo respondió su pregunta:
"momentos greedy" (en su forma contrarian medible) no porta ventaja extraíble net
de costos. La negativa del copiloto honesto de Mercado queda **vindicada** — no es
deflación arbitraria; genuinamente no hay edge de sentiment que mostrar.

## Condición de reapertura

Solo con una hipótesis específica, pre-registrada, NO contrarian-ni-Tipo-A y
**de-tendenciada por régimen** (que separe "señal de sentiment" de "estar long en
bull-market"), con su poder declarado sobre episodios independientes. "Probar la
dirección momentum a ver si pasa" NO califica — es el data-dredging que el marco §8.5
prohíbe.
