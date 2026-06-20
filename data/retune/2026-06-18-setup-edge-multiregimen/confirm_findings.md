# Findings — ¿el momentum sobrevive al exit REAL de musikito?

_Exit: escalera ['+15%', '+30%', '+50%', '+90%'] fracs [0.25, 0.25, 0.2, 0.15] + runner + piso -50%, horizonte 30d. De-dup 14d. n_setup=3133, n_B1=7097._

## realized_return (escalera + runner) — setup-momentum vs B1

| Régimen | setup mediana / media / win15 | B1 mediana / media / win15 | Δmedia |
|---|---|---|---|
| global | +6.7% / +6.3% / +40.8% (n=3133) | +3.7% / +4.8% / +35.9% (n=7097) | +1.6% |
| alt-bull | +9.8% / +9.2% / +44.3% (n=2326) | +7.5% / +8.2% / +40.7% (n=4676) | +1.0% |
| neutral | +0.2% / +2.3% / +37.2% (n=282) | -1.2% / +0.9% / +33.9% (n=846) | +1.4% |
| bear | -7.1% / -4.1% / +26.9% (n=525) | -4.4% / -3.2% / +23.0% (n=1575) | -0.9% |

## Stress de supervivencia

- **breakeven_p = 1.5%** — si esa fracción de los breakouts hubiera muerto (retorno −100%, no en el cache), la MEDIA del setup igualaría a la de B1. FRÁGIL (pocas muertes ocultas lo borran).

## Veredicto

- Global: Δmediana realized = +3.0%, Δmedia = +1.6%.
- Con el exit REAL (no el stop −12%), ¿el momentum supera a B1? SÍ en mediana; SÍ en media.

## Caveats
- realized_return = escalera de targets (fill intrabar optimista) + runner al cierre H + piso -50%.
- Supervivencia: el cache solo tiene vivos hoy; el breakeven_p estima cuántas muertes ocultas igualarían la media a B1.
- Retorno en USDT incluye beta de BTC.