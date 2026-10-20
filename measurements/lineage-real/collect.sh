#!/usr/bin/env bash
# Pull each chip's evidence into results/<chip>/ (logs, timings, checks -- not the 100k-row call
# files, which stay in the bucket and are compared here by the digests check.py wrote), then
# compare every chip against Graviton4.
#   bash measurements/lineage-real/collect.sh <bucket> <run label> <task nonce>
set -euo pipefail
B="${1:?bucket}"; RUN="${2:?run label}"; NONCE="${3:?nonce}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ACCT=$(aws sts get-caller-identity --query Account --output text)
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs; sfx=$NONCE ;; *) p=runs-$chip; sfx=${chip/_/}-$NONCE ;; esac
  for t in nextclade pangolin; do
    aws s3 ls "s3://$B/$p/lineage-real/$RUN/$t/host.txt" >/dev/null 2>&1 || { echo "$chip $t: no run"; continue; }
    mkdir -p "$HERE/results/$chip/$t"
    aws s3 cp --recursive --only-show-errors --exclude '*.csv' --exclude '*.gz' --exclude '*.tsv' --exclude '*usher.log' \
      --include 'timing.tsv' --include 'digests.tsv' \
      "s3://$B/$p/lineage-real/$RUN/$t/" "$HERE/results/$chip/$t/"
    aws s3 cp --only-show-errors "s3://spawn-results-$ACCT-us-west-2/tasks/cookbook-lineage-real-$t-$RUN-$sfx/completion.json" \
      "$HERE/results/$chip/$t/completion.json" || true
  done
done
python3 -I - "$HERE/results" <<'PY'
import json, os, sys
from datetime import datetime
R = sys.argv[1]
chips = [c for c in ("arm64", "x86_64", "amd") if os.path.exists(os.path.join(R, c, "pangolin", "check.json"))]
def when(s):
    return datetime.strptime(s.split(".")[0].rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
out, fail = [], []
def line(s):
    out.append(s); print(s)
for c in chips:
    for t in ("nextclade", "pangolin"):
        d = os.path.join(R, c, t)
        j = json.load(open(os.path.join(d, "completion.json")))
        win = (when(j["ended_at"]) - when(j["started_at"])).total_seconds()
        hr = j["cost_estimate"] / win * 3600
        host = " | ".join(l.strip() for l in open(os.path.join(d, "host.txt"))
                          if l.split(":")[0].split(" ")[0] in ("Model", "Thread(s)", "threads_per_core_cpu0", "arch", "model", "CPU"))
        line("%-7s %-9s window %5.0f s  $/hr %.4f  billed $ %.4f  %s  [%s]" % (c, t, win, hr, j["cost_estimate"], j.get("state"), host))
        for l in open(os.path.join(d, "timing.tsv")):
            f = l.rstrip("\n").split("\t")
            if "wall_s" not in f:
                line("%-7s   %s" % (c, "  ".join(f)))
                continue
            wall = float(f[f.index("wall_s") + 1])
            mem = ("maxrss %.2f GiB" % (int(f[f.index("maxrss_kb") + 1]) / 2**20)) if "maxrss_kb" in f else \
                  ("cgroup peak %.2f GiB" % (int(f[f.index("cgroup_memory_peak_bytes") + 1]) / 2**30)
                   if "cgroup_memory_peak_bytes" in f and f[f.index("cgroup_memory_peak_bytes") + 1].isdigit() else "")
            line("%-7s   %-18s wall %7.1f s  %s  compute $ %.4f" % (c, f[0], wall, mem, wall / 3600 * hr))
ref = dict(l.rstrip("\n").split("\t") for l in open(os.path.join(R, "arm64", "pangolin", "digests.tsv")))
refc = json.load(open(os.path.join(R, "arm64", "pangolin", "check.json")))
for c in chips[1:]:
    other = dict(l.rstrip("\n").split("\t") for l in open(os.path.join(R, c, "pangolin", "digests.tsv")))
    for k in ref:
        same = ref[k] == other.get(k)
        line("%-7s %-22s %s vs Graviton4 %s  [%s]" % (c, k, other.get(k, "-")[:12], ref[k][:12], "identical" if same else "DIFFER"))
        if not same:
            fail.append("%s %s" % (c, k))
    oc = json.load(open(os.path.join(R, c, "pangolin", "check.json")))
    for k in sorted(refc):
        if k.startswith("t_") or k.startswith("x_"):
            same = refc[k] == oc.get(k)
            line("%-7s %-40s [%s]" % (c, k, "identical" if same else "DIFFER"))
            if not same:
                fail.append("%s %s" % (c, k))
line("CROSS-CHIP IDENTICAL" if not fail else "CROSS-CHIP DIFFERS: %s" % fail)
open(os.path.join(R, "crosschip.txt"), "w").write("\n".join(out) + "\n")
sys.exit(1 if fail else 0)
PY
