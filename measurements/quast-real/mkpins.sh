#!/usr/bin/env bash
# Re-pin the scripts in pins.sha256 (the data pins below them are kept as staged), then upload the
# scripts and pins.
#
#   bash mkpins.sh <bucket>
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
S="prep.sh run.sh mkcontrols.py check.py assemblies.tsv spades-real.report.tsv"
grep -v -E ' (prep\.sh|run\.sh|mkcontrols\.py|check\.py|assemblies\.tsv|spades-real\.report\.tsv)$' pins.sha256 > pins.data
shasum -a 256 $S > pins.sha256
cat pins.data >> pins.sha256
rm pins.data
for f in $S pins.sha256; do
  aws s3 cp --only-show-errors "$f" "s3://${1:?bucket}/inputs/quast-real/$f"
done
wc -l pins.sha256
