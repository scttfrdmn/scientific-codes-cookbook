#!/usr/bin/env bash
# Pull each chip's evidence (not the VCFs) into results/<chip>/<caller>/, with spawn's completion
# record, plus 03-score's output; print one line per chip and caller.
#   bash measurements/structural-variants-real/collect.sh <bucket> <chip>:<task>:<nonce> ...
#   e.g. collect.sh "$B" arm64:manta:r2 arm64:delly:r1 x86_64:manta:r1 x86_64:delly:r1 amd:manta:r1 amd:delly:r1
set -euo pipefail
B="${1:?bucket}"; shift
HERE="$(cd "$(dirname "$0")" && pwd)"
ACCT=$(aws sts get-caller-identity --query Account --output text)
R="s3://spawn-results-$ACCT-us-west-2/tasks"
for run in "$@"; do
  chip=${run%%:*}; t=${run#*:}; N=${t#*:}; t=${t%%:*}
  case $chip in arm64) p=runs; id=$N ;; *) p=runs-$chip; id=${chip/_/}-$N ;; esac
  {
    aws s3 ls "s3://$B/$p/structural-variants-real/$t/check.txt" >/dev/null 2>&1 || { echo "$chip $t: no run"; continue; }
    mkdir -p "$HERE/results/$chip/$t"
    aws s3 cp --recursive --only-show-errors --exclude '*.vcf.gz' --exclude '*.tbi' --exclude '*.bcf' --exclude '*.csi' \
      "s3://$B/$p/structural-variants-real/$t/" "$HERE/results/$chip/$t/"
    aws s3 cp --only-show-errors "$R/cookbook-structural-variants-real-$t-$id/completion.json" "$HERE/results/$chip/$t/completion.json"
  }
done
for s in score; do
  if aws s3 ls "s3://$B/runs/structural-variants-real/$s/score.txt" >/dev/null 2>&1; then
    mkdir -p "$HERE/results/$s"
    aws s3 cp --recursive --only-show-errors "s3://$B/runs/structural-variants-real/$s/" "$HERE/results/$s/"
  fi
done
python3 -I - "$HERE/results" <<'PY'
import json, os, sys
from datetime import datetime
R = sys.argv[1]
def kv(p):
    d = {}
    for l in open(p):
        f = l.rstrip("\n").split("\t")
        if len(f) >= 2:
            d[f[0]] = f[1].strip()
    return d
def when(s):
    return datetime.strptime(s.split(".")[0].rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
out = []
for c in ("arm64", "x86_64", "amd"):
    for t in ("manta", "delly"):
        d = os.path.join(R, c, t)
        if not os.path.exists(os.path.join(d, "completion.json")):
            continue
        tm, h = kv(os.path.join(d, "timing.txt")), kv(os.path.join(d, "host.txt"))
        j = json.load(open(os.path.join(d, "completion.json")))
        win = (when(j["ended_at"]) - when(j["started_at"])).total_seconds()
        hr = j["cost_estimate"] / win * 3600
        tool = float(tm["workflow_s"]) + float(tm["config_s"]) if t == "manta" else float(tm["delly_s"])
        rss = int(tm.get("max_process_rss_kb") or tm.get("vmhwm_kb")) / 2**20
        cg = tm.get("cgroup_memory_peak", "NA")
        cg = "%.1f" % (int(cg) / 2**30) if cg.isdigit() else cg
        out.append("%-6s %-5s %-24s tpc %s  tool %6.0f s  window %6.0f s  $/hr %.4f  compute $ %.4f  billed $ %.4f  "
                   "rss %.2f GiB  cgroup %s GiB  %s %s" % (
                       c, t, h.get("cpu_model", "Graviton4 part " + h.get("cpu_part", "?")), h.get("threads_per_core"),
                       tool, win, hr, tool / 3600 * hr, j["cost_estimate"], rss, cg, j.get("state"),
                       "PASSED" if "CHECKS PASSED" in open(os.path.join(d, "check.txt")).read() else "NOT PASSED"))
print("\n".join(out))
open(os.path.join(R, "runs.txt"), "w").write("\n".join(out) + "\n")
PY
