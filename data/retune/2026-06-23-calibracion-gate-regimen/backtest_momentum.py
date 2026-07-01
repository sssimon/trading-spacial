"""TEST DECISIVO — la SECUENCIA (equity curve) del filtro MOMENTUM.
Copia de backtest_killswitch.py; cambio: en vez de gatillar en dias BTC-bear,
gatilla cuando hay candidatas que pasan 'vol_ratio>2 & rsi14>55' ESE dia.
Filtra el panel a esas filas, by_day con esas, cada dia con slots libres las llena
(rotacion por hash). M slots {5,10,20}, equal-weight, ladder net (costo 2%), compone.
Reporta terminal + maxDD SEPARADO train (2021-01..2023-12) y validate (2024-01..2025-04-29).
Barra: COMPONE si terminal>1 Y maxDD<50% en validate.
NO toca holdout; panel anti-survivorship hasta 2025-04-29.
"""
import sys, os, hashlib
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import pandas as pd
import calib_study as cs
import exit_study as ex

COST = 0.02
HOR = ex.HORIZON
FILTER = "vol_ratio > 2 and rsi14 > 55"

symbol_dfs = {s: ex.add_ladder(cs.compute_features(df)) for s, df in cs.load_spot_daily(cs._DEFAULT_DB).items()}
panel = cs._build_panel(symbol_dfs)
panel = panel[(panel["alive"]) & (panel["symbol"] != "BTCUSDT")].dropna(subset=["ladder_return"]).copy()
panel["net"] = panel["ladder_return"] - COST

# --- momentum filter: SOLO estas filas gatillan entradas ---
cand = panel[panel.eval(FILTER)].copy()
days = sorted(panel["date"].unique())          # calendario completo (por si un dia no hay candidata)
by_day = {d: g.sort_values("symbol") for d, g in cand.groupby("date")}


def simulate(M):
    cap = 1.0
    open_pos = []
    eq = []
    n_trades = 0
    days_invested = 0
    for i, d in enumerate(days):
        still = []
        for ei, p in open_pos:
            if ei <= i:
                cap += p
            else:
                still.append((ei, p))
        open_pos = still
        free = M - len(open_pos)
        if free > 0:
            g = by_day.get(d)
            if g is not None and len(g):
                off = int(hashlib.sha256(str(d).encode()).hexdigest(), 16) % len(g)
                pick = pd.concat([g.iloc[off:], g.iloc[:off]]).head(free)
                for net in pick["net"]:
                    open_pos.append((min(i + HOR, len(days) - 1), (cap / M) * float(net)))
                    n_trades += 1
        if len(open_pos) > 0:
            days_invested += 1
        eq.append(cap)
    for ei, p in open_pos:
        cap += p
    return pd.Series(eq, index=pd.to_datetime(days)), n_trades, days_invested


def seg_metrics(s, lo, hi):
    """Segmento [lo, hi]: normaliza a 1.0 al inicio, terminal y maxDD DENTRO del segmento."""
    w = s[(s.index >= lo) & (s.index <= hi)]
    if len(w) < 2:
        return float("nan"), float("nan")
    w = w / float(w.iloc[0])
    terminal = float(w.iloc[-1])
    maxdd = float(-(w / w.cummax() - 1.0).min())
    return terminal, maxdd


TRAIN = ("2021-01-01", "2023-12-31")
VAL = ("2024-01-01", "2025-04-29")
_split = pd.Timestamp(VAL[0], tz="UTC")
_pt_tr = cand[cand["date"] < _split]["net"].mean() * 100
_pt_val = cand[cand["date"] >= _split]["net"].mean() * 100

print(f"FILTRO MOMENTUM: {FILTER}   (ladder net cost={COST}, HORIZON={HOR}d)")
print(f"candidatas totales (alive, no-BTC, ladder valida): {len(cand)}  |  dias con >=1 candidata: {len(by_day)}/{len(days)}")
print(f"media por-trade net: train={_pt_tr:+.2f}%  val={_pt_val:+.2f}%   (lo que ya nos engaño una vez)\n")
print("M  |            TRAIN 2021-01..2023-12          |          VALIDATE 2024-01..2025-04-29        | trades  %dias-inv")
print("   | terminal   maxDD                           | terminal   maxDD   VEREDICTO                 |")
for M in (5, 10, 20):
    s, nt, di = simulate(M)
    tt, tdd = seg_metrics(s, *TRAIN)
    vt, vdd = seg_metrics(s, *VAL)
    passes = (vt > 1.0) and (vdd < 0.50)
    verdict = "COMPONE" if passes else ("PLANO/DD-alto" if vt <= 1.0 or vdd >= 0.50 else "?")
    print(f"{M:2} | {tt:7.2f}x  {tdd*100:5.1f}%                       "
          f"| {vt:7.2f}x  {vdd*100:5.1f}%  {verdict:20} | {nt:6} {di/len(days)*100:5.0f}%")

print("\nLECTURA: barra = terminal_val>1 Y maxdd_val<50%. Si el terminal se aplana o el DD supera 50%,")
print("el +7% por-trade era espejismo de cola (como el contrarian: +4.58% media, PLANO, 56% DD).")
