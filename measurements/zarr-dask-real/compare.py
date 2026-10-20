"""Cross-chip comparison of the three runs' results/<chip>/ (written by collect.sh)."""
import json
import os
import sys

D = sys.argv[1]
CHIPS = ("arm64", "x86_64", "amd")
S = {c: json.load(open(os.path.join(D, c, "summary.json"))) for c in CHIPS}
N = {c: json.load(open(os.path.join(D, c, "ncref-ref.json"))) for c in CHIPS}

print("== every check passed on every chip ==")
for c in CHIPS:
    bad = [k for k, ok, _ in S[c]["checks"] if not ok]
    print("%-7s %d checks, %d failed %s" % (c, len(S[c]["checks"]), len(bad), bad))
names = [[k for k, _, _ in S[c]["checks"]] for c in CHIPS]
print("same check list on all chips:", names[0] == names[1] == names[2])

print("\n== netCDF side (fixed points) ==")
for k in ("tas_sha256", "rows_sha256", "total_scaled", "min", "max"):
    v = [N[c][k] for c in CHIPS]
    print("%-14s %s  %s" % (k, "identical" if len(set(map(str, v))) == 1 else "DIFFERS", v[0] if len(set(map(str, v))) == 1 else v))

print("\n== per layout: float results vs Graviton4 ==")
for lay in S["arm64"]["layouts"]:
    row = []
    for k in ("tot64", "tot32", "day64", "wsum"):
        a = S["arm64"]["layouts"][lay][k]
        row.append("%s %s" % (k, "/".join("=" if S[c]["layouts"][lay][k] == a else "DIFF" for c in CHIPS[1:])))
    print("%-14s %s" % (lay, "  ".join(row)))

print("\n== observations ==")
for k in S["arm64"]["obs"]:
    v = [S[c]["obs"].get(k) for c in CHIPS]
    same = len(set(map(str, v))) == 1
    if k.endswith("_s") or k.startswith("rss"):
        print("%-48s %s" % (k, v))
    else:
        print("%-48s %s %s" % (k, "identical" if same else "DIFFERS", v[0] if same else v))

print("\n== timings (s) ==")
for c in CHIPS:
    L = S[c]["layouts"]
    print(c, "wall", S[c]["wall_s"], "rss", S[c]["ru_maxrss_gib"],
          {k: (L[k]["write_s"], L[k]["verify_s"], L[k]["reduce_s"], L[k]["point_series_s"], L[k]["day_map_s"]) for k in L})
