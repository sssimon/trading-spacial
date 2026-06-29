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


def _mk_db(tmp_path):
    import sqlite3
    p = tmp_path / "panel.db"
    con = sqlite3.connect(str(p))
    con.execute("CREATE TABLE spot_klines(symbol TEXT, open_time INTEGER, open REAL, high REAL, low REAL, close REAL, volume REAL, PRIMARY KEY(symbol, open_time)) WITHOUT ROWID")
    # 2 días de barras horarias para FOOUSDT: día1 (24 barras), día2 (2 barras)
    H = 3600_000
    d1 = int(pd.Timestamp("2021-03-01", tz="UTC").timestamp() * 1000)
    rows = []
    for h in range(24):
        rows.append(("FOOUSDT", d1 + h*H, 10.0+h, 20.0+h, 5.0+h, 12.0+h, 100.0))
    d2 = int(pd.Timestamp("2021-03-02", tz="UTC").timestamp() * 1000)
    rows.append(("FOOUSDT", d2, 50.0, 60.0, 40.0, 55.0, 7.0))
    rows.append(("FOOUSDT", d2 + H, 55.0, 65.0, 45.0, 58.0, 3.0))
    con.executemany("INSERT INTO spot_klines VALUES (?,?,?,?,?,?,?)", rows)
    con.commit(); con.close()
    return str(p)


def test_load_spot_daily_resample(tmp_path):
    dbp = _mk_db(tmp_path)
    out = cs.load_spot_daily(dbp)
    df = out["FOOUSDT"]
    d1 = pd.Timestamp("2021-03-01", tz="UTC")
    assert df.loc[d1, "open"] == 10.0          # primer open del día
    assert df.loc[d1, "high"] == 20.0 + 23      # max high (h=23)
    assert df.loc[d1, "low"] == 5.0             # min low (h=0)
    assert df.loc[d1, "close"] == 12.0 + 23     # último close
    assert df.loc[d1, "volume"] == 100.0 * 24   # suma
    assert abs(df.loc[d1, "quote_vol"] - (100.0*24)*(12.0+23)) < 1e-6
    d2 = pd.Timestamp("2021-03-02", tz="UTC")
    assert df.loc[d2, "close"] == 58.0          # último close del día parcial
