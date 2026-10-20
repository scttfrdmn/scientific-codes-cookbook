#!/usr/bin/env bash
# Pull the three chips' outputs, keep the small evidence files under results/<chip>/, and write the
# cross-chip comparison of every RNAfold output line to results/compare.txt.
#
#   bash collect.sh <bucket> <scratch dir>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
SCR="${2:?pass a scratch directory}"
HERE="$(cd "$(dirname "$0")" && pwd)"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs ;; *) p=runs-$chip ;; esac
  mkdir -p "$SCR/$chip" "$HERE/results/$chip"
  aws s3 cp --recursive --only-show-errors "$BUCKET/$p/viennarna-real/rfam151/" "$SCR/$chip/"
  for f in checks.txt summary.txt times.txt host.txt; do cp "$SCR/$chip/$f" "$HERE/results/$chip/"; done
done
cp "$SCR/arm64/enum.tsv" "$HERE/results/arm64/"
# RNAfold -p writes 6 lines per sequence: header, sequence, MFE, ensemble, centroid, frequency
python3 -I - "$SCR" <<'PY' | tee "$HERE/results/compare.txt"
import gzip, hashlib, sys
scr = sys.argv[1]
names = ["header", "sequence", "mfe structure (energy)", "ensemble [G]", "centroid {E d=}", "frequency; diversity"]
lines = {c: gzip.open("%s/%s/fold.out.gz" % (scr, c), "rt").read().split("\n") for c in ("arm64", "x86_64", "amd")}
for c, L in lines.items():
    print("%-7s fold.out sha256 %s  lines %d" % (c, hashlib.sha256("\n".join(L).encode()).hexdigest()[:16], len(L)))
a = lines["arm64"]
for c in ("x86_64", "amd"):
    b = lines[c]
    assert len(a) == len(b)
    diff = [0] * 6
    ex = [None] * 6
    for k, (x, y) in enumerate(zip(a, b)):
        if x != y:
            diff[k % 6] += 1
            if ex[k % 6] is None:
                ex[k % 6] = (a[k - k % 6], x, y)
    print("== %s against Graviton4: records whose line differs (of %d)" % (c, len(a) // 6))
    for i in range(6):
        print("  %-26s %d" % (names[i], diff[i]))
        if ex[i]:
            print("     e.g. %s\n       arm64 %s\n       %-5s %s" % (ex[i][0], ex[i][1][-60:], c[:5], ex[i][2][-60:]))
PY
