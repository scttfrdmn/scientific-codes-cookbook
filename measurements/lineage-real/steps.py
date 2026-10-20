#!/usr/bin/env python3
"""Per-step wall times from pangolin's snakemake logs: each rule from its start line to its
"Finished jobid" line. Rules run in parallel where snakemake allows, so steps can overlap.
    steps.py <pangolin_*.log> ...   (results/<chip>/pangolin/)"""
import re
import sys
from datetime import datetime

TS = re.compile(r"^\[(\w{3} \w{3} +\d+ \d\d:\d\d:\d\d \d{4})\]$")
for path in sys.argv[1:]:
    start, last, out = {}, None, []
    lines = open(path, errors="replace").read().splitlines()
    for i, l in enumerate(lines):
        m = TS.match(l.strip())
        if m:
            last = datetime.strptime(m.group(1), "%a %b %d %H:%M:%S %Y")
            continue
        m = re.match(r"^localrule (\w+):", l.strip())
        if m and last:
            start[m.group(1)] = last
        m = re.match(r"^Finished jobid: \d+ \(Rule: (\w+)\)", l.strip())
        if m and last and m.group(1) in start:
            out.append((m.group(1), (last - start[m.group(1)]).total_seconds()))
    print(path)
    for r, s in out:
        if r != "all":
            print("  %-22s %6.0f s" % (r, s))
