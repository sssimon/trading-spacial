"""Pre-registered, IRREVOCABLE parameters. Changing any of these = a NEW experiment
with its own pre-registration (spec §3, Adrian F-1). Do not tune to results."""

ATR_PERIOD = 22
ATR_TF = "1h"
CHANDELIER_MULT = 3.0
GIVEBACK_FRAC = 0.38
MAX_HOLD_H = 200.0
PRICE_TF = "5m"
BOOTSTRAP_N = 10000
BOOTSTRAP_SEED = 20260603

# 8 symbols with full 5m coverage AND closed positions (spec §2).
KEEP_SYMBOLS = (
    "BTCUSDT", "ETHUSDT", "RUNEUSDT", "XLMUSDT",
    "PENDLEUSDT", "UNIUSDT", "DOGEUSDT", "AVAXUSDT",
)

PAPA_DB = r"C:\Users\simon\Desktop\Papa\trading_backup_extracted\signals.db"
OHLCV_DB = "data/ohlcv.db"
OUTPUT_DIR = "data/retune/2026-06-03-arm-a-blind-exit"
