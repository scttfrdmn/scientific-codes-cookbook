#!/usr/bin/env bash
# Pull each chip's evidence (not the COGs) into results/<chip>/ and compare every chip against
# Graviton4.
#
#   bash measurements/earth-observation-real/collect.sh <bucket> <task nonce>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
NONCE="${2:?pass the nonce the specs were resolved with}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ACCT=$(aws sts get-caller-identity --query Account --output text)
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs; id=cookbook-earth-observation-real-$NONCE ;; *) p=runs-$chip; id=cookbook-earth-observation-real-${chip/_/}-$NONCE ;; esac
  aws s3 ls "s3://$BUCKET/$p/earth-observation-real/r2/score.tsv" >/dev/null 2>&1 || { echo "$chip: no run yet"; continue; }
  mkdir -p "$HERE/results/$chip"
  aws s3 cp --recursive --only-show-errors --exclude "*.tif" "s3://$BUCKET/$p/earth-observation-real/r2/" "$HERE/results/$chip/"
  aws s3 ls "s3://$BUCKET/$p/earth-observation-real/r2/" | grep '\.tif$' > "$HERE/results/$chip/cogs.ls"
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
chips = [c for c in ("arm64", "x86_64", "amd") if os.path.exists(os.path.join(R, c, "score.tsv"))]
rows = {}
for c in chips:
    a = tsv(os.path.join(R, c, "score.tsv"))
    j = json.load(open(os.path.join(R, c, "completion.json")))
    win = (when(j["ended_at"]) - when(j["started_at"])).total_seconds()
    hr = j["cost_estimate"] / win * 3600
    wall = float(a["wall_s_total"])
    rows[c] = a
    print("%-7s real.py %6.1f s  window %4d s  $/hr %.4f  compute $ %.5f  billed $ %.5f  rss worker %s GiB  %s"
          % (c, wall, win, hr, wall / 3600 * hr, j["cost_estimate"], a["peak_rss_gib_worker"], j["state"]))
TIMING = {k for k in rows.get("arm64", {}) if k.startswith(("wall_s", "sum_t_", "peak_rss"))}
for c in chips[1:]:
    A, B = rows["arm64"], rows[c]
    same = [x for x in A if x not in TIMING and A.get(x) == B.get(x)]
    diff = [x for x in A if x not in TIMING and A.get(x) != B.get(x)]
    print("== %s vs arm64: %d values identical; differ: %s" % (c, len(same), diff or "none"))
    for x in diff:
        print("    %s\n      arm64 %s\n      %-5s %s" % (x, A[x], c, B.get(x)))
    a = open(os.path.join(R, "arm64", "scenes.tsv")).read().splitlines()
    b = open(os.path.join(R, c, "scenes.tsv")).read().splitlines()
    nd = sum(1 for u, v in zip(a, b) if u != v) + abs(len(a) - len(b))
    print("   scenes.tsv: %d of %d rows differ" % (nd, len(a) - 1))
PY
