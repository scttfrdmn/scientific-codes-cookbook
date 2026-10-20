#!/usr/bin/env bash
# Pull each chip's evidence into results/<chip>/<tool>/ with spawn's completion record, and write
# results/summary.txt: one line per chip and tool, then every fixed point against Graviton4.
#   bash measurements/bwa-mem2-real/collect.sh <bucket> <nonce>
set -euo pipefail
B="${1:?bucket}"; N="${2:?task nonce}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ACCT=$(aws sts get-caller-identity --query Account --output text)
R="s3://spawn-results-$ACCT-us-west-2/tasks"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs; id=$N ;; *) p=runs-$chip; id=${chip/_/}-$N ;; esac
  for t in bwa mem2; do
    aws s3 ls "s3://$B/$p/bwa-mem2-real/$t/out/checks.tsv" > /dev/null 2>&1 || { echo "$chip $t: no run"; continue; }
    mkdir -p "$HERE/results/$chip/$t"
    aws s3 cp --recursive --only-show-errors --exclude '*.header.txt' "s3://$B/$p/bwa-mem2-real/$t/out/" "$HERE/results/$chip/$t/"
    aws s3 cp --only-show-errors "$R/cookbook-bwamem2-real-$t-$id/completion.json" "$HERE/results/$chip/$t/completion.json"
  done
done
python3 -I - "$HERE/results" <<'PY'
import json, os, sys
from datetime import datetime
R = sys.argv[1]
RUNS = ["SRR062634", "SRR062635", "SRR062641"]
def kv(p):
    return dict(l.rstrip("\n").split("\t", 1) for l in open(p) if "\t" in l)
def when(s):
    return datetime.strptime(s.split(".")[0].rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
# fixed points of the input bytes: what was read, and every byte the aligner wrote per run
FIXED = [k for r in RUNS for k in ("%s_body_sha256" % r, "%s_body_nomq_sha256" % r, "%s_records" % r,
         "%s_primary_mapped" % r, "%s_proper_pair" % r, "%s_supplementary" % r, "%s_secondary" % r,
         "%s_records_with_mq" % r)] + ["total_records"]
out, data, fail = [], {}, []
for c in ("arm64", "x86_64", "amd"):
    for t in ("bwa", "mem2"):
        d = os.path.join(R, c, t)
        if not os.path.exists(os.path.join(d, "results.tsv")):
            continue
        k = kv(os.path.join(d, "results.tsv")); data[(c, t)] = k
        j = json.load(open(os.path.join(d, "completion.json")))
        win = (when(j["ended_at"]) - when(j["started_at"])).total_seconds()
        hr = j["cost_estimate"] / win * 3600
        wall = float(k["total_align_wall_s"])
        nfail = sum(1 for l in open(os.path.join(d, "checks.tsv")) if l.rstrip("\n").split("\t")[3] != "ok")
        cores = " ".join(k.get("%s_avg_cores" % r, "?") for r in RUNS)
        out.append("%-7s %-5s %-6s %-26s tpc %s  align %5.0f s  pairs/s %6s  window %5.0f s  $/hr %.4f  "
                   "compute $ %.4f  billed $ %.4f  peak rss %.2f GiB  avg cores %s  kernel %s  %s  failed checks %d" % (
                       c, t, {"arm64": "r8g.4xlarge", "x86_64": "r8i.4xlarge", "amd": "r8a.4xlarge"}[c], k.get("cpu_model"), k.get("threads_per_core"), wall,
                       k.get("pairs_per_s"), win, hr, wall / 3600 * hr, j["cost_estimate"],
                       int(k["max_vmhwm_kib"]) / 2**20, cores, k.get("%s_kernel" % RUNS[0], "-"),
                       j.get("state"), nfail))
for t in ("bwa", "mem2"):
    shas = {c: {x: v for x, v in data[(c, t)].items() if x.startswith("sha256_")} for c in ("arm64", "x86_64", "amd") if (c, t) in data}
    for c in ("x86_64", "amd"):
        if c not in shas or "arm64" not in shas:
            continue
        same = shas[c] == shas["arm64"] and len(shas[c]) > 0
        if not same:
            fail.append("%s %s inputs" % (c, t))
        out.append("%-7s %-5s %-34s [%s]" % (c, t, "every staged input's sha256", "same" if same else "DIFFERS"))
        for key in FIXED:
            a, b = data[("arm64", t)].get(key), data[(c, t)].get(key)
            if a != b:
                fail.append("%s %s %s" % (c, t, key))
            out.append("%-7s %-5s %-34s %s vs Graviton4 %s  [%s]" % (c, t, key, b, a, "same" if a == b else "DIFFERS"))
out.append("ALL AGREE WITH GRAVITON4" if not fail else "DIFFER FROM GRAVITON4: %s" % fail)
print("\n".join(out))
open(os.path.join(R, "summary.txt"), "w").write("\n".join(out) + "\n")
PY
