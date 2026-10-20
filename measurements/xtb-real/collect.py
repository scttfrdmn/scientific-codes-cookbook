"""Cross-chip comparison of the three xtb-real runs.
    python3 measurements/xtb-real/collect.py <dir holding arm64/ x86_64/ amd/, each with xtb.jsonl.gz>
Energies (fixed points: asserted equal across chips), the analytic gradients against each other and
against each chip's own finite difference (the evidence for conda-forge/xtb-feedstock#50 at the scale of
a whole benchmark), and where the x86 gradient fails too."""
import collections
import gzip
import json
import os
import sys

import numpy as np

D = sys.argv[1]
fails = []


def load(arch):
    with gzip.open(os.path.join(D, arch, "xtb.jsonl.gz"), "rt") as fh:
        return {r["sys"]: r for r in map(json.loads, fh)}


runs = {a: load(a) for a in ("arm64", "x86_64", "amd") if os.path.exists(os.path.join(D, a, "xtb.jsonl.gz"))}
keys = sorted(set.intersection(*(set(r) for r in runs.values())))
print("structures in every run", len(keys))


def fd_ok(r):
    f = r["fd"]
    return abs(f["analytic"] - f["five_point"]) <= 1e-7 + 1e-5 * abs(f["five_point"])


def energy_cmp(a, b, part):
    d = [abs(runs[a][k][part]["E"] - runs[b][k][part]["E"]) for k in keys
         if runs[a][k][part].get("E") is not None and runs[b][k][part].get("E") is not None]
    nn = [k for k in keys if (runs[a][k][part].get("E") is None) != (runs[b][k][part].get("E") is None)]
    return max(d), sum(1 for v in d if v == 0.0), len(d), nn


for a, b in (("x86_64", "arm64"), ("amd", "arm64"), ("amd", "x86_64")):
    if a not in runs or b not in runs:
        continue
    print("== %s vs %s" % (a, b))
    for part in ("bench", "tight"):
        mx, same, n, nn = energy_cmp(a, b, part)
        print("  %-5s energy  max|dE| %.3e Eh, bit-identical %d of %d, converged on one only: %s" % (part, mx, same, n, nn or "none"))
        if mx > 1e-9 or nn:
            fails.append("%s-%s %s energy" % (a, b, part))
    fa = [abs(runs[a][k]["fd"]["five_point"] - runs[b][k]["fd"]["five_point"]) for k in keys
          if "fd" in runs[a][k] and "fd" in runs[b][k]]
    print("  5-point FD derivative max|diff| %.3e" % max(fa))
    rel, same = [], 0
    for k in keys:
        ga, gb = runs[a][k]["tight"].get("g"), runs[b][k]["tight"].get("g")
        if ga is None or gb is None or runs[a][k]["natoms"] < 2:
            continue
        ga, gb = np.array(ga), np.array(gb)
        same += bool(np.array_equal(ga, gb))
        rel.append(float(np.linalg.norm(ga - gb) / max(np.linalg.norm(ga), 1e-12)))
    rel = np.array(rel)
    print("  analytic gradient |g_a-g_b|/|g_a|: median %.2e p90 %.2e max %.2e; >1e-6 on %d of %d; bit-identical %d"
          % (np.median(rel), np.percentile(rel, 90), rel.max(), int((rel > 1e-6).sum()), len(rel), same))

print("== each chip's analytic gradient against its own 5-point FD (|err| <= 1e-7 + 1e-5|FD|)")
bad = {}
for a in runs:
    fd = [k for k in keys if "fd" in runs[a][k]]
    bad[a] = sorted(k for k in fd if not fd_ok(runs[a][k]))
    gn = [(runs[a][k]["bench"].get("gnorm"), k) for k in keys if runs[a][k]["bench"].get("gnorm") is not None]
    rel = np.array([abs(runs[a][k]["fd"]["analytic"] - runs[a][k]["fd"]["five_point"]) /
                    max(abs(runs[a][k]["fd"]["five_point"]), 1e-12) for k in fd])
    print("  %-6s fail %4d of %d; rel err median %.2e p90 %.2e max %.2e"
          % (a, len(bad[a]), len(fd), np.median(rel), np.percentile(rel, 90), rel.max()))
if "x86_64" in bad and "amd" in bad:
    print("  Intel and AMD fail the same structures:", bad["x86_64"] == bad["amd"])
if "arm64" in runs and "x86_64" in runs:
    ratio = np.array([np.linalg.norm(runs["arm64"][k]["tight"]["g"]) / np.linalg.norm(runs["x86_64"][k]["tight"]["g"])
                      for k in keys if runs["x86_64"][k]["natoms"] > 1 and runs["x86_64"][k]["tight"].get("g")
                      and runs["arm64"][k]["tight"].get("g") and k not in bad["x86_64"]
                      and np.linalg.norm(runs["x86_64"][k]["tight"]["g"]) > 1e-6])
    print("  |g_arm64| / |g_x86| where x86 passes: median %.1f, p10 %.1f, p90 %.1f, min %.2f, max %.1f (n %d)"
          % (np.median(ratio), np.percentile(ratio, 10), np.percentile(ratio, 90), ratio.min(), ratio.max(), len(ratio)))
    # does arm64's FD (its own, correct, energies) reproduce x86's analytic gradient?
    dd = [abs(runs["arm64"][k]["fd"]["five_point"] - runs["x86_64"][k]["fd"]["analytic"]) for k in keys
          if "fd" in runs["arm64"][k] and "fd" in runs["x86_64"][k] and k not in bad["x86_64"]]
    print("  arm64 FD vs x86 analytic (x86-passing): max|diff| %.2e" % max(dd))

if "x86_64" in bad:
    a = "x86_64"
    sub = collections.Counter(k.split("/")[0] for k in bad[a])
    tot = collections.Counter(k.split("/")[0] for k in keys if "fd" in runs[a][k])
    print("== where the x86 gradient fails, by subset (failing / with FD)")
    print("  " + ", ".join("%s %d/%d" % (s, sub[s], tot[s]) for s in sorted(sub, key=lambda s: -sub[s])))
    el_bad = collections.Counter(e for k in bad[a] for e in runs[a][k]["elements"])
    el_all = collections.Counter(e for k in keys if "fd" in runs[a][k] for e in runs[a][k]["elements"])
    print("  by element present (failing / structures containing it):")
    print("  " + ", ".join("%s %d/%d" % (e, el_bad[e], el_all[e]) for e in sorted(el_all, key=lambda e: -el_bad[e] / el_all[e])))
    with open(os.path.join(D, "x86-gradient-fail.txt"), "w") as fh:
        fh.write("\n".join(bad[a]) + "\n")

print("RESULT", "FAIL: " + "; ".join(fails) if fails else "energies agree across chips")
