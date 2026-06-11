"""Tests de las constantes congeladas del probe valle-calidad.

Pre-registro: data/retune/2026-06-11-valle-calidad-probe/PREREGISTRO.md.
Estos valores NO se mueven tras ver resultados — el test los fija como fósiles."""
from tools.valle_calidad_probe import constants as K


def test_constantes_del_preregistro():
    assert K.STUDY_START == "2021-01-01"
    assert K.STUDY_END == "2025-04-30"      # frontera pre-holdout en tiempo
    assert K.HOLD_DAYS == 20                # H declarado, sin grid
    assert K.NOTIONAL_USD == 1000.0
    assert K.BOOTSTRAP_ITERS == 10000
    assert K.SEED == 42
    assert K.MIN_EPISODES_VALLE == 30       # umbral de poder (> 10 del F&G)
    assert K.REGIME_SPLIT == "2023-03-01"   # punto medio, solo reporte de robustez
    assert K.DB_PATH == "data/program_ohlcv.db"


def test_reusa_definicion_de_valle_del_screener():
    # La ventana y la banda de consolidación NO se redefinen aquí: se importan
    # del screener A (única fuente de verdad de "qué es un valle").
    from screener.valley_filter import CONSOLIDATION_WINDOW_DAYS, RANGE_BAND_MAX
    assert K.CONSOLIDATION_WINDOW_DAYS == CONSOLIDATION_WINDOW_DAYS
    assert K.RANGE_BAND_MAX == RANGE_BAND_MAX
