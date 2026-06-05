"""Programa E1 — T0 ingest del universo amplio. Parámetros congelados.

Pre-registro: docs/superpowers/specs/2026-06-05-programa-t0-ingest-universo.md
(commiteado ANTES de enumerar el listing). Cambiar la regla = re-abrir T0
contra el spec, no editar aquí.
"""

# Ventana retroactiva (decisión Samuel 2026-06-05): incluye el estrés 2022.
WINDOW_START = "2021-01"
WINDOW_END = "2026-05"
TIMEFRAME = "1h"

# Binance Vision: listing S3 (XML) + bulk zips. El listing RETIENE símbolos
# delistados — esa es la defensa anti-survivorship de la regla del universo.
VISION_LISTING = "https://data.binance.vision/?delimiter=/&prefix="
SPOT_KLINES_PREFIX = "data/spot/monthly/klines/"
VISION_DOWNLOAD = "https://data.binance.vision/"

# Exclusión declarada (spec §Regla 3): stablecoins y fiat — no son cripto-beta.
EXCLUDED_BASES = frozenset({
    "USDC", "BUSD", "TUSD", "FDUSD", "DAI", "PAX", "USDP", "SUSD",
    "UST", "USTC", "EUR", "GBP", "AUD", "BRL", "TRY", "RUB", "UAH",
    "NGN", "ZAR", "IDRT", "BIDR", "VAI", "AEUR",
})
LEVERAGED_SUFFIXES = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")

# Cobertura: SE REPORTA, no excluye (spec §Regla 6).
COVERAGE_OK_THRESHOLD = 0.95

PROGRAM_DB = "data/program_ohlcv.db"
OUTPUT_DIR = "data/retune/2026-06-05-programa-t0-ingest"
