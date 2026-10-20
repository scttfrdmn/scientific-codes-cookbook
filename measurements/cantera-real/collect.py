#!/usr/bin/env python3
"""Compare the three chips' results.jsonl: each x86 chip against Graviton4, and Intel against AMD.
    python3 collect.py <dir holding arm64/ x86_64/ amd/, each with results.jsonl>
Equilibrium states are fixed points (a Gibbs minimum), and so is a converged flame on the same grid;
an ignition delay is read off an adaptive integrator's step sequence, a path. All are reported here:
the assertions themselves ran inside each chip's task."""
import json
import os
import sys

D = sys.argv[1]
CH = [c for c in ["arm64", "x86_64", "amd"] if os.path.exists(os.path.join(D, c, "results.jsonl"))]
FK = ("set", "id", "mixture", "phi", "P_atm", "T0", "transport", "slope")
IK = ("set", "id", "mixture", "phi", "P_atm", "T0", "density_mol_m3", "reactor")


def load(c):
    R = [json.loads(l) for l in open(os.path.join(D, c, "results.jsonl"))]
    F = {tuple(r.get(k) for k in FK): r for r in R if r["job"] == "flame" and r.get("ok")}
    I = {tuple(r.get(k) for k in IK): r for r in R if r["job"] == "ignition" and r.get("ok")}
    E = {}
    for r in R:
        if r["job"] == "equilibrium" and r.get("ok"):
            for x in r["rows"]:
                E[(x["fuel"], x["P_atm"], x["phi"], x["T"])] = x
    return F, I, E


def rel(a, b):
    return abs(a - b) / max(abs(a), 1e-300)


out = []


def line(s):
    out.append(s)
    print(s)


L = {c: load(c) for c in CH}
PAIRS = [("arm64", c) for c in CH[1:]] + ([("x86_64", "amd")] if "x86_64" in CH and "amd" in CH else [])
for ref, c in PAIRS:
    A, B = L[ref], L[c]
    for name, i in (("flames", 0), ("reactors", 1), ("equilibrium states", 2)):
        if sorted(map(str, A[i])) != sorted(map(str, B[i])):
            line("%s: %s set differs from %s" % (c, name, ref))
    F, G = A[0], B[0]
    same_grid = [k for k in F if k in G and F[k]["points"] == G[k]["points"]]
    same_su = [k for k in F if k in G and F[k]["Su_cm_s"] == G[k]["Su_cm_s"]]
    w = max((k for k in F if k in G), key=lambda k: rel(F[k]["Su_cm_s"], G[k]["Su_cm_s"]))
    line("%s vs %s: flames %d; same grid size on %d; Su bit-identical on %d; max |dSu|/Su %.2e (%s %s phi %s P %s, %d vs %d points)" % (
        c, ref, len(F), len(same_grid), len(same_su), rel(F[w]["Su_cm_s"], G[w]["Su_cm_s"]), w[0], w[6], w[3], w[4],
        F[w]["points"], G[w]["points"]))
    sg = [k for k in same_grid]
    if sg:
        w2 = max(sg, key=lambda k: rel(F[k]["Su_cm_s"], G[k]["Su_cm_s"]))
        line("    on the same grid size: max |dSu|/Su %.2e" % rel(F[w2]["Su_cm_s"], G[w2]["Su_cm_s"]))
    I, J = A[1], B[1]
    ks = [k for k in I if k in J]
    for key in ("tau_dTdt_us", "T_end", "T_eq"):
        same = sum(I[k][key] == J[k][key] for k in ks)
        w = max(ks, key=lambda k: rel(I[k][key], J[k][key]))
        line("%s vs %s: reactors %-12s bit-identical on %d of %d; max rel diff %.2e (%s %s)" % (
            c, ref, key, same, len(ks), rel(I[w][key], J[w][key]), w[0], w[1] or "T0=%s P=%s phi=%s" % (w[5], w[4], w[3])))
    E1, E2 = A[2], B[2]
    ks = [k for k in E1 if k in E2]
    for s in ("vcs", "gibbs", "element_potential"):
        kk = [k for k in ks if "g_RT" in E1[k][s] and "g_RT" in E2[k][s]]
        conv = (sum("g_RT" in E1[k][s] for k in ks), sum("g_RT" in E2[k][s] for k in ks))
        same = sum(E1[k][s]["g_RT"] == E2[k][s]["g_RT"] for k in kk)
        w = max(kk, key=lambda k: rel(E1[k][s]["g_RT"], E2[k][s]["g_RT"]))
        line("%s vs %s: equilibrium %-17s converged %d / %d; g/RT bit-identical on %d of %d; max rel diff %.2e" % (
            c, ref, s, conv[1], conv[0], same, len(kk), rel(E1[w][s]["g_RT"], E2[w][s]["g_RT"])))
with open(os.path.join(D, "crosschip.txt"), "w") as fh:
    fh.write("\n".join(out) + "\n")
