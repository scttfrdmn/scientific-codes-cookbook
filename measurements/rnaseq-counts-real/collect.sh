#!/usr/bin/env bash
# Pull each chip's evidence into results/<chip>/ and compare every chip against Graviton4. Counts
# are integers from a deterministic computation on identical bytes, so the count tables, the
# featureCounts summary and every accounting line are asserted identical across chips.
#   bash measurements/rnaseq-counts-real/collect.sh <bucket> <run label> <task nonce>
set -euo pipefail
B="${1:?bucket}"; RUN="${2:?run label}"; NONCE="${3:?nonce}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ACCT=$(aws sts get-caller-identity --query Account --output text)
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs; sfx=$NONCE ;; *) p=runs-$chip; sfx=${chip/_/}-$NONCE ;; esac
  for t in featurecounts htseq; do
    aws s3 ls "s3://$B/$p/rnaseq-counts-real/$RUN/$t/check.txt" >/dev/null 2>&1 || { echo "$chip $t: no run"; continue; }
    mkdir -p "$HERE/results/$chip/$t"
    aws s3 cp --recursive --only-show-errors --exclude 'fc_counts_T*' --exclude 'htseq.txt' \
      "s3://$B/$p/rnaseq-counts-real/$RUN/$t/" "$HERE/results/$chip/$t/"
    aws s3 cp --only-show-errors "s3://spawn-results-$ACCT-us-west-2/tasks/cookbook-rnaseq-counts-real-$t-$RUN-$sfx/completion.json" \
      "$HERE/results/$chip/$t/completion.json" || true
  done
done
python3 -I - "$HERE/results" <<'PY'
import hashlib, json, os, sys
from datetime import datetime
R = sys.argv[1]
chips = [c for c in ("arm64", "x86_64", "amd") if os.path.exists(os.path.join(R, c, "htseq", "check.txt"))]
def when(s):
    return datetime.strptime(s.split(".")[0].rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()
out, fail = [], []
def line(s):
    out.append(s); print(s)
for c in chips:
    for t in ("featurecounts", "htseq"):
        d = os.path.join(R, c, t)
        j = json.load(open(os.path.join(d, "completion.json")))
        win = (when(j["ended_at"]) - when(j["started_at"])).total_seconds()
        hr = j["cost_estimate"] / win * 3600
        tim = [l.split("\t") for l in open(os.path.join(d, "timing.txt"))]
        h = dict(l.rstrip("\n").split("\t", 1) for l in open(os.path.join(d, "host.txt")) if "\t" in l)
        for f in tim:
            wall, hwm = float(f[3]), int(f[5])
            line("%-7s %-14s %-9s %-28s tpc %s  wall %5.0f s  window %5.0f s  $/hr %.4f  compute $ %.4f  "
                 "billed $ %.4f  vmhwm %.2f GiB  %s  %s" % (
                     c, t, f[0].replace("_rc", ""), h.get("cpu_model", "Graviton4 cpu_part " + h.get("cpu_part", "?")),
                     h.get("threads_per_core"), wall, win, hr, wall / 3600 * hr, j["cost_estimate"], hwm / 2**20,
                     h.get("openblas_corename", ""), j.get("state")))
# check.txt carries no wall time, so the whole file -- every count and accounting line -- must match
FILES = [("featurecounts", "fc_counts.tsv"), ("featurecounts", "fc.summary"), ("featurecounts", "check.txt"),
         ("htseq", "htseq_counts.tsv"), ("htseq", "check.txt")]
for c in chips[1:]:
    for t, f in FILES:
        a, b = sha(os.path.join(R, "arm64", t, f)), sha(os.path.join(R, c, t, f))
        line("%-7s %-28s %s vs Graviton4 %s  [%s]" % (c, t + "/" + f, b[:12], a[:12], "ok" if a == b else "FAIL"))
        if a != b:
            fail.append("%s %s/%s" % (c, t, f))
line("CROSS-CHIP PASSED" if not fail else "CROSS-CHIP FAILED %s" % fail)
open(os.path.join(R, "crosschip.txt"), "w").write("\n".join(out) + "\n")
sys.exit(1 if fail else 0)
PY
