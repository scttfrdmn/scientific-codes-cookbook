#!/usr/bin/env bash
# Pull each chip's evidence (reports, checks, logs, times) into results/<chip>/ and write the
# cross-chip comparison of fingerprints and reports to results/compare.txt.
#
#   bash collect.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
HERE="$(cd "$(dirname "$0")" && pwd)"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs ;; *) p=runs-$chip ;; esac
  aws s3 cp --recursive --only-show-errors --exclude '*.samples' \
    "$BUCKET/$p/cutadapt-real/zymo/" "$HERE/results/$chip/"
done
python3 - "$HERE/results" <<'PY' | tee "$HERE/results/compare.txt"
import json, os, sys
R = sys.argv[1]
def strip(j):
    # what is about the run, not the result: version strings, paths, timings, the -j count
    for k in ("cutadapt_version", "python_version", "command_line_arguments", "cores", "input"):
        j.pop(k, None)
    return j
for acc in ("ERR2984773", "ERR2935805"):
    print("==", acc)
    base = json.load(open(os.path.join(R, "arm64", acc, "fingerprints.json")))
    for chip in ("x86_64", "amd"):
        p = os.path.join(R, chip, acc, "fingerprints.json")
        if not os.path.exists(p):
            print(chip, "missing"); continue
        fp = json.load(open(p))
        diff = sorted(k for k in set(base) | set(fp) if base.get(k) != fp.get(k))
        print("fingerprints arm64 vs %-6s: %s" % (chip, "identical" if not diff else "differ in " + ", ".join(diff)))
        for name in sorted(os.listdir(os.path.join(R, "arm64", acc))):
            if name.endswith(".json") and name != "fingerprints.json":
                a = strip(json.load(open(os.path.join(R, "arm64", acc, name))))
                b = strip(json.load(open(os.path.join(R, chip, acc, name))))
                print("  report %-14s arm64 vs %-6s: %s" % (name, chip, "identical" if a == b else "DIFFER"))
PY
