#!/usr/bin/env python3
"""Compare the three chips' results.tsv entry by entry; print the cohort numbers the README quotes.

    python3 compare.py results/arm64 results/x86_64 results/amd
"""
import csv
import os
import sys

NUM = ["rw", "rf", "rw_control", "p1_rel", "gemmi_rel", "d_min"]
INT = ["n_refl", "n_free", "n_atoms"]


def load(d):
    with open(os.path.join(d, "results.tsv")) as f:
        return {r["id"]: r for r in csv.DictReader(f, delimiter="\t")}


ref_dir, others = sys.argv[1], sys.argv[2:]
ref = load(ref_dir)
ok = [r for r in ref.values() if r["status"] == "ok"]
print("cohort %d: ok %d, skip %d, error %d" % (len(ref), len(ok), sum(r["status"] == "skip" for r in ref.values()),
                                               sum(r["status"] == "error" for r in ref.values())))
err = {}
for r in ref.values():
    if r["status"] == "error":
        k = r["reason"].split(":")[1].strip()[:48] if ":" in r["reason"] else r["reason"]
        err.setdefault(k, []).append(r["id"])
for k, v in sorted(err.items()):
    print("  error %-50s %d  %s" % (k, len(v), " ".join(v)))
dw = sorted(abs(float(r["rw"]) - float(r["rw_pub"])) for r in ok)
df = sorted(abs(float(r["rf"]) - float(r["rf_pub"])) for r in ok if r["rf"] != "NA" and r["rf_pub"] != "NA")
for name, d in (("R-work", dw), ("R-free", df)):
    print("|%s recomputed - deposited| over %d: median %.4f, <=0.01 %d, <=0.02 %d, <=0.05 %d, max %.4f" % (
        name, len(d), d[len(d) // 2], sum(x <= 0.01 for x in d), sum(x <= 0.02 for x in d), sum(x <= 0.05 for x in d), d[-1]))
worst = sorted(ok, key=lambda r: -abs(float(r["rw"]) - float(r["rw_pub"])))[:6]
for r in worst:
    print("  worst %s  deposited %s  recomputed %.4f  d_min %.2f  free %s" % (
        r["id"], r["rw_pub"], float(r["rw"]), float(r["d_min"]), r["n_free"]))
ctl = sorted(float(r["rw_control"]) for r in ok)
print("control R-work: median %.4f, min %.4f; min margin over real R %.4f" % (
    ctl[len(ctl) // 2], ctl[0], min(float(r["rw_control"]) - float(r["rw"]) for r in ok)))
print("space groups %d, reflections %d..%d (total %d), atoms %d..%d" % (
    len({r["sg"] for r in ok}), min(int(r["n_refl"]) for r in ok), max(int(r["n_refl"]) for r in ok),
    sum(int(r["n_refl"]) for r in ok), min(int(r["n_atoms"]) for r in ok), max(int(r["n_atoms"]) for r in ok)))
print("no free set: %d; f_obs_label chosen: %d; r_free_flags_label chosen: %d; reflection-file cell != model cell: %d" % (
    sum(r["rf"] == "NA" for r in ok), sum(r["f_obs_label"] != "NA" for r in ref.values()),
    sum(r["r_free_flags_label"] != "NA" for r in ref.values()),
    sum(r["cell_diff"] not in ("NA", "0.0") and float(r["cell_diff"]) > 1e-9 for r in ok)))
w = sorted(float(r["wall_s"]) for r in ref.values())
print("per-entry wall: median %.1f s, max %.1f s; peak worker RSS %.0f MiB" % (
    w[len(w) // 2], w[-1], max(float(r["rss_mib"]) for r in ref.values())))

for od in others:
    o = load(od)
    print("== %s vs %s" % (od, ref_dir))
    same_status = all(o[i]["status"] == ref[i]["status"] and o[i]["reason"] == ref[i]["reason"] for i in ref)
    print("  same entries, same status and reason: %s" % same_status)
    for c in INT:
        print("  %-11s identical on all ok: %s" % (c, all(o[r["id"]][c] == r[c] for r in ok)))
    for c in NUM:
        diffs = [abs(float(o[r["id"]][c]) - float(r[c])) for r in ok if r[c] != "NA" and o[r["id"]][c] != "NA"]
        nbit = sum(1 for r in ok if o[r["id"]][c] != r[c])
        print("  %-11s bit-identical %d of %d; max |diff| %.3e" % (c, len(ok) - nbit, len(ok), max(diffs or [0.0])))
    sw = sum(float(o[i]["wall_s"]) for i in o) / sum(float(ref[i]["wall_s"]) for i in ref)
    print("  sum of per-entry wall vs reference: %.3fx" % sw)
