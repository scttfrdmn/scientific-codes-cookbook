#!/usr/bin/env bash
# Pull each chip's evidence into results/<chip>/ (not the pairs or the .mcool) and compare every
# chip against Graviton4. Pixel digests are integer counts and asserted equal; the ICE weights are a
# converged fixed point and their cross-chip difference is reported.
#   bash measurements/hic-real/collect.sh <bucket> <run label> <task nonce>
set -euo pipefail
B="${1:?bucket}"; RUN="${2:?run label}"; NONCE="${3:?nonce}"
HERE="$(cd "$(dirname "$0")" && pwd)"
SCR="${XTMP:-$HOME/.xtmp}/hic-real/weights"
ACCT=$(aws sts get-caller-identity --query Account --output text)
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs; sfx=$NONCE ;; *) p=runs-$chip; sfx=${chip/_/}-$NONCE ;; esac
  for t in pairs cool; do
    aws s3 ls "s3://$B/$p/hic-real/$RUN/$t/check.txt" >/dev/null 2>&1 || { echo "$chip $t: no run"; continue; }
    mkdir -p "$HERE/results/$chip/$t" "$SCR/$chip"
    aws s3 cp --recursive --only-show-errors --exclude '*.gz' --exclude '*.mcool' --exclude '*.npy' \
      "s3://$B/$p/hic-real/$RUN/$t/" "$HERE/results/$chip/$t/"
    [ $t = cool ] && aws s3 cp --recursive --only-show-errors --exclude '*' --include '*.npy' \
      "s3://$B/$p/hic-real/$RUN/$t/" "$SCR/$chip/"
    aws s3 cp --only-show-errors "s3://spawn-results-$ACCT-us-west-2/tasks/cookbook-hic-real-$t-$RUN-$sfx/completion.json" \
      "$HERE/results/$chip/$t/completion.json" || true
  done
done
python3 -I - "$HERE/results" "$SCR" <<'PY'
import json, os, sys
from datetime import datetime
import numpy as np
R, S = sys.argv[1], sys.argv[2]
chips = [c for c in ("arm64", "x86_64", "amd") if os.path.exists(os.path.join(R, c, "cool", "check.txt"))]
def kv(p):
    d = {}
    for l in open(p):
        if "\t" in l:
            k, v = l.rstrip("\n").split("\t", 1)
            d[k] = v
    return d
def when(s):
    return datetime.strptime(s.split(".")[0].rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
out = []
def line(s):
    out.append(s); print(s)
for c in chips:
    for t in ("pairs", "cool"):
        d = os.path.join(R, c, t)
        j = json.load(open(os.path.join(d, "completion.json")))
        win = (when(j["ended_at"]) - when(j["started_at"])).total_seconds()
        hr = j["cost_estimate"] / win * 3600
        tim = [l.split("\t") for l in open(os.path.join(d, "timing.tsv")).read().splitlines()[1:]]
        k, h = kv(os.path.join(d, "check.txt")), kv(os.path.join(d, "host.txt"))
        tool = float(k["parallel_wall_s"]) if t == "pairs" else sum(float(x[1]) for x in tim)
        rss = max(int(x[3]) for x in tim) / 2**20
        line("%-6s %-5s %-34s tpc %s  tool %6.0f s  window %5.0f s  $/hr %.4f  compute $ %.4f  billed $ %.4f  "
             "peak rss %.2f GiB  cgroup %.2f GiB  %s" % (
                 c, t, h.get("cpu_model", "Graviton4 cpu_part " + h.get("cpu_part", "?")), h.get("threads_per_core"),
                 tool, win, hr, tool / 3600 * hr, j["cost_estimate"], rss,
                 int(k.get("cgroup_memory_peak_bytes", 0)) / 2**30, j.get("state")))
        for x in tim:
            line("         %-22s %8s s  rss %6.2f GiB" % (x[0], x[1], int(x[3]) / 2**20))
        if t == "cool":
            line("         openblas: %s" % h.get("openblas_corename"))
fail = []
ref = chips[0]
SAME = ["rep1_records", "rep2_records", "combined_4dn_records", "combined_ours_records", "combined_4dn_body_md5",
        "combined_ours_body_md5", "rep2_body_md5", "planted_kept_body_md5", "mapq_filter_removed",
        "dedup_rep1_dups", "dedup_rep2_dups", "dedup_planted_dups", "identity_combined", "identity_planted"]
for c in chips[1:]:
    a, b = kv(os.path.join(R, ref, "pairs", "check.txt")), kv(os.path.join(R, c, "pairs", "check.txt"))
    for k in SAME:
        if a.get(k) != b.get(k):
            fail.append("%s %s: %s vs %s" % (c, k, b.get(k), a.get(k)))
    da = open(os.path.join(R, ref, "cool", "digests.tsv")).read()
    db = open(os.path.join(R, c, "cool", "digests.tsv")).read()
    if da != db:
        fail.append("%s: pixel digests differ from %s" % (c, ref))
    line("%s vs %s: pairs checks %s, pixel digests %s" % (c, ref, "equal" if not fail else "DIFFER", "equal" if da == db else "DIFFER"))
    for f in sorted(os.listdir(os.path.join(S, ref))):
        wa, wb = np.load(os.path.join(S, ref, f)), np.load(os.path.join(S, c, f))
        m = ~np.isnan(wa)
        same_mask = bool(np.array_equal(np.isnan(wa), np.isnan(wb)))
        rel = float(np.max(np.abs(wa[m] - wb[m]) / np.abs(wa[m]))) if same_mask else float("nan")
        line("   %s %s: masks %s, max rel diff %.3e, byte-identical %s" % (
            c, f, "equal" if same_mask else "DIFFER", rel, bool(np.array_equal(wa[m], wb[m]))))
        if not same_mask:
            fail.append("%s %s: masked bins differ" % (c, f))
for f in fail:
    line("FAIL " + f)
open(os.path.join(R, "crosschip.txt"), "w").write("\n".join(out) + "\n")
sys.exit(1 if fail else 0)
PY
