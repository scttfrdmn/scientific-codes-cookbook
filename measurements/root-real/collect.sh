#!/usr/bin/env bash
# Pull each chip's evidence into results/<chip>/ and compare every chip against Graviton4.
#
#   bash measurements/root-real/collect.sh <bucket> <task nonce>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
NONCE="${2:?pass the nonce the specs were resolved with}"
HERE="$(cd "$(dirname "$0")" && pwd)"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs; id=cookbook-root-real-dimuon-$NONCE ;; *) p=runs-$chip; id=cookbook-root-real-dimuon-${chip/_/}-$NONCE ;; esac
  aws s3 ls "s3://$BUCKET/$p/root-real/dimuon/root.tsv" >/dev/null 2>&1 || { echo "$chip: no run yet"; continue; }
  mkdir -p "$HERE/results/$chip"
  aws s3 cp --recursive --only-show-errors "s3://$BUCKET/$p/root-real/dimuon/" "$HERE/results/$chip/"
  aws s3 cp --only-show-errors "s3://spawn-results-$(aws sts get-caller-identity --query Account --output text)-us-west-2/tasks/$id/completion.json" "$HERE/results/$chip/completion.json"
done
python3 -I - "$HERE/results" <<'PY'
import json, os, sys
from datetime import datetime
R = sys.argv[1]
def tsv(p):
    return dict(l.rstrip("\n").split("\t", 1) for l in open(p))
def when(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
chips = [c for c in ("arm64", "x86_64", "amd") if os.path.exists(os.path.join(R, c, "root.tsv"))]
rows = {}
for c in chips:
    a, k = tsv(os.path.join(R, c, "root.tsv")), tsv(os.path.join(R, c, "check.tsv"))
    j = json.load(open(os.path.join(R, c, "completion.json")))
    win = (when(j["ended_at"]) - when(j["started_at"])).total_seconds()
    hr = j["cost_estimate"] / win * 3600
    loop = float(a["event_loop_s"])
    rows[c] = (a, k)
    print("%-7s loop %6.2f s  cores %s  analysis %s s  check %s s  window %4d s  $/hr %.4f  compute $ %.5f  billed $ %.5f  rss %s/%s GiB  %s"
          % (c, loop, a["event_loop_avg_cores"], a["wall_s_analysis"], k["wall_s_check"], win, hr,
             loop / 3600 * hr, j["cost_estimate"], a["peak_rss_gib"], k["peak_rss_gib"], j["state"]))
SKIP = {"event_loop_s", "event_loop_cpu_s", "event_loop_avg_cores", "event_loop_mevents_per_s", "wall_s_analysis",
        "peak_rss_gib", "wall_s_check", "uproot_source_read_s", "uproot_snapshot_read_s", "os_root_bytes"}
for c in chips[1:]:
    print("== %s vs arm64" % c)
    for i, name in enumerate(("root.tsv", "check.tsv")):
        A, B = rows["arm64"][i], rows[c][i]
        same = [x for x in A if x not in SKIP and A.get(x) == B.get(x)]
        diff = [x for x in A if x not in SKIP and A.get(x) != B.get(x)]
        print("  %s: %d values identical; differ: %s" % (name, len(same), diff or "none"))
        for x in diff:
            print("    %s\n      arm64 %s\n      %-5s %s" % (x, A[x], c, B.get(x)))
    for h in ("hist-mass.txt", "hist-nmuon.txt"):
        a = open(os.path.join(R, "arm64", h)).read().split()
        b = open(os.path.join(R, c, h)).read().split()
        nd = sum(1 for x, y in zip(a, b) if x != y)
        print("  %s: %d of %d bins differ" % (h, nd, len(a)))
PY
