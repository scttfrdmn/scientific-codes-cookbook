#!/usr/bin/env python3
"""Compare measurements/ants-real across chips, and against FSL's FNIRT on the same scans.

    python3 -I compare.py            # reads results/{x86_64,amd}/results.tsv and ../fsl-real/results/
                                     # writes results/compare.txt
"""
import csv, os, statistics, sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load(path):
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    ids = [r["id"] for r in rows]
    if len(ids) != len(set(ids)):
        sys.exit("%s: duplicate subject ids" % path)
    return {r["id"]: r for r in rows}


intel = load(os.path.join(HERE, "results/x86_64/results.tsv"))
amd = load(os.path.join(HERE, "results/amd/results.tsv"))
ids = sorted(set(intel) & set(amd))
out = []
p = out.append
p("subjects on both chips: %d (Intel %d, AMD %d)" % (len(ids), len(intel), len(amd)))
p("")
p("Intel vs AMD, per subject                identical   largest |difference|")
for col in ("rigid_sha16", "aff_sha16", "warp_sha16", "id_rel", "resid_mm", "self_dev", "dice_aff", "dice_syn",
            "r_aff", "r_syn", "jac_min_brain", "ic_mean", "ic_max", "warp_mean"):
    same = sum(intel[i][col] == amd[i][col] for i in ids)
    if col.endswith("sha16"):
        p("  %-36s %4d / %d" % (col, same, len(ids)))
        continue
    d = max(abs(float(intel[i][col]) - float(amd[i][col])) for i in ids)
    p("  %-36s %4d / %d   %.3g" % (col, same, len(ids), d))

p("")
p("ANTs SyN (antsRegistrationSyNQuick.sh) vs FSL FNIRT, same scans, same MNI152 2 mm bytes,")
p("curated mask carried to MNI space, Dice against MNI152's brain mask:")
for arch in ("x86_64", "arm64"):
    f = os.path.join(HERE, "../fsl-real/results/%s/results.tsv" % arch)
    if not os.path.exists(f):
        continue
    fsl = load(f)
    both = [i for i in ids if i in fsl and fsl[i]["status"] == "OK"]
    a = [float(intel[i]["dice_syn"]) for i in both]
    b = [float(fsl[i]["dice_fnirt"]) for i in both]
    aa = [float(intel[i]["dice_aff"]) for i in both]
    fa = [float(fsl[i]["dice_flirt"]) for i in both]
    wins = sum(x > y for x, y in zip(a, b))
    p("  FSL %-7s n=%d  ANTs affine median %.4f  FLIRT median %.4f  |  SyN median %.4f  FNIRT median %.4f  "
      "SyN higher on %d / %d" % (arch, len(both), statistics.median(aa), statistics.median(fa),
                                statistics.median(a), statistics.median(b), wins, len(both)))
text = "\n".join(out) + "\n"
open(os.path.join(HERE, "results/compare.txt"), "w").write(text)
print(text, end="")
