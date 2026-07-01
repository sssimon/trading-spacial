"""Medición directa de musikito: ¿su SELECCIÓN real (89 picks de 2019) le ganó al azar
de alts (130 baseline) en su propio ciclo? Lee el setup_features.csv de la caja
(uploads/musikito) — la data real, ya con features + forward. PICK vs BASE, Mann-Whitney.
CAVEAT: el CSV probablemente tiene survivorship en SUS picks (los delistados no se pudieron
bajar) -> el sesgo juega A FAVOR de musikito. Si aun así no le gana al azar, es decisivo.
"""
import pandas as pd
from scipy.stats import mannwhitneyu

CSV = r"C:\Users\simon\.claude\uploads\musikito\setup_features.csv"
df = pd.read_csv(CSV)
pick = df[df["label"] == "PICK"]
base = df[df["label"] == "BASE"]
print(f"n PICK={len(pick)}  n BASE={len(base)}  (survivorship en PICK juega a favor de musikito)\n")

for col in ["max_fwd_7d", "max_fwd_14d", "hit_t1_7d"]:
    p = pd.to_numeric(pick[col], errors="coerce").dropna()
    b = pd.to_numeric(base[col], errors="coerce").dropna()
    try:
        _, pval = mannwhitneyu(p, b, alternative="greater")  # PICK > BASE
    except Exception:
        pval = float("nan")
    # columnas max_fwd_* ya vienen en % (9.92 = 9.92%); hit_t1_7d es 0/1
    print(f"{col:14} | PICK med={p.median():6.2f}% media={p.mean():6.2f}% "
          f"| BASE med={b.median():6.2f}% media={b.mean():6.2f}% "
          f"| delta_media={(p.mean()-b.mean()):+6.2f}pp  p(PICK>BASE)={pval:.3f}")

print("\nLECTURA: si delta<=0 o p>>0.05 -> la SELECCIÓN de musikito NO le ganó al azar de alts,")
print("ni en su propio ciclo, ni con el survivorship a su favor. Su edge no estaba en elegir.")
