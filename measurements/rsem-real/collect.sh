#!/usr/bin/env bash
# Pull each chip's evidence into results/<chip>/ (the 30-90 MB results tables stay in the bucket,
# hashed in results.sha256) and print the per-chip timing and price table.
#   bash measurements/rsem-real/collect.sh <bucket> <run label> <task nonce>
set -euo pipefail
B="${1:?bucket}"; RUN="${2:?run label}"; NONCE="${3:?nonce}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ACCT=$(aws sts get-caller-identity --query Account --output text)
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs; sfx=$NONCE ;; *) p=runs-$chip; sfx=${chip/_/}-$NONCE ;; esac
  aws s3 ls "s3://$B/$p/rsem-real/$RUN/quant/check.txt" >/dev/null 2>&1 || { echo "$chip: no run"; continue; }
  mkdir -p "$HERE/results/$chip"
  aws s3 cp --recursive --only-show-errors --exclude '*.results' --exclude '*.theta' --exclude '*.model' \
    --exclude 'ERR188026.stat/*' "s3://$B/$p/rsem-real/$RUN/quant/" "$HERE/results/$chip/"
  aws s3 cp --only-show-errors "s3://spawn-results-$ACCT-us-west-2/tasks/cookbook-rsem-real-quant-$RUN-$sfx/completion.json" \
    "$HERE/results/$chip/completion.json"
done
aws s3 cp --only-show-errors "s3://$B/runs/rsem-real/$RUN/crosschip.txt" "$HERE/results/crosschip.txt" || true
python3 -I - "$HERE/results" <<'PY'
import json, os, sys
from datetime import datetime
R = sys.argv[1]
def when(s):
    return datetime.strptime(s.split(".")[0].rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
for c in ("arm64", "x86_64", "amd"):
    d = os.path.join(R, c)
    if not os.path.exists(os.path.join(d, "completion.json")):
        continue
    j = json.load(open(os.path.join(d, "completion.json")))
    win = (when(j["ended_at"]) - when(j["started_at"])).total_seconds()
    hr = j["cost_estimate"] / win * 3600
    h = dict(l.rstrip("\n").split("\t", 1) for l in open(os.path.join(d, "host.txt")) if "\t" in l)
    t = [l.split("\t") for l in open(os.path.join(d, "timing.txt"))]
    wall = float([x for x in t if x[0] == "rsem"][0][4])
    rss = max(int(x[2]) for x in t if x[0] == "maxrss_kb") / 1048576
    print("%-6s %-28s tpc %s  rsem %5.0f s  window %5.0f s  peak %.2f GiB  $/hr %.4f  compute $ %.4f  billed $ %.4f"
          % (c, h.get("cpu_model", "Graviton4 " + h.get("cpu_part", "?")), h.get("threads_per_core"), wall, win, rss,
             hr, wall * hr / 3600, j["cost_estimate"]))
PY
