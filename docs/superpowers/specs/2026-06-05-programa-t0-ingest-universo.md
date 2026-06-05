# Programa E1 — T0: ingest del universo amplio (spot 1h)

**Fecha:** 2026-06-05 · **Track:** T0 (junta 2026-06-04, decisión "data ancha YA")
**Pre-registro:** este spec se commitea ANTES de enumerar el listing de Binance
Vision. La regla del universo no puede ver la data que selecciona.

## Qué es T0 (y qué no es)

T0 es **infraestructura de data**, no un estudio: descarga retroactiva bulk de
klines spot 1h para el universo amplio, habilitando las celdas 3
(cross-sectional, BLOQUEADA-POR-DATA) y 4 (stat-arb). T0 no corre análisis, no
emite veredictos, no tiene gates. La selección fina de símbolos para cada
estudio F es decisión de ESE estudio (pre-registrada en su spec) — T0 entrega el
panel total y el reporte de cobertura.

## Parámetros congelados

| Parámetro | Valor | Justificación |
|---|---|---|
| Fuente | Binance Vision `data/spot/monthly/klines/<SYM>/1h/` | misma infra que funding-carry (cero infra nueva) |
| Ventana | `2021-01` → `2026-05` (65 meses) | incluye el régimen de estrés 2022 (LUNA/FTX); decisión Samuel 2026-06-05 |
| Timeframe | 1h | matchea perp_klines de funding.db y el grano de señal |
| Destino | `data/program_ohlcv.db` (gitignoreado) | mundo separado de `data/ohlcv.db` (producción) y de `data/holdout/` |
| Esquema | `spot_klines(symbol, open_time, open, high, low, close, volume, PK(symbol, open_time))` | OHLCV completo: stat-arb puede necesitar rangos, no solo closes |

## Regla del universo (pre-registrada — el corazón de T0)

1. **Enumeración:** listing S3 de Binance Vision bajo el prefijo
   `data/spot/monthly/klines/` (XML, paginado). NO un snapshot de
   ticker/exchangeInfo de hoy: el listing retiene símbolos delistados — esta es
   la defensa contra el sesgo de supervivencia.
2. **Filtro de forma:** se conservan símbolos que terminan en `USDT`.
3. **Exclusión de no-cripto-beta (lista declarada):** se excluye el símbolo si
   su base está en `EXCLUDED_BASES` = stablecoins y fiat
   (`USDC, BUSD, TUSD, FDUSD, DAI, PAX, USDP, SUSD, UST, USTC, EUR, GBP, AUD,
   BRL, TRY, RUB, UAH, NGN, ZAR, IDRT, BIDR, VAI, AEUR`) o si termina en
   sufijo de token apalancado (`UPUSDT, DOWNUSDT, BULLUSDT, BEARUSDT`).
4. **Condición de entrada al panel:** el símbolo tiene el archivo mensual de
   `2021-01` presente en el listing de su prefijo 1h (existía al inicio de la
   ventana). Los listados después de 2021-01 NO entran al panel de la Edición 1
   (panel ~balanceado para stat-arb); quedan enumerados en el artefacto como
   `listed_later` por si un estudio los necesita con justificación propia.
5. **Los delistados mid-window SE QUEDAN** en el panel, con su `last_month`
   registrado. Excluirlos sería reintroducir por la puerta de atrás el
   survivorship que la regla existe para impedir.
6. **Piso de cobertura: SE REPORTA, NO SE EXCLUYE.** El reporte marca
   `coverage_ok = (meses presentes / meses esperados mientras listado) ≥ 0.95`
   por símbolo. La exclusión por cobertura es decisión del estudio consumidor,
   no del ingest — el ingest es total.

## Artefactos

- `data/retune/2026-06-05-programa-t0-ingest/universe.json` — la salida de la
  regla: panel (con `first_month`, `last_month`, `coverage_ok`, flags),
  excluidos con causa (`excluded_base`, `leveraged`, `listed_later`), y la
  procedencia (fecha de enumeración, conteos por etapa del filtro).
- `data/retune/2026-06-05-programa-t0-ingest/coverage.json` — filas ingestadas
  por símbolo/mes tras la descarga, huecos detectados.
- `data/program_ohlcv.db` — la data (gitignoreado, reproducible re-corriendo el
  ingest: la fuente es bulk inmutable de Binance Vision).

## Holdout

T0 **descarga** data pública; no evalúa nada. El candado de `data/holdout/`
(non-negotiable #2) y A.4-3/#322 (non-negotiable #3) no se tocan: ningún módulo
de T0 lee `data/holdout/` ni llama `simulate_strategy`/`open_holdout`.
Precedente de ventana: funding-carry corrió hasta 2026-05 con roast aprobado —
lo protegido es el dataset del holdout y la evaluación A.4, no el calendario.

## Negative space

- NO se modifica `tools/funding_carry/` (sus constantes son IRREVOCABLES; T0 es
  paquete nuevo `tools/program_ingest/` que clona el patrón de red).
- NO se escribe en `data/ohlcv.db` ni se toca `data/_storage.py`.
- NO se hace captura continua en T0 (eso es un follow-up con su propia decisión
  de scheduling; el bulk retroactivo es lo que desbloquea las celdas 3-4).
- NO se selecciona universo "bueno" — el ingest es total y la selección es de
  los estudios.
- NO se registran trials (T0 no es estudio; el registry no se toca).
