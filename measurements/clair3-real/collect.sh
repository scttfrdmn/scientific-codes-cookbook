#!/usr/bin/env bash
# Pull each chip's call evidence into results/<chip>/ (the VCFs stay in the bucket), the eval and
# strat evidence into results/{eval,strat}/, spawn's completion records, and write results/perchip.txt.
#   bash measurements/clair3-real/collect.sh <bucket> <call nonce> <eval/strat nonce>
set -euo pipefail
B="${1:?bucket}"; N="${2:?call nonce}"; E="${3:?eval nonce}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ACCT=$(aws sts get-caller-identity --query Account --output text)
R="s3://spawn-results-$ACCT-us-west-2/tasks"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs; id=$N ;; *) p=runs-$chip; id=${chip/_/}-$N ;; esac
  mkdir -p "$HERE/results/$chip"
  aws s3 cp --recursive --only-show-errors --exclude '*.vcf.gz' --exclude '*.tbi' "s3://$B/$p/clair3-real/call/" "$HERE/results/$chip/"
  aws s3 cp --only-show-errors "$R/cookbook-clair3-real-call-$id/completion.json" "$HERE/results/$chip/completion.json"
done
for t in eval strat; do
  mkdir -p "$HERE/results/$t"
  aws s3 cp --recursive --only-show-errors --exclude '*.vcf.gz' --exclude '*.tbi' --exclude '*/vcfeval.log' \
    "s3://$B/runs/clair3-real/$t/" "$HERE/results/$t/"
  aws s3 cp --only-show-errors "$R/cookbook-clair3-real-$t-$E/completion.json" "$HERE/results/$t/completion.json"
done
python3 -I - "$HERE/results" <<'PY'
import json, os, sys
from datetime import datetime
R = sys.argv[1]
def kv(p):
    return dict(l.rstrip("\n").split("\t")[:2] for l in open(p) if "\t" in l)
def when(s):
    return datetime.strptime(s.split(".")[0].rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
out = []
for c in ("arm64", "x86_64", "amd", "eval", "strat"):
    d = os.path.join(R, c)
    k = kv(os.path.join(d, "results.tsv")); t = kv(os.path.join(d, "timings.tsv"))
    j = json.load(open(os.path.join(d, "completion.json")))
    win = (when(j["ended_at"]) - when(j["started_at"])).total_seconds()
    hr = j["cost_estimate"] / win * 3600
    tool = sum(float(v) for v in t.values())
    nfail = sum(1 for l in open(os.path.join(d, "checks.tsv")) if l.rstrip("\n").split("\t")[3] != "ok")
    out.append("%-6s %-28s tpc %s  tool %6.0f s  window %6.0f s  $/hr %.4f  compute $ %.4f  billed $ %.4f  "
               "cgroup peak %s MiB  %s  checks failed %d" % (
                   c, k.get("cpu_model"), k.get("threads_per_core"), tool, win, hr,
                   tool / 3600 * hr, j["cost_estimate"], k.get("cgroup_memory_peak_mib"), j.get("state"), nfail))
print("\n".join(out))
open(os.path.join(R, "perchip.txt"), "w").write("\n".join(out) + "\n")
PY
