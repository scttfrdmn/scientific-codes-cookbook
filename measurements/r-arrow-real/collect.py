#!/usr/bin/env python3
"""Fetch each chip's outputs into results/<chip>/, print the per-chip cost lines and the cross-chip comparison.

    AWS_PROFILE=aws python3 measurements/r-arrow-real/collect.py <cookbook bucket> <spawn results bucket> <nonce>
"""
import datetime
import json
import os
import subprocess
import sys

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
bucket, results, nonce = sys.argv[1], sys.argv[2], sys.argv[3]
CHIP = {"arm64": ("runs", "cookbook-r-arrow-real-r1-%s"), "x86_64": ("runs-x86_64", "cookbook-r-arrow-real-r1-x8664-%s"),
        "amd": ("runs-amd", "cookbook-r-arrow-real-r1-amd-%s")}
KEEP = ["check.txt", "results.tsv", "host.txt", "footprint.txt", "staged.sha256", "files.csv", "pay.csv", "hour.csv", "dist.csv"]


def ts(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))


def s3(url):
    return subprocess.run(["aws", "s3", "cp", url, "-"], capture_output=True, text=True, check=True).stdout


rs = {}
for chip, (prefix, task) in CHIP.items():
    os.makedirs(os.path.join(D, chip), exist_ok=True)
    for f in KEEP:
        open(os.path.join(D, chip, f), "w").write(s3("s3://%s/%s/r-arrow-real/r1/%s" % (bucket, prefix, f)))
    c = json.loads(s3("s3://%s/tasks/%s/completion.json" % (results, task % nonce)))
    json.dump(c, open(os.path.join(D, chip, "completion.json"), "w"), indent=1)
    r = rs[chip] = dict(l.split("\t", 1) for l in open(os.path.join(D, chip, "results.tsv")).read().splitlines())
    window = (ts(c["ended_at"]) - ts(c["started_at"])).total_seconds()
    per_hr = c["cost_estimate"] / window * 3600
    tool = float(r["TOOL_WALL_S"])
    print("%-7s tool %6.2f s  window %4.0f s  $/hr %.4f  compute $/result %.4f  billed $/result %.4f  VmHWM %s"
          % (chip, tool, window, per_hr, tool / 3600 * per_hr, c["cost_estimate"], r["VmHWM"]))

skip = ("time_", "TOOL_WALL_S", "VmHWM", "cpu_count", "io_thread_count", "longdouble")
same = diff = 0
for k in rs["arm64"]:
    if k.startswith(skip):
        continue
    v = [rs[ch].get(k) for ch in rs]
    if len(set(v)) == 1:
        same += 1
    else:
        diff += 1
        print("DIFF", k, v)
print("%d non-timing records identical on all three chips, %d differ" % (same, diff))
