#!/usr/bin/env python3
"""Per-subject comparison of two chips' measurements/fsl-real/results/<chip>/results.tsv.
    compare.py [first second]    (default: arm64 x86_64; also amd)"""
import csv, os, statistics as st, sys

HERE = os.path.dirname(os.path.abspath(__file__))
def load(a):
    return {r["id"]: r for r in csv.DictReader(open(os.path.join(HERE, "results", a, "results.tsv")), delimiter="\t")}
NA, NX = (sys.argv[1:3] if len(sys.argv) > 2 else ("arm64", "x86_64"))
A, X = load(NA), load(NX)
ids = sorted(A)
assert sorted(X) == ids and all(A[i]["status"] == X[i]["status"] == "OK" for i in ids)
n = len(ids)
same_bet = sum(A[i]["bet_sha16"] == X[i]["bet_sha16"] for i in ids)
same_vox = sum(A[i]["bet_vox"] == X[i]["bet_vox"] for i in ids)
print(f"subjects                      {n}")
print(f"BET mask bytes identical      {same_bet} / {n}")
print(f"BET mask voxel count equal    {same_vox} / {n}")
for k in ("bet_raw_dice", "bet_dice", "resid_mm", "dice_flirt", "dice_fnirt"):
    d = [abs(float(A[i][k]) - float(X[i][k])) for i in ids]
    eq = sum(x == 0 for x in d)
    print(f"{k:14s} identical {eq:3d}/{n}   max |{NA}-{NX}| {max(d):.6f}   median {st.median(d):.6f}")
diff = [i for i in ids if A[i]["bet_sha16"] != X[i]["bet_sha16"]]
for i in diff:
    print(f"  BET differs  {i}  vox {A[i]['bet_vox']} vs {X[i]['bet_vox']}  dice {A[i]['bet_dice']} vs {X[i]['bet_dice']}")
for k in ("t_bet", "t_flirt6", "t_flirt12", "t_fnirt"):
    a = sum(float(A[i][k]) for i in ids); x = sum(float(X[i][k]) for i in ids)
    print(f"{k:10s} sum {NA} {a:8.0f} s   {NX} {x:8.0f} s   {NX}/{NA} {x/a:.2f}")
imp = [float(A[i]["bet_dice"]) - float(A[i]["bet_raw_dice"]) for i in ids]
print(f"robustfov improves BET Dice on {sum(v > 0 for v in imp)}/{n} ({NA}); min gain {min(imp):.4f}")
for name, R in ((NA, A), (NX, X)):
    g = [float(R[i]["dice_fnirt"]) - float(R[i]["dice_flirt"]) for i in ids]
    print(f"{name}: FNIRT beats its affine start on {sum(v > 0 for v in g)}/{n}, min gain {min(g):.4f}; "
          f"max planted-motion residual {max(float(R[i]['resid_mm']) for i in ids):.4f} mm (bound 0.25)")
low = sum(float(A[i]["bet_raw_dice"]) < 0.80 for i in ids)
print(f"naive BET below Dice 0.80: {low}/{n} ({NA})")
