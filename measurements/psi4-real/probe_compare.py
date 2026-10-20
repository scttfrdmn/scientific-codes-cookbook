import json
import sys

D = sys.argv[1]


def L(d):
    return {(r["geom_id"], r["rotated"]): r for r in map(json.loads, open(D + "/" + d + "/psi4.jsonl"))}


t, l, p4, p1 = L("p16"), L("loose"), L("p4"), L("p1")
cols = ["sapt_es", "sapt_ex", "sapt_exs2", "sapt_ind", "sapt_exind", "sapt_disp", "sapt_exdisp_os", "sapt_exdisp_ss",
        "sapt_delta_HF", "sapt_all", "tz_HF", "tz_MP2_os", "tz_MP2_ss", "tz_MP2_all"]
for c in cols:
    print("%-15s tight-loose %.2e   16thr-4thr %.2e  16thr-1thr %.2e" % (
        c, max(abs(t[k][c] - l[k][c]) for k in t if c in t[k]), max(abs(t[k][c] - p4[k][c]) for k in t if c in t[k]),
        max(abs(t[k][c] - p1[k][c]) for k in p1 if c in t[k])))
for name, s in (("p16", t), ("p4", p4), ("p1", p1), ("loose", l)):
    for k, r in s.items():
        print(name, k, "row", r["row"], "sapt %.0f s total %.0f s rss %.0f MiB" % (r["t_sapt"], r["t_total"], r["maxrss_mib"]))
