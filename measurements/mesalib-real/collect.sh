#!/usr/bin/env bash
# Pull each chip's evidence into results/<chip>/ (the 4K frame excepted) and compare every chip with Graviton4.
#
#   bash measurements/mesalib-real/collect.sh <bucket> <task nonce>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
NONCE="${2:?pass the nonce the specs were resolved with}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ACCT=$(aws sts get-caller-identity --query Account --output text)
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs; id=cookbook-mesalib-real-$NONCE ;; *) p=runs-$chip; id=cookbook-mesalib-real-${chip/_/}-$NONCE ;; esac
  aws s3 ls "s3://$BUCKET/$p/mesalib-real/r1/exact.tsv" >/dev/null 2>&1 || { echo "$chip: no run yet"; continue; }
  mkdir -p "$HERE/results/$chip"
  aws s3 cp --recursive --only-show-errors --exclude 'frame00.png' "s3://$BUCKET/$p/mesalib-real/r1/" "$HERE/results/$chip/"
  aws s3api head-object --bucket "$BUCKET" --key "$p/mesalib-real/r1/frame00.png" --query ETag --output text \
    | sed 's/^/frame00.png ETag /' > "$HERE/results/$chip/frame00.etag" 2>/dev/null || true
  aws s3 cp --only-show-errors "s3://spawn-results-$ACCT-us-west-2/tasks/$id/completion.json" "$HERE/results/$chip/completion.json"
done
python3 -I - "$HERE/results" <<'PY'
import json, os, sys
from datetime import datetime
R = sys.argv[1]
def when(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
def tsv(c, n):
    p = os.path.join(R, c, n)
    return dict(l.split("\t", 1) for l in open(p).read().split("\n") if "\t" in l) if os.path.exists(p) else {}
chips = [c for c in ("arm64", "x86_64", "amd") if os.path.exists(os.path.join(R, c, "exact.tsv"))]
shade = {}
for c in chips:
    j = json.load(open(os.path.join(R, c, "completion.json")))
    win = (when(j["ended_at"]) - when(j["started_at"])).total_seconds()
    hr = j["cost_estimate"] / win * 3600
    tim = {}
    for l in open(os.path.join(R, c, "times.txt")):
        f = l.split()
        if len(f) >= 5 and f[1] == "exit":
            tim[f[0]] = (int(f[2]), int(f[4]))
    tool = sum(w for k, (rc, w) in tim.items() if k != "backends")
    rss = max(float(tsv(c, n).get("peak_rss_gib", "nan")) for n in os.listdir(os.path.join(R, c)) if n.endswith(".tsv") and n != "backends.tsv")
    print("%-7s %-9s tool %5d s  window %5d s  $/hr %.4f  compute $ %.4f  billed $ %.4f  peak rss %.2f GiB"
          % (c, j["state"], tool, win, hr, tool / 3600 * hr, j["cost_estimate"], rss))
    print("        stages: " + "  ".join("%s %ds" % (k, w) for k, (rc, w) in tim.items()))
    shade[c] = {}
    for n in sorted(os.listdir(os.path.join(R, c))):
        if n.startswith("shade-t") and n.endswith(".json"):
            s = json.load(open(os.path.join(R, c, n)))
            shade[c][int(s["threads"])] = s
            t = s["times"]
            print("        shade %2s threads: build %.1f s, first frame %.2f s, mean of rest %.2f s"
                  % (s["threads"], s["build_s"], t[0], sum(t[1:]) / max(1, len(t) - 1)))
    th = sorted(shade[c])
    same = all(shade[c][t]["sha256"] == shade[c][th[0]]["sha256"] for t in th)
    print("        frames identical across thread counts %s: %s" % (th, same))
    e = tsv(c, "exact.tsv")
    for k in sorted(e):
        if k.startswith(("id_", "depth_", "flood", "control", "above")) and not k.endswith("_s"):
            print("        %-22s %s" % (k, e[k]))
for c in chips[1:]:
    a, b = tsv("arm64", "exact.tsv"), tsv(c, "exact.tsv")
    keys = [k for k in a if k.endswith("sha256") or k.startswith(("id_", "flood_", "control_", "above"))]
    keys = [k for k in keys if not k.endswith("_s")]
    diff = [k for k in keys if a.get(k) != b.get(k)]
    print("== %s vs arm64: exact rows differing: %s" % (c, ", ".join(diff) or "none"))
    for t in sorted(set(shade["arm64"]) & set(shade[c])):
        sa, sb = shade["arm64"][t]["sha256"], shade[c][t]["sha256"]
        print("   shaded frames at %d threads: %d of %d byte-identical" % (t, sum(x == y for x, y in zip(sa, sb)), len(sa)))
PY
