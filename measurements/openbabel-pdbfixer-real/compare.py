#!/usr/bin/env python3
"""Compare the three chips' results: python3 -I compare.py results/

Open Babel: every metric except wall and memory, the output sha256 and the mismatch lists.
PDBFixer: every per-entry column of entries.tsv except timings and memory, column by column,
Intel and AMD each against Graviton4. A differing column is listed with how many entries differ.
"""
import csv
import hashlib
import os
import sys

R = sys.argv[1]
CHIPS = ("arm64", "x86_64", "amd")
SKIP_OB = {"workload_wall_s", "worker_maxrss_kib"}
TIMING = {"t_atoms_s", "t_h_s", "t_fix_s", "t_ff_s", "t_total_s", "maxrss_kib"}


def kv(path):
    out = {}
    for line in open(path):
        p = line.split()
        if len(p) >= 2:
            out[p[0]] = " ".join(p[1:])
    return out


print("== Open Babel, whole ChEMBL 37: metrics (wall and memory excluded)")
ob = {c: kv(os.path.join(R, c, "ob", "metrics.txt")) for c in CHIPS}
for c in CHIPS[1:]:
    d = [k for k in ob["arm64"] if k not in SKIP_OB and ob["arm64"][k] != ob[c].get(k)]
    print("-- Graviton4 vs %s: %s" % (c, "identical (%d metrics)" % (len(ob["arm64"]) - len(SKIP_OB)) if not d else
                                    "; ".join("%s %s/%s" % (k, ob["arm64"][k], ob[c].get(k)) for k in d)))
for c in CHIPS:
    print("%-7s output_sha256 %s" % (c, ob[c]["output_sha256"]))
print("== mismatch lists (sha256)")
for f in sorted(os.listdir(os.path.join(R, "arm64", "ob"))):
    if f.startswith("mismatch_"):
        hs = {c: hashlib.sha256(open(os.path.join(R, c, "ob", f), "rb").read()).hexdigest()[:16] for c in CHIPS}
        print("%-40s %s" % (f, "identical " + hs["arm64"] if len(set(hs.values())) == 1 else str(hs)))

print("\n== PDBFixer, hash directory ab: per-entry columns (timings and memory excluded)")
ent = {}
for c in CHIPS:
    with open(os.path.join(R, c, "fix", "entries.tsv")) as f:
        ent[c] = {r["id"]: r for r in csv.DictReader(f, delimiter="\t")}
cols = [k for k in next(iter(ent["arm64"].values())) if k not in TIMING and k != "id"]
for c in CHIPS[1:]:
    assert set(ent[c]) == set(ent["arm64"]), "different entry sets"
    diff = {k: [i for i in ent["arm64"] if ent["arm64"][i][k] != ent[c][i][k]] for k in cols}
    same = [k for k in cols if not diff[k]]
    print("-- Graviton4 vs %s: %d of %d columns identical on all %d entries" % (c, len(same), len(cols), len(ent[c])))
    for k in cols:
        if diff[k]:
            ex = diff[k][0]
            print("   %-18s differs on %3d entries  e.g. %s: %s / %s" % (k, len(diff[k]), ex, ent["arm64"][ex][k][:24], ent[c][ex][k][:24]))
for c in CHIPS:
    st = {}
    for r in ent[c].values():
        st[r["status"]] = st.get(r["status"], 0) + 1
    print("%-7s %s" % (c, " ".join("%s=%d" % kvp for kvp in sorted(st.items()))))
