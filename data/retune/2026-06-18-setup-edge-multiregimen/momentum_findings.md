# Findings — ¿la lectura MOMENTUM de musikito tiene edge?

_Regla: breakout sobre máx de 20d + volumen ≥1.5× + sobre SMA20. 386 símbolos, panel 455068 filas. hits mínima=10667, conjunta=6418._

## Veredicto (regla-conjunta vs B1, mediana max_fwd_14d, Mann-Whitney one-sided)

- (a) **Global**: SÍ (Δ>0, p<0.05)
- (b) **En alt-bull** (breadth≥0.6): SÍ (Δ>0, p<0.05)
- (c) **Fuera de alt-bull**: SÍ (Δ>0, p<0.05)

## Regla-conjunta (momentum completo) vs B1 por régimen

- **Global**: setup n=6418 mediana max_fwd_14d=20.0% win15=60.2% | B1 n=17404 mediana=17.3% win15=54.8% | Δmediana=2.8% (p=2.749e-20)
- **alt-bull**: setup n=5161 mediana max_fwd_14d=21.4% win15=62.9% | B1 n=13633 mediana=19.7% win15=59.1% | Δmediana=1.8% (p=2.142e-09)
- **neutral**: setup n=427 mediana max_fwd_14d=17.6% win15=56.9% | B1 n=1281 mediana=13.3% win15=46.2% | Δmediana=4.3% (p=0.0003131)
- **bear**: setup n=830 mediana max_fwd_14d=13.0% win15=45.2% | B1 n=2490 mediana=10.1% win15=35.9% | Δmediana=2.8% (p=2.189e-10)

## Regla-mínima (solo breakout) vs B1

- **Global**: setup n=10667 mediana max_fwd_14d=17.8% win15=55.7% | B1 n=27971 mediana=16.1% win15=52.2% | Δmediana=1.7% (p=1.691e-16)
- **alt-bull**: setup n=8231 mediana max_fwd_14d=19.5% win15=59.2% | B1 n=20730 mediana=18.3% win15=56.6% | Δmediana=1.2% (p=5.078e-08)
- **neutral**: setup n=794 mediana max_fwd_14d=16.2% win15=53.0% | B1 n=2351 mediana=13.4% win15=46.3% | Δmediana=2.8% (p=0.0009679)
- **bear**: setup n=1642 mediana max_fwd_14d=10.6% win15=39.8% | B1 n=4890 mediana=9.9% win15=36.1% | Δmediana=0.6% (p=0.0001443)

## Caveats

- Sesgo de supervivencia: cache solo de símbolos vivos hoy; niveles absolutos inflados para setup y baseline por igual; el DELTA sigue informativo.
- Retorno en USDT incluye beta de BTC.
