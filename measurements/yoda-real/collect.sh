#!/usr/bin/env bash
# Pull each chip's evidence into results/<chip>/ and compare every chip against Graviton4.
#
#   bash measurements/yoda-real/collect.sh <bucket> <task nonce>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
NONCE="${2:?pass the nonce the specs were resolved with}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ACCT=$(aws sts get-caller-identity --query Account --output text)
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs; id=cookbook-yoda-real-real-$NONCE ;; *) p=runs-$chip; id=cookbook-yoda-real-real-${chip/_/}-$NONCE ;; esac
  aws s3 ls "s3://$BUCKET/$p/yoda-real/real/fill.tsv" >/dev/null 2>&1 || { echo "$chip: no run yet"; continue; }
  mkdir -p "$HERE/results/$chip"
  aws s3 cp --recursive --only-show-errors --exclude "*.yoda.gz" "s3://$BUCKET/$p/yoda-real/real/" "$HERE/results/$chip/"
  aws s3 cp --only-show-errors "s3://spawn-results-$ACCT-us-west-2/tasks/$id/completion.json" "$HERE/results/$chip/completion.json"
done
python3 -I - "$HERE/results" <<'PY' | tee "$HERE/results/compare.txt"
import json, os, sys
from datetime import datetime
R = sys.argv[1]
def tsv(p):
    return dict(l.rstrip("\n").split("\t", 1) for l in list(open(p))[1:])
def when(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
chips = [c for c in ("arm64", "x86_64", "amd") if os.path.exists(os.path.join(R, c, "fill.tsv"))]
rows = {}
for c in chips:
    a = tsv(os.path.join(R, c, "fill.tsv"))
    a.update({"refs:" + k: v for k, v in tsv(os.path.join(R, c, "refs.tsv")).items()})
    j = json.load(open(os.path.join(R, c, "completion.json")))
    win = (when(j["ended_at"]) - when(j["started_at"])).total_seconds()
    hr = j["cost_estimate"] / win * 3600
    tool = float(a["wall_s_pass"]) + float(a["refs:wall_s_refs"])
    rows[c] = a
    print("%-7s fill pass %6.1f s + refs %5.1f s  window %4d s  $/hr %.4f  compute $ %.5f  billed $ %.5f  rss worker %s/%s GiB  %s"
          % (c, float(a["wall_s_pass"]), float(a["refs:wall_s_refs"]), win, hr, tool / 3600 * hr,
             j["cost_estimate"], a["peak_rss_gib_worker"], a["refs:peak_rss_gib_worker"], j["state"]))
SKIP = lambda k: "wall_s" in k or "cpu_s" in k or "rss" in k or "rate" in k
for c in chips[1:]:
    A, B = rows["arm64"], rows[c]
    same = [x for x in A if not SKIP(x) and A.get(x) == B.get(x)]
    diff = [x for x in A if not SKIP(x) and A.get(x) != B.get(x)]
    print("== %s vs arm64: %d values identical; differ: %s" % (c, len(same), diff or "none"))
    for x in diff:
        print("    %s\n      arm64 %s\n      %-5s %s" % (x, A[x], c, B.get(x)))
PY
