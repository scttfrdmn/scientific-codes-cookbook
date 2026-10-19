"""Compare the three chips' outputs: the fixed points must agree, to bounds set by GPAW's own
convergence criteria (forces 1e-5 eV/A, density 1e-6), with 10x headroom. Bounds were fixed before
the x86 runs.

    python3 collect.py <dir with arm64/ x86_64/ amd/ each holding the run's out/ files>
"""
import json
import os
import sys

import numpy as np

D = sys.argv[1]
CHIPS = ["arm64", "x86_64", "amd"]
BOUND = {"energy_eV": 1e-4, "force_eVA": 1e-4, "freq_THz": 1e-3}
fail = []


def load(chip):
    p = os.path.join(D, chip)
    out = {}
    for case in ("qe64", "vasp16"):
        out[case + "_E"] = json.load(open(os.path.join(p, "gpaw-%s.json" % case)))["energy"]
        out[case + "_F"] = np.loadtxt(os.path.join(p, "forces-%s-gpaw.tsv" % case))
    for line in open(os.path.join(p, "freqs.tsv")).read().splitlines()[1:]:
        k, q, f = line.split("\t")
        out["freq_%s_%s" % (k, q)] = np.array([float(x) for x in f.split()])
    return out


R = {c: load(c) for c in CHIPS if os.path.isdir(os.path.join(D, c))}
base = R["arm64"]
print("%-34s %s" % ("quantity", "  ".join("%-22s" % c for c in R if c != "arm64")))
for key in sorted(base):
    diffs = []
    for chip, r in R.items():
        if chip == "arm64":
            continue
        d = float(np.max(np.abs(np.asarray(r[key]) - np.asarray(base[key]))))
        kind = "energy_eV" if key.endswith("_E") else "force_eVA" if key.endswith("_F") else "freq_THz"
        ok = d < BOUND[kind]
        if not bool(ok):
            fail.append("%s %s" % (chip, key))
        diffs.append("%.2e %s" % (d, "ok" if ok else "FAIL(>%g)" % BOUND[kind]))
    print("%-34s %s" % (key, "  ".join("%-22s" % x for x in diffs)))
print("FAILED: " + ", ".join(fail) if fail else "ALL CROSS-CHIP FIXED POINTS AGREE (max |chip - Graviton4|)")
sys.exit(1 if fail else 0)
