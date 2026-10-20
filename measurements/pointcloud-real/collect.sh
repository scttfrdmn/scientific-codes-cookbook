#!/usr/bin/env bash
# Pull each chip's evidence (not the DTM) into results/<chip>/ and compare every chip against Graviton4.
#
#   bash measurements/pointcloud-real/collect.sh <bucket> <task nonce>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
NONCE="${2:?pass the nonce the specs were resolved with}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ACCT=$(aws sts get-caller-identity --query Account --output text)
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs; id=cookbook-pointcloud-real-$NONCE ;; *) p=runs-$chip; id=cookbook-pointcloud-real-${chip/_/}-$NONCE ;; esac
  aws s3 ls "s3://$BUCKET/$p/pointcloud-real/r1/results.tsv" >/dev/null 2>&1 || { echo "$chip: no run yet"; continue; }
  mkdir -p "$HERE/results/$chip"
  aws s3 cp --recursive --only-show-errors --exclude "*.tif" --exclude "pdal.log" "s3://$BUCKET/$p/pointcloud-real/r1/" "$HERE/results/$chip/"
  aws s3 ls "s3://$BUCKET/$p/pointcloud-real/r1/" > "$HERE/results/$chip/bucket.ls"
  aws s3 cp --only-show-errors "s3://spawn-results-$ACCT-us-west-2/tasks/$id/completion.json" "$HERE/results/$chip/completion.json"
done
python3 -I - "$HERE/results" <<'PY' | tee "$HERE/results/compare.txt"
import json, os, sys
from datetime import datetime
R = sys.argv[1]
def tsv(p):
    return dict(l.rstrip("\n").split("\t", 1) for l in list(open(p))[1:])
def when(s):
    return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
chips = [c for c in ("arm64", "x86_64", "amd") if os.path.exists(os.path.join(R, c, "results.tsv"))]
res = {c: tsv(os.path.join(R, c, "results.tsv")) for c in chips}
for c in chips:
    t = tsv(os.path.join(R, c, "times.tsv"))
    j = json.load(open(os.path.join(R, c, "completion.json")))
    win = (when(j["ended_at"]) - when(j["started_at"])).total_seconds()
    hr = j["cost_estimate"] / win * 3600
    w = float(t["wall_s_pdal"])
    print("%-7s pdal %6.1f s  laspy %6.1f s  real.py %6.1f s  window %5d s  $/hr %.4f  compute $ %.4f  billed $ %.4f  rss A/B %s/%s MiB  %s"
          % (c, w, float(t["wall_s_laspy"]), float(t["wall_s_total"]), win, hr, w / 3600 * hr, j["cost_estimate"],
             t["peak_rss_mib_pdal_A"], t["peak_rss_mib_pdal_B"], j["state"]))
for c in chips[1:]:
    A, B = res["arm64"], res[c]
    diff = [k for k in A if A.get(k) != B.get(k)]
    print("== %s vs arm64: %d of %d values identical; differ: %s" % (c, len(A) - len(diff), len(A), diff or "none"))
    for k in diff:
        print("    %s\n      arm64 %s\n      %-5s %s" % (k, A[k][:200], c, B.get(k, "")[:200]))
    a = open(os.path.join(R, "arm64", "tiles.tsv")).read().splitlines()
    b = open(os.path.join(R, c, "tiles.tsv")).read().splitlines()
    cut = lambda l: l.split("\t")[:7]      # drop the per-tile timings and RSS
    nd = sum(1 for u, v in zip(a, b) if cut(u) != cut(v)) + abs(len(a) - len(b))
    print("   tiles.tsv: %d of %d tile rows differ (counts, LAZ sha256, DTM sha256)" % (nd, len(a) - 1))
PY
