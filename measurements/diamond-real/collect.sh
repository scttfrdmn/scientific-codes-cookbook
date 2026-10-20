#!/usr/bin/env bash
# Pull each chip's evidence into results/<chip>/ (not the hit tables, which stay in the bucket),
# plus spawn's completion record, and print one line per chip and the fixed points compared
# against Graviton4. compare/ (02-compare's cross-chip table comparison) is pulled if it ran.
#   bash measurements/diamond-real/collect.sh <bucket> <nonce>
set -euo pipefail
B="${1:?bucket}"; N="${2:?task nonce}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ACCT=$(aws sts get-caller-identity --query Account --output text)
R="s3://spawn-results-$ACCT-us-west-2/tasks"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs; id=$N ;; *) p=runs-$chip; id=${chip/_/}-$N ;; esac
  aws s3 ls "s3://$B/$p/diamond-real/search/check.txt" >/dev/null 2>&1 || { echo "$chip: no run"; continue; }
  mkdir -p "$HERE/results/$chip"
  aws s3 cp --recursive --only-show-errors --exclude '*.gz' --exclude 'cluster.tsv' --exclude 'qlen.tsv' \
    --exclude 'self.txt' --exclude 'reps.txt' "s3://$B/$p/diamond-real/search/" "$HERE/results/$chip/"
  aws s3 cp --only-show-errors "$R/cookbook-diamond-real-search-$id/completion.json" "$HERE/results/$chip/completion.json"
done
if aws s3 ls "s3://$B/runs/diamond-real/compare/compare.txt" >/dev/null 2>&1; then
  mkdir -p "$HERE/results/compare"
  aws s3 cp --only-show-errors "s3://$B/runs/diamond-real/compare/compare.txt" "$HERE/results/compare/"
fi
python3 -I - "$HERE/results" <<'PY'
import json, os, sys
from datetime import datetime
R = sys.argv[1]
def kv(p, sep=None):
    d = {}
    for l in open(p):
        f = l.rstrip("\n").split(sep)
        if len(f) >= 2:
            d[f[0]] = f[1]
    return d
def when(s):
    return datetime.strptime(s.split(".")[0].rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
FIXED = ["db_sequences", "db_letters", "queries", "query_letters", "queries_mapped",
         "representative_queries"]
PATH = ["dmnd_sha256", "queries_with_hit", "hit_rows", "reps_with_hit", "self_rows", "self_100pct", "full_length_self",
        "own_cluster_hit", "hits_sha256", "pairs", "pairs_sha256", "self_set_sha256"]
out, data, fail = [], {}, []
for c in ("arm64", "x86_64", "amd"):
    d = os.path.join(R, c)
    if not os.path.exists(os.path.join(d, "check.txt")):
        continue
    k = kv(os.path.join(d, "check.txt")); data[c] = k
    t = open(os.path.join(d, "timing.txt")).read().split()
    t = dict(zip(t[0::2], t[1::2]))
    j = json.load(open(os.path.join(d, "completion.json")))
    h = kv(os.path.join(d, "host.txt"), "\t")
    win = (when(j["ended_at"]) - when(j["started_at"])).total_seconds()
    hr = j["cost_estimate"] / win * 3600
    tool = float(t["makedb_s"]) + float(t["search_s"])
    out.append("%-7s %-28s tpc %s  makedb %5s s  search %5s s  tool %5.0f s  window %5.0f s  $/hr %.4f  "
               "compute $ %.4f  billed $ %.4f  rss makedb %.2f / search %.2f GiB  cgroup %s  %s %s" % (
                   c, h.get("cpu_model", "Graviton4 part " + h.get("cpu_part", "?")), h.get("threads_per_core"),
                   t["makedb_s"], t["search_s"], tool, win, hr, tool / 3600 * hr, j["cost_estimate"],
                   int(t["makedb_vmhwm_kb"]) / 2**20, int(t["vmhwm_kb"]) / 2**20, t.get("cgroup_memory_peak"),
                   j.get("state"), "PASSED" if "CHECKS PASSED" in open(os.path.join(d, "check.txt")).read() else "NOT PASSED"))
for c in ("x86_64", "amd"):
    if c not in data or "arm64" not in data:
        continue
    for key in FIXED + PATH:
        a, b = data["arm64"].get(key), data[c].get(key)
        tag = ("ok" if a == b else "FAIL") if key in FIXED else ("same" if a == b else "differs")
        if tag == "FAIL":
            fail.append("%s %s" % (c, key))
        out.append("%-7s %-24s %s vs Graviton4 %s  [%s]" % (c, key, b, a, tag))
out.append("FIXED POINTS AGREE" if not fail else "FIXED POINTS DIFFER: %s" % fail)
print("\n".join(out))
open(os.path.join(R, "crosschip.txt"), "w").write("\n".join(out) + "\n")
PY
