#!/bin/bash
# ============================================================
#  FULL GRID SEARCH — OVERNIGHT RUN
#  768 combos per symbol, 15 symbols (12 target + 3 profitable)
#  Expected: ~8-12 hours total
# ============================================================

cd /Users/samueldarioballesterosgarcia/projects/01_products/trading-spacial

LOG_DIR="data/backtest/grid_logs"
mkdir -p "$LOG_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
MASTER_LOG="$LOG_DIR/grid_search_${TIMESTAMP}.log"

echo "============================================================" | tee "$MASTER_LOG"
echo "  FULL GRID SEARCH — STARTED $(date)" | tee -a "$MASTER_LOG"
echo "  768 combos per symbol, 15 symbols" | tee -a "$MASTER_LOG"
echo "============================================================" | tee -a "$MASTER_LOG"

# Run each symbol sequentially (they share cached data, parallel would OOM)
SYMBOLS=(
    XRPUSDT
    SOLUSDT
    BTCUSDT
    AVAXUSDT
    NEARUSDT
    ETHUSDT
    BNBUSDT
    APTUSDT
    OPUSDT
    ATOMUSDT
    DOTUSDT
    LINKUSDT
    DOGEUSDT
    XLMUSDT
    ADAUSDT
)

TOTAL=${#SYMBOLS[@]}
IDX=0

for SYM in "${SYMBOLS[@]}"; do
    IDX=$((IDX + 1))
    echo "" | tee -a "$MASTER_LOG"
    echo "[$IDX/$TOTAL] Starting $SYM at $(date)" | tee -a "$MASTER_LOG"

    python grid_search_tf.py --symbol "$SYM" --start 2023-01-01 --end 2026-01-01 --top 5 2>&1 | tee -a "$MASTER_LOG"

    echo "[$IDX/$TOTAL] Finished $SYM at $(date)" | tee -a "$MASTER_LOG"
    echo "---" | tee -a "$MASTER_LOG"
done

echo "" | tee -a "$MASTER_LOG"
echo "============================================================" | tee -a "$MASTER_LOG"
echo "  GRID SEARCH COMPLETE — $(date)" | tee -a "$MASTER_LOG"
echo "============================================================" | tee -a "$MASTER_LOG"

# Aggregate all results
echo "" | tee -a "$MASTER_LOG"
echo "  FINAL SUMMARY — Best params per symbol:" | tee -a "$MASTER_LOG"
echo "" | tee -a "$MASTER_LOG"

for SYM in "${SYMBOLS[@]}"; do
    CSV="data/backtest/${SYM}_tf_grid_search.csv"
    if [ -f "$CSV" ]; then
        BEST=$(head -2 "$CSV" | tail -1)
        PNL=$(echo "$BEST" | python3 -c "import sys,csv; r=list(csv.reader(sys.stdin)); print(r[0][r[0].index('net_pnl')] if 'net_pnl' in r[0] else 'N/A')" 2>/dev/null || echo "N/A")
        echo "  $SYM: best P&L = $PNL" | tee -a "$MASTER_LOG"
    else
        echo "  $SYM: no results" | tee -a "$MASTER_LOG"
    fi
done

# Create consolidated best params
python3 << 'PYEOF' 2>&1 | tee -a "$MASTER_LOG"
import os, json, csv

data_dir = "data/backtest"
symbols = [
    "XRPUSDT", "SOLUSDT", "BTCUSDT", "AVAXUSDT", "NEARUSDT", "ETHUSDT",
    "BNBUSDT", "APTUSDT", "OPUSDT", "ATOMUSDT", "DOTUSDT", "LINKUSDT",
    "DOGEUSDT", "XLMUSDT", "ADAUSDT"
]

profitable = {}
all_best = {}

for sym in symbols:
    csv_path = os.path.join(data_dir, f"{sym}_tf_grid_search.csv")
    if not os.path.exists(csv_path):
        continue
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    if not rows:
        continue

    best = rows[0]  # already sorted by net_pnl desc
    pnl = float(best["net_pnl"])
    all_best[sym] = {
        "net_pnl": pnl,
        "win_rate": float(best["win_rate"]),
        "profit_factor": float(best["profit_factor"]),
        "tf_ema_fast": int(best["tf_ema_fast"]),
        "tf_ema_slow": int(best["tf_ema_slow"]),
        "tf_ema_filter": int(best["tf_ema_filter"]),
        "tf_atr_trail": float(best["tf_atr_trail"]),
        "tf_rsi_entry_long": int(best["tf_rsi_entry_long"]),
    }

    if pnl > 0:
        profitable[sym] = {
            "strategy": "auto",
            "tf_ema_fast": int(best["tf_ema_fast"]),
            "tf_ema_slow": int(best["tf_ema_slow"]),
            "tf_ema_filter": int(best["tf_ema_filter"]),
            "tf_atr_trail": float(best["tf_atr_trail"]),
            "tf_rsi_entry_long": int(best["tf_rsi_entry_long"]),
            "tf_rsi_entry_short": 100 - int(best["tf_rsi_entry_long"]),
            "use_5m_trigger": False,
        }

print(f"\n  === PROFITABLE SYMBOLS: {len(profitable)}/{len(all_best)} ===")
for sym, params in sorted(profitable.items(), key=lambda x: all_best[x[0]]["net_pnl"], reverse=True):
    b = all_best[sym]
    print(f"  {sym:>10}: ${b['net_pnl']:+,.0f} (WR {b['win_rate']}%, PF {b['profit_factor']:.2f}) "
          f"EMA({b['tf_ema_fast']}/{b['tf_ema_slow']}/{b['tf_ema_filter']}), "
          f"ATR {b['tf_atr_trail']}, RSI {b['tf_rsi_entry_long']}")

print(f"\n  === NOT PROFITABLE ===")
for sym in sorted(all_best, key=lambda x: all_best[x]["net_pnl"], reverse=True):
    if sym not in profitable:
        b = all_best[sym]
        print(f"  {sym:>10}: ${b['net_pnl']:+,.0f} (best combo still negative)")

# Save config-ready params
output = os.path.join(data_dir, "tf_optimized_params.json")
with open(output, "w") as f:
    json.dump(profitable, f, indent=2)
print(f"\n  Config-ready params: {output}")
PYEOF

echo ""
echo "OVERNIGHT_GRID_SEARCH_COMPLETE"
