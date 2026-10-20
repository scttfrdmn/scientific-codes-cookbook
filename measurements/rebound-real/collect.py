#!/usr/bin/env python3
"""Compare the three chips' rebound-real outputs.

    python3 collect.py <dir>    # <dir>/{arm64,x86_64,amd}/{results.json,long.json,ephem.npz,long-*.npz}
"""
import json
import os
import sys

import numpy as np

D = sys.argv[1]
AU_M = 149597870700.0
CHIPS = ("arm64", "x86_64", "amd")


def load(chip):
    p = os.path.join(D, chip)
    return {"r": json.load(open(os.path.join(p, "results.json"))), "l": json.load(open(os.path.join(p, "long.json"))),
            "e": np.load(os.path.join(p, "ephem.npz")), "n": np.load(os.path.join(p, "long-nominal.npz")),
            "s": np.load(os.path.join(p, "long-shadow.npz"))}


def helio(run, i):
    return run["pos"][:, i] - run["pos"][:, 0]


def compare(c0, c1, A, B):
    print("== %s vs %s" % (c1, c0))
    print("  failed checks: %s %s | %s %s" % (c0, A["r"]["failed"] + A["l"]["failed"] or "none",
                                            c1, B["r"]["failed"] + B["l"]["failed"] or "none"))
    print("  check verdicts identical: %s" % ([c[1] for c in A["r"]["checks"] + A["l"]["checks"]] ==
                                              [c[1] for c in B["r"]["checks"] + B["l"]["checks"]]))
    print("  DE440 initial state: %s" % ("identical" if A["r"]["ic_sha256"] == B["r"]["ic_sha256"] else "DIFFERS"))
    de = [k for k in A["e"].files if k.startswith("de440")]
    print("  DE440 evaluations at checkpoints: %s" % ("identical" if all(np.array_equal(A["e"][k], B["e"][k]) for k in de)
                                                  else "max diff %.2e m" % max(np.abs(A["e"][k] - B["e"][k]).max() * AU_M for k in de)))
    for k in sorted((k for k in A["e"].files if not k.startswith("de440")), key=lambda k: (k.split("_")[0], abs(int(k.split("_")[-1])))):
        same = np.array_equal(A["e"][k], B["e"][k])
        print("  IAS15 %-18s %s" % (k, "bit-identical" if same else "max diff %.3e m" % (np.abs(A["e"][k] - B["e"][k]).max() * AU_M)))
    for lab, key in (("nominal", "n"), ("shadow", "s")):
        a, b = A[key], B[key]
        same = [np.array_equal(a["pos"][i], b["pos"][i]) and np.array_equal(a["vel"][i], b["vel"][i]) for i in range(len(a["t"]))]
        first = next((i for i, x in enumerate(same) if not x), None)
        if first is None:
            print("  WHFast %s: every checkpoint bit-identical through %.1f Myr" % (lab, a["t"][-1] / 365.25e6))
        else:
            d = np.linalg.norm(helio(a, 1) - helio(b, 1), axis=1) * AU_M
            print("  WHFast %s: first differs at checkpoint %d (%.1f Myr); Mercury separation (m) at 1/10/50/100 Myr: %s" % (
                lab, first, a["t"][first] / 365.25e6, ["%.3g" % d[min(int(x * 2), len(d) - 1)] for x in (1, 10, 50, 100)]))
    for k in ("ias15_fwd", "ias15_bwd", "gr_precession", "whfast_ladder_ratios", "whfast_c17_1000yr", "whfast_c17_vs_ias15_km"):
        print("  %-24s %s %s\n  %-24s %s %s" % (k, c0, A["r"][k], "", c1, B["r"][k]))
    for k in ("dE_max", "dL_max", "us_per_step", "wall_s"):
        print("  long %-10s %s %s | %s %s" % (k, c0, A["l"]["nominal"][k], c1, B["l"]["nominal"][k]))


def shadow(chip, run):
    """The 1 m shadow against the nominal run. Positions separate by along-track shear (the kick
    changes Mercury's mean motion), so the chaos is read from the eccentricity difference instead.
    longcheck.py's position e-fold mixes in that shear and is not a Lyapunov time."""
    n, s = run["n"], run["s"]
    t = n["t"] / 365.25e6
    print("== %s: shadow (Mercury +1 m at J2000) vs nominal" % chip)
    print("  shadow |dE/E| max %.2e, |dL|/|L| max %.2e; nominal |dE/E| max %.2e (first half %.2e, second %.2e)" % (
        np.abs(s["dE"]).max(), s["dL"].max(), np.abs(n["dE"]).max(), np.abs(n["dE"][:len(t) // 2 + 1]).max(),
        np.abs(n["dE"][len(t) // 2:]).max()))
    marks = [x for x in (1, 10, 25, 50, 75, 100) if x <= t[-1] + 1e-9]
    sep = np.linalg.norm(helio(n, 1) - helio(s, 1), axis=1) * AU_M
    de = np.abs(n["e"][:, 0] - s["e"][:, 0])
    print("  Myr                    " + " ".join("%8g" % x for x in marks))
    print("  Mercury separation (m) " + " ".join("%8.1e" % np.interp(x, t, sep) for x in marks))
    print("  Mercury |de|           " + " ".join("%8.1e" % np.interp(x, t, de) for x in marks))
    env = np.maximum.accumulate(de)
    for lo, hi in ((10, 100), (20, 80)):
        w = (t >= lo) & (t <= hi)
        if w.sum() > 4:
            print("  e-folding time of max|de|, %d-%d Myr: %.1f Myr" % (lo, hi, 1 / np.polyfit(t[w], np.log(env[w]), 1)[0]))
    for i, nm in ((1, "Mercury"), (9, "Pluto")):
        print("  %-8s e range %.4f-%.4f, a range %.1e (relative)" % (
            nm, n["e"][:, i - 1].min(), n["e"][:, i - 1].max(), np.ptp(n["a"][:, i - 1]) / n["a"][0, i - 1]))


runs = {c: load(c) for c in CHIPS if os.path.isdir(os.path.join(D, c))}
for c0, c1 in (("arm64", "x86_64"), ("arm64", "amd"), ("x86_64", "amd")):
    if c0 in runs and c1 in runs:
        compare(c0, c1, runs[c0], runs[c1])
for c, r in runs.items():
    shadow(c, r)
