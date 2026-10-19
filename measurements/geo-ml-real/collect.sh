#!/usr/bin/env bash
# Pull each chip's evidence into results/<chip>/ (not the CSVs, which stay in the bucket), print the
# results table and compare every chip's outputs against Graviton4's.
#   bash measurements/geo-ml-real/collect.sh <bucket> <task nonce>
set -euo pipefail
B="${1:?bucket}"; NONCE="${2:?task nonce}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ACCT=$(aws sts get-caller-identity --query Account --output text)
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs; sfx=$NONCE ;; *) p=runs-$chip; sfx=${chip/_/}-$NONCE ;; esac
  aws s3 ls "s3://$B/$p/geo-ml-real/r1/results.json" >/dev/null 2>&1 || { echo "$chip: no run"; continue; }
  mkdir -p "$HERE/results/$chip"
  aws s3 cp --recursive --only-show-errors --exclude '*.csv' --exclude 'pins.check' \
    "s3://$B/$p/geo-ml-real/r1/" "$HERE/results/$chip/"
  aws s3 cp --only-show-errors "s3://spawn-results-$ACCT-us-west-2/tasks/cookbook-geo-ml-real-$sfx/completion.json" \
    "$HERE/results/$chip/completion.json" || true
done
python3 -I - "$HERE/results" <<'PY'
import json, os, sys
from datetime import datetime
R = sys.argv[1]
def when(s):
    return datetime.strptime(s.split(".")[0].rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
chips = [c for c in ("arm64", "x86_64", "amd") if os.path.exists(os.path.join(R, c, "results.json"))]
res = {c: json.load(open(os.path.join(R, c, "results.json"))) for c in chips}
out = []
def line(s):
    out.append(s); print(s)
for c in chips:
    r, j = res[c], json.load(open(os.path.join(R, c, "completion.json")))
    win = (when(j["ended_at"]) - when(j["started_at"])).total_seconds()
    hr = j["cost_estimate"] / win * 3600
    wall = float(open(os.path.join(R, c, "wall.txt")).read().split()[4].rstrip("s"))
    peak = open(os.path.join(R, c, "footprint.txt")).read().split()[1]
    line("%-7s %-28s tpc %s  real.py %4.0f s  window %4.0f s  $/hr %.4f  compute $ %.4f  billed $ %.4f  "
         "memory.peak %s GiB  ru_maxrss %s GiB  corename %s  %s" % (
             c, r["cpu_model"], r["threads_per_core"], wall, win, hr, wall / 3600 * hr, j["cost_estimate"],
             peak, r["ru_maxrss_gib"], r["openblas_corename"], j.get("state")))
    line("        phases: " + " ".join("%s=%s" % (k[2:], v) for k, v in sorted(r.items()) if k.startswith("t_")))
ref = res["arm64"]
for c in chips[1:]:
    diff = sorted(k for k in ref if not k.startswith("t_") and k not in ("ru_maxrss_gib", "cpu_model",
                  "threads_per_core", "openblas_corename", "nproc", "terra_tract_areas_sha256")
                  and ref.get(k) != res[c].get(k))
    line("%s vs arm64, differing results: %s" % (c, ", ".join("%s (%s -> %s)" % (k, ref.get(k), res[c].get(k))
                                                   for k in diff) or "none"))
open(os.path.join(R, "crosschip.txt"), "w").write("\n".join(out) + "\n")
PY
