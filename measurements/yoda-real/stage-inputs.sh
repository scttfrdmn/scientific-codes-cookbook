#!/usr/bin/env bash
# Stage the yoda-real inputs and scripts.
#
#   bash measurements/yoda-real/stage-inputs.sh <bucket>
#
# Data, two sources:
#  * CMS Open Data record 12341 (DOI 10.7483/OPENDATA.CMS.LVG5.QT81), the 2.2 GB NanoAOD root-real
#    already staged -- reused, not copied: run measurements/root-real/stage-inputs.sh first. With
#    it, root-real's committed ROOT histograms of the same bytes (results/arm64/hist-*.txt,
#    identical on all three chips), which fill.py must reproduce.
#  * Rivet 4.1.4's reference data: the analyses/ tree of hepcedar/rivet at commit 74816bd4cf29
#    (the rivet-4.1.4 tag), fetched by a spawn task ON AWS (00-fetch.task.json runs fetch.py)
#    into the bucket. Tier: a versioned release of the code's own repository. Pinned twice: the
#    tarball's sha256, and refs.manifest (each reference file's sha256, a function of the commit).
set -euo pipefail

BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOTDIR="$(cd "$HERE/../.." && pwd)"
P="s3://$BUCKET/inputs/yoda-real"

cd "$HERE"
grep -E ' (fetch\.py|real\.sh|fill\.py|refs\.py)$' pins.sha256 | shasum -a 256 -c
for f in fetch.py real.sh fill.py refs.py pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$f" "$P/$f"
done
for f in hist-mass.txt hist-nmuon.txt; do
  want=$(awk -v f="$f" '$2==f{print $1}' pins.sha256)
  got=$(shasum -a 256 "$ROOTDIR/measurements/root-real/results/arm64/$f" | awk '{print $1}')
  [ "$got" = "$want" ] || { echo "FAIL: root-real's $f is $got, pinned $want" >&2; exit 1; }
  aws s3 cp --region "$REGION" --only-show-errors "$ROOTDIR/measurements/root-real/results/arm64/$f" "$P/root/$f"
done
cms=$(aws s3 cp --region "$REGION" --only-show-errors "s3://$BUCKET/inputs/root-real/data/Run2012BC_DoubleMuParked_Muons.root.sha256" - | awk '{print $1}')
[ "$cms" = "$(awk '$2=="Run2012BC_DoubleMuParked_Muons.root"{print $1}' pins.sha256)" ] \
  || { echo "FAIL: stage root-real first (its record 12341 file is missing or not the pinned bytes)" >&2; exit 1; }

check() {
  got=$(aws s3 cp --region "$REGION" --only-show-errors "$P/rivet/$1" - 2>/dev/null | awk '{print $1}')
  [ -n "$got" ] && [ "$got" = "$(awk -v f="$2" '$2==f{print $1}' pins.sha256)" ]
}
manifest_ok() {
  got=$(aws s3 cp --region "$REGION" --only-show-errors "$P/rivet/refs.manifest" - 2>/dev/null | shasum -a 256 | awk '{print $1}')
  [ "$got" = "$(awk '$2=="refs.manifest"{print $1}' pins.sha256)" ]
}
if ! check rivet-4.1.4-analyses.tar.gz.sha256 rivet-4.1.4-analyses.tar.gz || ! manifest_ok; then
  spawn task run --spec "$(python3 "$ROOTDIR/scripts/spec_arch.py" yoda arm64 "$BUCKET" "$(date +%s)" "$HERE/00-fetch.task.json")" --wait
  check rivet-4.1.4-analyses.tar.gz.sha256 rivet-4.1.4-analyses.tar.gz || { echo "FAIL: the Rivet tarball is not the pinned bytes" >&2; exit 1; }
  manifest_ok || { echo "FAIL: refs.manifest is not the pinned manifest" >&2; exit 1; }
fi
echo "staged to $P/"
