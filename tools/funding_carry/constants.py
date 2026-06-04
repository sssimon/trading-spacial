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

# --- Power v2 anchors (REV 5) — computed by power.py, FROZEN 2026-06-03 ---
# Unit: ANNUALIZED (mean per-settlement rate × 1095). Both anchors in the same unit.
# fossil_rate_band: gate_a bootstrap CI on 9-symbol mean(fundingRate)×1095 over 2024-01→2026-05.
# cost_floor: median(cost_v3/notional) over 9 symbols / H_REF_YEARS.
# HEALTHY: R_FOSSIL_LO (0.0590) >> T_FLOOR (0.0039) by 15×.
INTERVALS_PER_YEAR = 1095
H_REF_YEARS = 2.0
MARGIN = 0.0
R_FOSSIL_LO = 0.0589811223718191    # gate_a ci_lo on fossil mean rates (annualized)
R_FOSSIL_HI = 0.08118610026455027   # gate_a ci_hi on fossil mean rates (annualized)
T_FLOOR = 0.0038575872804181457     # median cost / notional / H_REF_YEARS (annualized)
# DECAY_WEEKS_W: power v2 / intra-window sigma method.
# intra-window pooled sigma=0.1489/yr; target_half_band=(R_FOSSIL_HI-T_FLOOR)/4=0.0193.
# W=1 is correct — the fossil band and cost floor are 15× apart; 1 week suffices.
DECAY_WEEKS_W = 1              # frozen by power.py v2 2026-06-03: intra-window sigma=0.1489
DECAY_KILL_N = 4              # frozen by power.py 2026-06-03: confirmatory guard (false-REFUTED target)
FUNDING_FETCH_LIMIT = 1000     # FAPI fundingRate page size; covers multi-day gaps + back-fill

SHADOW_OUTPUT_DIR = "data/shadow"
SHADOW_VERSION = "v0.1"
