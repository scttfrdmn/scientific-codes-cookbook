#!/usr/bin/env bash
# Pull each chip's evidence into results/<chip>/ (checks, host, footprint, per-pixel progress and
# spawn's completion record -- not pix.tar, which stays in the bucket and is diffed on AWS by
# compare.task.json), then print the per-chip timing and cost table.
#   bash measurements/specutils-real/collect.sh <bucket> <task nonce>
set -euo pipefail
B="${1:?bucket}"; NONCE="${2:?task nonce}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ACCT=$(aws sts get-caller-identity --query Account --output text)
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs; sfx=$NONCE ;; *) p=runs-$chip; sfx=${chip/_/}-$NONCE ;; esac
  mkdir -p "$HERE/results/$chip"
  for f in check.txt results.json host.txt footprint.txt progress.txt; do
    aws s3 cp --only-show-errors "s3://$B/$p/specutils-real/r1/$f" "$HERE/results/$chip/$f"
  done
  aws s3 cp --only-show-errors "s3://spawn-results-$ACCT-us-west-2/tasks/cookbook-specutils-real-r1-$sfx/completion.json" \
    "$HERE/results/$chip/completion.json"
done
aws s3 cp --only-show-errors "s3://$B/runs/specutils-real/compare/crosschip.txt" "$HERE/results/crosschip.txt" || true
python3 -I - "$HERE/results" <<'PY'
import json, os, re, sys
from datetime import datetime
R = sys.argv[1]
when = lambda s: datetime.strptime(s.split(".")[0].rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
for c in ("arm64", "x86_64", "amd"):
    d = os.path.join(R, c)
    j = json.load(open(os.path.join(d, "completion.json")))
    win = (when(j["ended_at"]) - when(j["started_at"])).total_seconds()
    hr = j["cost_estimate"] / win * 3600
    fp = open(os.path.join(d, "footprint.txt")).read()
    wall = int(re.search(r"real.py wall_s (\d+)", fp).group(1))
    peak = int(re.search(r"memory.peak (\d+)", fp).group(1))
    rss = max(json.loads(l.split(" ", 2)[2])["maxrss_kib"] for l in open(os.path.join(d, "progress.txt")) if l.startswith("done "))
    host = " | ".join(l.strip() for l in open(os.path.join(d, "host.txt")) if l.startswith(("Model name", "Thread", "openblas")))
    ok = "SPECUTILS REAL OK" in open(os.path.join(d, "check.txt")).read()
    print("%-7s %s  tool %4d s  window %4.0f s  $/hr %.4f  compute $ %.4f  billed $ %.4f  worker maxrss %.2f GiB  cgroup peak %.2f GiB  [%s]  %s" % (
        c, j["state"], wall, win, hr, wall / 3600 * hr, j["cost_estimate"], rss / 2**20, peak / 2**30, host, "OK" if ok else "CHECK FAILED"))
PY
