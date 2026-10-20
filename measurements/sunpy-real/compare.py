#!/usr/bin/env python3
"""Per-chip timings and cost, then every per-image field of each chip against Graviton4.

    python3 -I compare.py <results dir>
"""
import json
import os
import sys
from datetime import datetime

R = sys.argv[1]
chips = [c for c in ("arm64", "x86_64", "amd") if os.path.exists(os.path.join(R, c, "results.json"))]


def when(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")


res = {}
for c in chips:
    d = json.load(open(os.path.join(R, c, "results.json")))
    res[c] = {r["file"]: r for r in d["images"]}
    j = json.load(open(os.path.join(R, c, "completion.json")))
    win = (when(j["ended_at"]) - when(j["started_at"])).total_seconds()
    hr = j["cost_estimate"] / win * 3600
    foot = {"cgroup": l for l in open(os.path.join(R, c, "footprint.txt")).read().split("\n") if l.startswith("cgroup memory.peak")}
    host = open(os.path.join(R, c, "host.txt")).read()
    tpc = [l.split(":")[1].strip() for l in host.split("\n") if l.startswith("Thread(s) per core")]
    model = [l.split(":")[1].strip() for l in host.split("\n") if l.startswith("Model name")]
    wall = d["wall_s"]
    rss = max(r["maxrss_kib"] for r in d["images"]) / 2 ** 20
    per = sum(r["wall_s"] for r in d["images"]) / len(d["images"])
    print("%-7s %-9s %s tpc=%s  real.py %6.1f s  window %4d s  $/hr %.4f  compute $ %.4f  billed $ %.4f  "
          "worker rss %.2f GiB  cgroup %.1f GiB  per-image %.1f s  corename %s"
          % (c, j["state"], model[0] if model else "?", tpc[0] if tpc else "?", wall, win, hr, wall / 3600 * hr,
             j["cost_estimate"], rss, float(foot.get("cgroup", "memory.peak nan").split()[-1]) / 2 ** 30, per,
             d["versions"].get("openblas_corename")))
    T = {}
    for r in d["images"]:
        for k, v in r["t"].items():
            T[k] = T.get(k, 0) + v
    print("         per-phase CPU s: " + "  ".join("%s %.0f" % kv for kv in T.items()))

SKIP = {"t", "wall_s", "maxrss_kib"}
a = res["arm64"]
for c in chips[1:]:
    b = res[c]
    print("== %s vs arm64 (%d images, %d in common)" % (c, len(b), len(set(a) & set(b))))
    keys = sorted({k for r in a.values() for k in r} - SKIP)
    same = []
    for k in keys:
        nd, worst = 0, 0.0
        for f in a:
            x, y = a[f].get(k), b[f].get(k)
            if x == y:
                continue
            nd += 1
            try:
                xf, yf = float(x), float(y)
                worst = max(worst, abs(xf - yf) / max(abs(xf), 1e-300))
            except (TypeError, ValueError):
                worst = float("nan")
        if nd:
            print("  %-32s differs in %3d images, worst relative %.2e" % (k, nd, worst))
        else:
            same.append(k)
    print("  identical in every image: %s" % ", ".join(same))
print("COMPARE DONE")
