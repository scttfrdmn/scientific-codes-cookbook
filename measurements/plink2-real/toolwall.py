#!/usr/bin/env python3
"""Seconds during which PLINK itself was running, from the Start/End times it prints.

    python3 toolwall.py logs.tar [logs19.tar ...]

The tasks also spend time in awk verifying identities over 78M rows, and bioconda's x86 image
runs a BusyBox awk several times slower than the arm64 image's -- a userland difference, not a
chip one. So the per-chip tool time is the union of PLINK's own [Start, End] intervals
(1-second resolution), which is serial in p2.sh and overlapping in p19.sh's parallel phase.
"""
import re
import sys
import tarfile
from datetime import datetime

PAT = re.compile(r"^(Start|End) time: (.+)$")

for path in sys.argv[1:]:
    iv = []
    with tarfile.open(path) as t:
        for m in t.getmembers():
            if not m.name.startswith("log."):
                continue
            start = None
            for line in t.extractfile(m).read().decode(errors="replace").splitlines():
                g = PAT.match(line.strip())
                if not g:
                    continue
                ts = datetime.strptime(g.group(2).strip(), "%a %b %d %H:%M:%S %Y").timestamp()
                if g.group(1) == "Start":
                    start = ts
                elif start is not None:
                    iv.append((start, ts))
                    start = None
    iv.sort()
    union, cur = 0.0, None
    for a, b in iv:
        if cur and a <= cur[1]:
            cur[1] = max(cur[1], b)
        else:
            if cur:
                union += cur[1] - cur[0]
            cur = [a, b]
    if cur:
        union += cur[1] - cur[0]
    print("%s\tplink_invocations=%d\tplink_busy_s=%.0f\tsum_s=%.0f" % (path, len(iv), union, sum(b - a for a, b in iv)))
