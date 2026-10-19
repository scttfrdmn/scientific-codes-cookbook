#!/usr/bin/env bash
# Pull each chip's evidence into results/<chip>/ (frames excepted) and compare every chip with Graviton4.
#
#   bash measurements/paraview-real/collect.sh <bucket> <task nonce>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
NONCE="${2:?pass the nonce the specs were resolved with}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ACCT=$(aws sts get-caller-identity --query Account --output text)
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs; id=cookbook-paraview-real-week-$NONCE ;; *) p=runs-$chip; id=cookbook-paraview-real-week-${chip/_/}-$NONCE ;; esac
  aws s3 ls "s3://$BUCKET/$p/paraview-real/week/hours.tsv" >/dev/null 2>&1 || { echo "$chip: no run yet"; continue; }
  mkdir -p "$HERE/results/$chip"
  aws s3 cp --recursive --only-show-errors --exclude 'frames/*' "s3://$BUCKET/$p/paraview-real/week/" "$HERE/results/$chip/"
  # frames are compared by content hash; one frame is kept as the picture
  aws s3 cp --only-show-errors "s3://$BUCKET/$p/paraview-real/week/frames/f000.png" "$HERE/results/$chip/f000.png"
  aws s3 ls "s3://$BUCKET/$p/paraview-real/week/frames/" | wc -l | awk '{print "frames_in_bucket\t"$1}' > "$HERE/results/$chip/frames.txt"
  aws s3api list-objects-v2 --bucket "$BUCKET" --prefix "$p/paraview-real/week/frames/" --query 'Contents[].[Key,ETag]' --output text \
    | sed 's|.*/||' | sort >> "$HERE/results/$chip/frames.txt"
  aws s3 cp --only-show-errors "s3://spawn-results-$ACCT-us-west-2/tasks/$id/completion.json" "$HERE/results/$chip/completion.json"
done
python3 -I - "$HERE/results" <<'PY'
import csv, json, os, sys
from datetime import datetime
R = sys.argv[1]
def when(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
chips = [c for c in ("arm64", "x86_64", "amd") if os.path.exists(os.path.join(R, c, "hours.tsv"))]
rows, frames = {}, {}
for c in chips:
    rows[c] = list(csv.DictReader(open(os.path.join(R, c, "hours.tsv")), delimiter="\t"))
    frames[c] = [l.split("\t") for l in open(os.path.join(R, c, "frames.txt")).read().split("\n")[1:] if l]
    summ = dict(l.split("\t") for l in open(os.path.join(R, c, "summary.tsv")).read().split("\n") if l)
    tim = dict(l.split(" ", 1) for l in open(os.path.join(R, c, "times.txt")).read().split("\n") if l and " " in l)
    j = json.load(open(os.path.join(R, c, "completion.json")))
    win = (when(j["ended_at"]) - when(j["started_at"])).total_seconds()
    hr = j["cost_estimate"] / win * 3600
    wall = float(summ["wall_s"])
    cg = tim.get("cgroup_peak_bytes", "nan").split()[0]
    print("%-7s %-7s pvbatch %7.1f s  window %5d s  $/hr %.4f  compute $ %.4f  billed $ %.4f  rss %s GiB  cgroup %.2f GiB"
          % (c, j["state"], wall, win, hr, wall / 3600 * hr, j["cost_estimate"], summ["peak_rss_gib"],
             float(cg) / 2 ** 30))
    for k in ("t_read", "t_contour", "t_edges", "t_clip", "t_integrate", "t_render", "t_numpy"):
        print("        %-12s %7.1f s" % (k, sum(float(r[k]) for r in rows[c])))
KEYS = [k for k in rows["arm64"][0] if not k.startswith("t_")]
for c in chips[1:]:
    print("== %s vs arm64 (%d hours)" % (c, len(rows[c])))
    for k in KEYS:
        nd = sum(1 for a, b in zip(rows["arm64"], rows[c]) if a[k] != b[k])
        if nd == 0:
            continue
        try:
            w = max(abs(float(a[k]) - float(b[k])) / abs(float(a[k])) for a, b in zip(rows["arm64"], rows[c]) if float(a[k]))
        except ValueError:
            w = float("nan")
        print("  %-12s differs in %3d hours, worst relative %.2e" % (k, nd, w))
    same = [k for k in KEYS if all(a[k] == b[k] for a, b in zip(rows["arm64"], rows[c]))]
    print("  identical in every hour: %s" % ", ".join(same))
    fa, fb = dict(frames["arm64"]), dict(frames[c])
    print("  frames: %d of %d byte-identical" % (sum(1 for k in fa if fa[k] == fb.get(k)), len(fa)))
PY
