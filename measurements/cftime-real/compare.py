#!/usr/bin/env python3
"""Every row of summary.tsv is a deterministic fact about the archive and cftime's decode of it,
including the sha256 of the full per-store and per-variable results -- so all three chips must
agree on every row. Prints the comparison and exits 1 on any difference."""
import os
import sys

d = sys.argv[1]
chips = ("arm64", "x86_64", "amd")
s = {c: [l.rstrip("\n").split("\t", 1) for l in open(os.path.join(d, c, "summary.tsv"))] for c in chips}
bad = 0
for c in chips[1:]:
    same = s[c] == s["arm64"]
    print("%-7s vs arm64: summary.tsv %s (%d rows)" % (c, "IDENTICAL" if same else "DIFFERS", len(s[c])))
    if not same:
        bad += 1
        a, b = dict(map(tuple, s["arm64"])), dict(map(tuple, s[c]))
        for k in sorted(set(a) | set(b)):
            if a.get(k) != b.get(k):
                print("   %s: arm64 %s | %s %s" % (k, a.get(k), c, b.get(k)))
for c in chips:
    t = dict(l.rstrip("\n").split("\t", 1) for l in open(os.path.join(d, c, "timing.tsv")))
    h = open(os.path.join(d, c, "host.txt")).read().split("\n")
    print("%-7s %s | decode wall %s s, cpu %s s, workers %s, cgroup peak %.2f GiB, worker max RSS %.2f GiB" % (
        c, " / ".join(x.strip() for x in h if x.strip()), t["decode_wall_s"], t["children_cpu_s"], t["workers"],
        int(t["cgroup_memory_peak_bytes"]) / 2**30, int(t["children_maxrss_kib"]) / 2**20))
sys.exit(1 if bad else 0)
