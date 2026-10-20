#!/usr/bin/env python3
"""Per-chip cost lines and the cross-chip comparison, from results/<chip>/ (download each run's
outputs there first) and spawn's completion records.

    AWS_PROFILE=aws python3 measurements/datashader-real/collect.py <spawn results bucket> <nonce>
"""
import datetime
import json
import os
import subprocess
import sys

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
bucket, nonce = sys.argv[1], sys.argv[2]
TASK = {"arm64": "cookbook-datashader-real-r1-%s", "x86_64": "cookbook-datashader-real-r1-x8664-%s",
        "amd": "cookbook-datashader-real-r1-amd-%s"}


def ts(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))


rs = {}
for chip, t in TASK.items():
    url = "s3://%s/tasks/%s/completion.json" % (bucket, t % nonce)
    c = json.loads(subprocess.run(["aws", "s3", "cp", url, "-"], capture_output=True, text=True, check=True).stdout)
    json.dump(c, open(os.path.join(D, chip, "completion.json"), "w"), indent=1)
    r = rs[chip] = json.load(open(os.path.join(D, chip, "results.json")))
    window = (ts(c["ended_at"]) - ts(c["started_at"])).total_seconds()
    per_hr = c["cost_estimate"] / window * 3600
    tool = float(r["TOOL_WALL_S"])
    print("%-7s tool %6.2f s  window %4.0f s  $/hr %.4f  compute $/result %.4f  billed $/result %.4f  peak RSS %s GiB"
          % (chip, tool, window, per_hr, tool / 3600 * per_hr, c["cost_estimate"], r["peak RSS main process GiB"]))

timing = ("time", "TOOL_WALL_S", "datashader rate", "threadpool", "reference worker peak", "peak RSS")
same = diff = 0
for k in rs["arm64"]:
    if k.startswith(timing):
        continue
    v = [str(rs[ch].get(k)) for ch in rs]
    if len(set(v)) == 1:
        same += 1
    else:
        diff += 1
        print("DIFF", k, v)
print("%d non-timing records identical on all three chips, %d differ" % (same, diff))
