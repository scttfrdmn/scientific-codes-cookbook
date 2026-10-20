#!/usr/bin/env bash
# Stage measurements/popgen-real. The data is NOT copied again: the 22 Phase 3 autosome VCFs and
# the sample panel are the bytes measurements/plink-real staged under inputs/plink-real/ (RODA,
# s3://1000genomes/release/20130502/), read in place by the spec and re-hashed on the box against
# pins.sha256 here. Staged under inputs/popgen-real/: the scripts, the pins, and PLINK 1.9's
# per-chromosome SNP counts from plink-real's verified arm64 run (the cross-code check).
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DST="s3://$BUCKET/inputs/popgen-real"

for f in ALL.chr22.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz \
         integrated_call_samples_v3.20130502.ALL.panel; do
  aws s3 ls --region "$REGION" "s3://$BUCKET/inputs/plink-real/$f" >/dev/null \
    || { echo "FAIL: inputs/plink-real/ is not staged; run measurements/plink-real/stage-inputs.sh first" >&2; exit 1; }
done

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cp "$ROOT/measurements/plink-real/results/arm64/id-all.txt" "$tmp/plink-real-id-all.txt"
{
  cat "$ROOT/measurements/plink-real/vcf.sha256"
  echo "b4023dc6ee2d62ee89c8d4d347db4d348e65518d66d346574cdae7a4bbd76858  integrated_call_samples_v3.20130502.ALL.panel"
  ( cd "$tmp" && shasum -a 256 plink-real-id-all.txt )
} > "$tmp/pins.sha256"
for f in run.sh real.py colhash.py; do cp "$HERE/$f" "$tmp/$f"; done
for f in run.sh real.py colhash.py pins.sha256 plink-real-id-all.txt; do
  aws s3 cp --region "$REGION" --only-show-errors "$tmp/$f" "$DST/$f"
done
cat "$tmp/pins.sha256" | tail -2
echo "staged $DST/"
