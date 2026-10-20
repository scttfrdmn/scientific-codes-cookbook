#!/usr/bin/env bash
# Pull each chip's evidence into results/<chip>/ (the 17 MB abundance tables stay in the bucket,
# hashed in results.sha256) and print the per-chip timing and price table.
#   bash measurements/kallisto-real/collect.sh <bucket> <run label> <task nonce>
set -euo pipefail
B="${1:?bucket}"; RUN="${2:?run label}"; NONCE="${3:?nonce}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ACCT=$(aws sts get-caller-identity --query Account --output text)
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs; sfx=$NONCE ;; *) p=runs-$chip; sfx=${chip/_/}-$NONCE ;; esac
  aws s3 ls "s3://$B/$p/kallisto-real/$RUN/quant/check.txt" >/dev/null 2>&1 || { echo "$chip: no run"; continue; }
  mkdir -p "$HERE/results/$chip"
  aws s3 cp --recursive --only-show-errors --exclude '*abundance.tsv' --exclude '*.h5' "s3://$B/$p/kallisto-real/$RUN/quant/" "$HERE/results/$chip/"
  aws s3 cp --only-show-errors "s3://spawn-results-$ACCT-us-west-2/tasks/cookbook-kallisto-real-quant-$RUN-$sfx/completion.json" \
    "$HERE/results/$chip/completion.json"
done
aws s3 cp --only-show-errors "s3://$B/runs/kallisto-real/$RUN/check.txt" "$HERE/results/check.txt" || true
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
    t = {x[0]: x for x in (l.rstrip("\n").split("\t") for l in open(os.path.join(d, "timing.txt")))}
    for r in ("A", "B"):
        wall = float(t["quant_" + r][4]); rss = int(t["quant_" + r][6]) / 1048576
        print("%-6s %-26s tpc %s  %s %4.0f s  window %4.0f s  peak %.2f GiB  $/hr %.4f  compute $ %.4f  billed $ %.4f"
              % (c, h.get("cpu_model", "Graviton4 " + h.get("cpu_part", "?")), h.get("threads_per_core"), r, wall, win,
                 rss, hr, wall * hr / 3600, j["cost_estimate"]))
PY
