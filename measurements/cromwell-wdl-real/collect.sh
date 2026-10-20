#!/usr/bin/env bash
# Pull each chip's evidence into results/<chip>/ (not the GVCF, which stays in the bucket) with
# spawn's completion record, and print one line per chip.
#   bash measurements/cromwell-wdl-real/collect.sh <bucket> <nonce>
set -euo pipefail
B="${1:?bucket}"; N="${2:?task nonce}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ACCT=$(aws sts get-caller-identity --query Account --output text)
R="s3://spawn-results-$ACCT-us-west-2/tasks"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs; id=$N ;; *) p=runs-$chip; id=${chip/_/}-$N ;; esac
  aws s3 ls "s3://$B/$p/cromwell-wdl-real/run/check.txt" > /dev/null 2>&1 || { echo "$chip: no run"; continue; }
  mkdir -p "$HERE/results/$chip"
  aws s3 cp --recursive --only-show-errors --exclude '*.g.vcf.gz*' --exclude 'meta*.json' \
    "s3://$B/$p/cromwell-wdl-real/run/" "$HERE/results/$chip/"
  aws s3 cp --only-show-errors "$R/cookbook-cromwell-wdl-real-run-$id/completion.json" "$HERE/results/$chip/completion.json"
done
if aws s3 ls "s3://$B/runs/cromwell-wdl-real/compare/compare.txt" > /dev/null 2>&1; then
  mkdir -p "$HERE/results/compare"
  aws s3 cp --only-show-errors "s3://$B/runs/cromwell-wdl-real/compare/compare.txt" "$HERE/results/compare/"
fi
python3 -I - "$HERE/results" <<'PY'
import json, os, sys
from datetime import datetime
R = sys.argv[1]
def kv(p):
    d = {}
    for l in open(p):
        f = l.rstrip("\n").split(None, 1) if "\t" not in l else l.rstrip("\n").split("\t")
        if len(f) >= 2:
            d.setdefault(f[0], f[1])
    return d
def when(s):
    return datetime.strptime(s.split(".")[0].rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
for c in ("arm64", "x86_64", "amd"):
    d = os.path.join(R, c)
    if not os.path.exists(os.path.join(d, "check.txt")):
        continue
    k, t = kv(os.path.join(d, "check.txt")), {a: int(b) for a, b in kv(os.path.join(d, "timing.txt")).items()}
    cj = json.load(open(os.path.join(d, "completion.json")))
    billed = (when(cj["ended_at"]) - when(cj["started_at"])).total_seconds()
    rate = cj["cost_estimate"] / billed * 3600
    r1 = t["cromwell_run1_end"] - t["cromwell_run1_start"]
    print("\t".join(str(x) for x in [c, k["instance_type"], "tpc=" + k["threads_per_core"],
          "run1=%ds" % r1, "run2=%ds" % (t["cromwell_run2_end"] - t["cromwell_run2_start"]),
          "run3=%ds" % (t["cromwell_run3_end"] - t["cromwell_run3_start"]),
          "direct=%ds" % (t["direct_end"] - t["direct_start"]), "billed=%ds" % billed, "$/hr=%.4f" % rate,
          "compute$=%.4f" % (r1 * rate / 3600), "billed$=%.4f" % cj["cost_estimate"],
          "taskpeak=%.1fGiB" % (int(k["task_cgroup_memory_peak_kib"]) / 2**20),
          "shardrss=%.2fGiB" % (int(k["direct_hc_max_shard_rss_kib"]) / 2**20),
          "engine=%.2fGiB" % (int(k["engine_maxrss_kib"]) / 2**20),
          "body=" + k["run1_body_md5"], "fails=" + k["fails"]]))
PY
