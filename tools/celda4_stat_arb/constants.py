"""Parámetros IRREVOCABLES de la config primaria (spec §4).

Una vez commiteados, cambiar cualquiera = experimento NUEVO con namespace nuevo
(spec, preámbulo). Cada literal lleva su ancla (§ del spec). Estilo clonado de
tools/funding_carry/constants.py.
"""

# --- Ventana del ESTUDIO (spec §3, NV-A) -----------------------------------
# El estudio termina donde empieza la era del holdout. NINGÚN módulo lee barras,
# funding ni volumen con timestamp >= STUDY_END, aunque el db los contenga.
STUDY_START = "2021-01-01"        # spec §3 / §4: inicio del mundo post-institucionalización
STUDY_END = "2025-04-30"          # spec §3 NV-A: frontera del holdout en TIEMPO (no se cruza)

# --- Formación / trading (spec §4) -----------------------------------------
FORMATION_DAYS = 180              # spec §4: 4,320 barras 1h esperadas; rango 6-12mo de la lit.
TRADING_DAYS = 30                 # spec §4: walk-forward rolling sin solape; re-estimación cada 30d (F7)

# --- Test de formación Engle-Granger (spec §4, F4) -------------------------
ADF_P = 0.05                      # spec §4: ADF sobre residuos, p<0.05 (regression='c', autolag='AIC')
SIGMA_GUARD = 1e-6               # spec §4: σ del spread de formación (log) < esto → par excluido

# --- Cap de pares (spec §4, F5) --------------------------------------------
TOP_PAIRS = 20                    # spec §4: top-20 por menor p-value ADF (Gatev top-20)
MAX_PAIRS_PER_SYMBOL = 1          # spec §4 F5: un símbolo en MÁXIMO 1 par por trading window

# --- Señal (spec §4) -------------------------------------------------------
Z_ENTRY = 2.0                     # spec §4: |z| >= 2.0 (centro del rango 1.5-2.5)
Z_EXIT = 0.0                      # spec §4: cruce de z=0
Z_STOP = 3.0                      # spec §4: |z| >= 3.0 → cierre, sin re-entrada en el window

# --- Elegibilidad de símbolo (spec §4, F2/F3) ------------------------------
MIN_DOLLAR_VOL_DAILY = 1_000_000  # spec §4: mediana 180d de dollar-volume diario >= $1M
MIN_COVERAGE = 0.95               # spec §4: cobertura >= 95% de las 4,320 barras de formación

# --- Posiciones (spec §4) --------------------------------------------------
NOTIONAL_PER_LEG = 10_000         # spec §4 / §3: dollar-neutral, $10k por pierna (escala-invariante en %)

# --- Bootstrap (spec §5) ---------------------------------------------------
BOOTSTRAP_N = 10_000              # spec §5 Gate A/B: resamples por trading-window (F8)
SEED = 20260605                   # spec §4/§5: seed fijo, determinista

# --- Gate B — vigencia (spec §5, V4) ---------------------------------------
# Punto medio DERIVADO por regla: 2021-01→2025-04 = 52 meses; mitad = 26 meses
# → 2023-03. No elegido por eras de cripto; función mecánica de la ventana.
GATE_B_START = "2023-03-01"       # spec §5: windows con inicio >= esto entran al Gate B

# --- Kill criteria (spec §5, F9/F11) ---------------------------------------
MIN_POSITIONS = 30                # spec §5: < 30 posiciones cerradas → kill (N-INSUFICIENTE)
CONCENTRATION_MAX = 0.50          # spec §5 F11: mayor contribución neta+ de un par-window > 50% Σ+

# --- Gate de poder (spec §5/§6, F10/NV-B) ----------------------------------
# Regla pre-registrada: si MDE > POWER_MULT × T_FLOOR_v3w → muere N-INSUFICIENTE.
POWER_MULT = 3.0                  # spec §5 NV-B: ancla auto-referente a costos (cost floor)

# --- v3w: ventana de referencia para DERIVAR cortes de tier (spec §3-bis) --
# Pre-implementación se mide el dollar-volume mediano de los 10 curados de v3
# sobre esta ventana; los cortes son los puntos medios geométricos entre grupos
# de tier adyacentes. La derivación se congela aquí; el candado verifica el mapeo.
V3W_REFERENCE_WINDOW = (STUDY_START, STUDY_END)

# --- Procedencia / salida (spec §7, §8) ------------------------------------
SOURCE_PRIMARY = "celda4-stat-arb/primary"     # spec §7: trial registry source de la primaria
OUTPUT_DIR = "data/retune/programa-celdas/celda4-stat-arb"  # spec §8: artefactos del verdict
