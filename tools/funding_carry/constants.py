"""Pre-registered, IRREVOCABLE parameters. Changing any = a NEW experiment."""

# Candidate liquid universe (filtered to those with full coverage at ingest).
CANDIDATE_SYMBOLS = (
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT", "AVAXUSDT",
    "DOGEUSDT", "LINKUSDT", "UNIUSDT", "XLMUSDT", "RUNEUSDT", "PENDLEUSDT",
)

WINDOW_START = "2024-01-01"
WINDOW_END = "2026-05-31"
NOTIONAL = 10_000.0            # $ per leg; returns are scale-invariant in %

BOOTSTRAP_N = 10_000
BOOTSTRAP_SEED = 20260603

# Gate B2 synthetic short-vol shock, calibrated to 2022 (LUNA/FTX) magnitude.
SHOCK_FUNDING_PER_8H = 0.005   # forced NEGATIVE funding we PAY, 0.5%/8h (extreme)
SHOCK_DAYS = 5                 # sustained stress duration
SHOCK_INTERVALS_PER_DAY = 3    # 8h funding -> 3/day (the shock is defined on an 8h basis)

# Binance Vision bulk + API.
BULK_BASE = "https://data.binance.vision/data/futures/um/monthly"
FAPI_FUNDING = "https://fapi.binance.com/fapi/v1/fundingRate"

OHLCV_DB = "data/ohlcv.db"        # spot klines (reused)
FUNDING_DB = "data/funding.db"    # produced by ingest
OUTPUT_DIR = "data/retune/2026-06-03-funding-carry-falsification"

# --- Tail-aware kill rule (sub-project #1, spec 9605758) ---
KILL_K = 24                       # exit after this many consecutive negative settlements (~8d)
K_SENSITIVITY = (9, 18, 24, 36)   # DESCRIPTIVE only — does NOT gate the verdict
N_SHOCKS = 2                      # synthetic out-of-sample shocks (2022 = LUNA + FTX)
LEVERAGE = 2.0                    # fixed conservative; liquidation needs ~50% adverse (not binding)
OUTPUT_DIR_KILL = "data/retune/2026-06-03-funding-carry-tail-kill"

# --- Shadow-deploy v0.1 (sub-project realizabilidad, spec 2026-06-03) ---
# Frozen universe = the verdict's symbols_used (LINKUSDT/SOLUSDT dropped for coverage).
SHADOW_SYMBOLS = (
    "BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT",
    "UNIUSDT", "XLMUSDT", "RUNEUSDT", "PENDLEUSDT",
)
FAPI_MARK_KLINES = "https://fapi.binance.com/fapi/v1/markPriceKlines"
FAPI_SPOT = "https://api.binance.com/api/v3/ticker/price"

DECAY_CI_LO = 0.0502           # backtest gate_a ci_lo — in-sample-anchored decay threshold
DECAY_WEEKS_W = 2              # rolling window (PROVISIONAL — frozen by power.py, Task 9/11)
DECAY_KILL_N = 4              # consecutive non-overlapping windows below threshold (PROVISIONAL)
FUNDING_FETCH_LIMIT = 1000     # FAPI fundingRate page size; covers multi-day gaps + back-fill

SHADOW_OUTPUT_DIR = "data/shadow"
SHADOW_VERSION = "v0.1"
