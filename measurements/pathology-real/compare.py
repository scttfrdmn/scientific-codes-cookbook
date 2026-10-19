#!/usr/bin/env python3
"""Cross-chip comparison of real.py's per-slide results.

    python3 -I compare.py results/        # reads results/<chip>/slides.jsonl, chip in arm64 x86_64 amd

Every per-slide observable is an integer count, a hash, or a value computed elementwise from
the same bytes, so the comparison is exact: the decoded pixels (sha256 + RGB sum over every
visited block), the patch manifest, every check count, the disagreement histogram and its
maximum. Timings and RSS are excluded -- they are the measurement, not the result.
Exits 1 if any slide differs between any two chips.
"""
import json
import os
import sys

SKIP = {"fetch_s", "analyse_s", "wall_s", "rss_mib", "phase_s"}
root = sys.argv[1]
chips = [c for c in ("arm64", "x86_64", "amd") if os.path.exists(os.path.join(root, c, "slides.jsonl"))]
data = {}
for c in chips:
    data[c] = {}
    for line in open(os.path.join(root, c, "slides.jsonl")):
        r = json.loads(line)
        data[c][r["slide"]] = {k: v for k, v in r.items() if k not in SKIP}

ref = chips[0]
bad = 0
print("chips: %s" % ", ".join(chips))
for c in chips[1:]:
    same = diff = 0
    keys_diff = {}
    for s, r in data[ref].items():
        o = data[c].get(s)
        if o == r:
            same += 1
            continue
        diff += 1
        for k in sorted(set(r) | set(o or {})):
            if (o or {}).get(k) != r.get(k):
                keys_diff[k] = keys_diff.get(k, 0) + 1
    missing = set(data[c]) ^ set(data[ref])
    print("%s vs %s: %d/%d slides identical in every observable%s%s" % (
        c, ref, same, len(data[ref]), "" if not keys_diff else "; differing fields %s" % keys_diff,
        "" if not missing else "; slide sets differ %s" % sorted(missing)))
    bad += diff + len(missing)

for c in chips:
    n_px = sum(r.get("blocks_visited", 0) for r in data[c].values())
    print("%s: %d slides, %d blocks, pixels sha256 of tumor_091 %s" % (
        c, len(data[c]), n_px, data[c].get("tumor_091", {}).get("pixels_sha256", "?")[:16]))
if bad:
    sys.exit("FAIL: %d slide results differ across chips" % bad)
print("CROSS-CHIP IDENTICAL")
