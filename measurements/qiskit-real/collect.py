#!/usr/bin/env python3
"""Compare the three chips' circuits.jsonl, circuit by circuit, against Graviton4.
    python3 collect.py <dir holding arm64/ x86_64/ amd/, each with circuits.jsonl>
Parsed counts are a fixed point (deterministic parse) and must be identical; every verdict must
hold on every chip. The compiled circuit (its exact QASM, sha256) is a seeded heuristic search
over floating-point costs, so it is a PATH: reported, not asserted."""
import json
import math
import os
import sys

D = sys.argv[1]
CH = [c for c in ["arm64", "x86_64", "amd"] if os.path.exists(os.path.join(D, c, "circuits.jsonl"))]
rows = {}
for c in CH:
    with open(os.path.join(D, c, "circuits.jsonl")) as fh:
        rows[c] = {r["circuit"]: r for r in map(json.loads, fh)}
names = sorted(rows["arm64"])
out, bad = [], []


def line(s):
    out.append(s)
    print(s)


for c in CH[1:]:
    assert sorted(rows[c]) == names, "%s has a different circuit list" % c
    a, b = rows["arm64"], rows[c]
    parsed = [n for n in names if any(a[n].get(k) != b[n].get(k) for k in ("n", "size", "depth", "nonlocal"))]
    same_sha = [n for n in names if a[n].get("t_sha256") and a[n].get("t_sha256") == b[n].get("t_sha256")]
    uu = [n for n in names if a[n].get("u_sha256") and b[n].get("u_sha256")]
    line("%s vs arm64: measurement-free compile byte-identical on %d of %d" % (
        c, sum(a[n]["u_sha256"] == b[n]["u_sha256"] for n in uu), len(uu)))
    both = [n for n in names if a[n].get("t_sha256") and b[n].get("t_sha256")]
    diff_sha = [n for n in both if n not in same_sha]
    cz_diff = [n for n in diff_sha if a[n]["t_cz"] != b[n]["t_cz"]]
    seq_diff = [n for n in diff_sha if a[n].get("t_seq_sha256") != b[n].get("t_seq_sha256")]
    ang_diff = [n for n in diff_sha if a[n].get("t_ang_sha256") != b[n].get("t_ang_sha256")]
    line("%s vs arm64: parsed n/size/depth/nonlocal differ on %d of %d" % (c, len(parsed), len(names)))
    if parsed:
        bad.append("%s parse" % c)
    line("%s vs arm64: compiled QASM byte-identical on %d of %d compiled by both; differs on %d%s" % (
        c, len(same_sha), len(both), len(diff_sha), (": " + " ".join(diff_sha)) if diff_sha else ""))
    if diff_sha:
        line("    of those, the gate sequence (names + qubits) differs on %d%s; angles rounded to 1e-9 differ on %d%s" % (
            len(seq_diff), (": " + " ".join(seq_diff)) if seq_diff else "", len(ang_diff),
            (": " + " ".join(ang_diff)) if ang_diff else ""))
        line("    of those, cz count differs on %d: %s" % (len(cz_diff), " ".join(
            "%s(%d vs %d)" % (n, a[n]["t_cz"], b[n]["t_cz"]) for n in cz_diff)))
    for key in ("infidelity", "l1_infidelity", "closed_form_err", "direct_norm_err", "qi_vs_aer_maxdiff"):
        k = [n for n in names if isinstance(a[n].get(key), float) and isinstance(b[n].get(key), float)]
        if not k:
            continue
        same = sum(a[n][key] == b[n][key] for n in k)
        worst = max(k, key=lambda n: abs(a[n][key] - b[n][key]))
        line("%s vs arm64: %-18s identical on %d of %d; max |diff| %.3e (%s)" % (
            c, key, same, len(k), abs(a[worst][key] - b[worst][key]), worst))
    for key in ("fw_mps_exact", "fw_clifford_in_support", "stab_equiv"):
        k = [n for n in names if key in a[n] or key in b[n]]
        d = [n for n in k if a[n].get(key) != b[n].get(key)]
        if k:
            line("%s vs arm64: %-22s same verdict on %d of %d" % (c, key, len(k) - len(d), len(k)))
            if d:
                bad.append("%s %s" % (c, key))
    t = [n for n in names if "transpile_s" in a[n] and "transpile_s" in b[n]]
    ta, tb = sum(a[n]["transpile_s"] for n in t), sum(b[n]["transpile_s"] for n in t)
    line("%s vs arm64: summed transpile time %.0f s vs %.0f s (%.2fx)" % (c, tb, ta, tb / ta if ta else math.nan))
line("FAIL: " + "; ".join(bad) if bad else "cross-chip: every fixed point identical")
with open(os.path.join(D, "crosschip.txt"), "w") as fh:
    fh.write("\n".join(out) + "\n")
