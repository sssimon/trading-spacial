import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import pandas as pd
import calib_study as cs

def test_load_btc_dominance_normaliza_porcentaje(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("date,dominance\n2021-01-01,70.0\n2021-01-02,68.5\n")
    s = cs.load_btc_dominance(str(p))
    assert abs(s.loc[pd.Timestamp("2021-01-01", tz="UTC")] - 0.70) < 1e-9
    assert s.max() <= 1.0  # normalizado a fracción

def test_load_btc_dominance_ya_fraccion(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("date,dominance\n2021-01-01,0.70\n2021-01-02,0.685\n")
    s = cs.load_btc_dominance(str(p))
    assert abs(s.loc[pd.Timestamp("2021-01-01", tz="UTC")] - 0.70) < 1e-9
