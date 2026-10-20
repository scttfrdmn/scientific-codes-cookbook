#!/usr/bin/env bash
# Pull each chip's evidence into results/<chip>/ and compare every chip against Graviton4: the
# timings and prices per chip, then every assembly and every gffcompare report, byte for byte.
#   bash measurements/transcript-assembly-real/collect.sh <bucket> <run label> <task nonce>
set -euo pipefail
B="${1:?bucket}"; RUN="${2:?run label}"; NONCE="${3:?nonce}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ACCT=$(aws sts get-caller-identity --query Account --output text)
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs; sfx=$NONCE ;; *) p=runs-$chip; sfx=${chip/_/}-$NONCE ;; esac
  for t in stringtie gffcompare; do
    aws s3 ls "s3://$B/$p/transcript-assembly-real/$RUN/$t/host.txt" >/dev/null 2>&1 || { echo "$chip $t: no run"; continue; }
    mkdir -p "$HERE/results/$chip/$t"
    # the assemblies and annotated GTFs are 50-200 MB each; keep them in the bucket, hash them here
    aws s3 cp --recursive --only-show-errors --exclude '*.gtf' --exclude '*.tmap' --exclude '*.refmap' \
      --exclude '*.loci' --exclude '*.tracking' --exclude '*.ids' \
      "s3://$B/$p/transcript-assembly-real/$RUN/$t/" "$HERE/results/$chip/$t/"
    aws s3 cp --only-show-errors "s3://spawn-results-$ACCT-us-west-2/tasks/cookbook-ta-real-$t-$RUN-$sfx/completion.json" \
      "$HERE/results/$chip/$t/completion.json" || true
  done
done
python3 -I - "$HERE/results" <<'PY'
import json, os, re, sys
from datetime import datetime
R = sys.argv[1]
chips = [c for c in ("arm64", "x86_64", "amd") if os.path.exists(os.path.join(R, c, "gffcompare", "check.txt"))]
def when(s):
    return datetime.strptime(s.split(".")[0].rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
out, fail = [], []
def line(s):
    out.append(s); print(s)
for c in chips:
    for t in ("stringtie", "gffcompare"):
        d = os.path.join(R, c, t)
        j = json.load(open(os.path.join(d, "completion.json")))
        win = (when(j["ended_at"]) - when(j["started_at"])).total_seconds()
        hr = j["cost_estimate"] / win * 3600
        h = dict(l.rstrip("\n").split("\t", 1) for l in open(os.path.join(d, "host.txt")) if "\t" in l)
        cpu = h.get("cpu_model") or ("Graviton4 cpu_part " + h["cpu_part"] if "cpu_part" in h else h.get("uname_m", "?"))
        line("%-7s %-11s window %5.0f s  $/hr %.4f  billed $ %.4f  %s  tpc %s  %s" % (
            c, t, win, hr, j["cost_estimate"], cpu, h.get("threads_per_core", "-"), j.get("state")))
        if t == "stringtie":
            for l in open(os.path.join(d, "timing.txt")):
                f = l.rstrip("\n").split("\t")
                wall, kb = float(f[4]), int(f[6])
                line("%-7s   %-10s wall %6.1f s  maxrss %.2f GiB  compute $ %.4f" % (c, f[0], wall, kb / 2**20, wall / 3600 * hr))
# the assemblies: check.json carries each one's sha256 (header comment set aside) and every count
ref = json.load(open(os.path.join(R, "arm64", "stringtie", "check.json")))
for c in chips[1:]:
    other = json.load(open(os.path.join(R, c, "stringtie", "check.json")))
    for k in sorted(ref):
        same = ref[k] == other[k]
        line("%-7s %-10s %s vs Graviton4 %s  [%s]" % (c, k, other[k].get("canonical_sha256", "-")[:12], ref[k].get("canonical_sha256", "-")[:12],
                                                      "identical" if same else "DIFFER"))
        if not same:
            fail.append("%s %s" % (c, k))
    a = open(os.path.join(R, "arm64", "gffcompare", "check.txt")).read()
    b = open(os.path.join(R, c, "gffcompare", "check.txt")).read()
    line("%-7s gffcompare/check.txt %s" % (c, "identical" if a == b else "DIFFER"))
    if a != b:
        fail.append("%s gffcompare" % c)
line("CROSS-CHIP IDENTICAL" if not fail else "CROSS-CHIP DIFFERS: %s" % fail)
open(os.path.join(R, "crosschip.txt"), "w").write("\n".join(out) + "\n")
PY
