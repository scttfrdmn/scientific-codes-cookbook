#!/usr/bin/env bash
# Stage the phasing-real inputs.
#
#   VCFs     GIAB NISTv4.2.1 HG002 GRCh38 (RODA s3://giab/release/...): the benchmark and its two
#            phased supplements, copied server-side into the bucket
#   reads    read by 00-prep over HTTPS, chr20 only, through the publisher's md5-checked index:
#            PacBio HiFi Revio 48x from s3://giab, Illumina 2x250 from NCBI's GIAB FTP
#   ref      GRCh38 chr20, already in the bucket from recipes/bwa-samtools
#   scripts  lib.sh, prep.sh, phase.sh, check.py -- every task checks its staged inputs, scripts
#            included, against pins.sha256 before it runs
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
P="$BUCKET/inputs/phasing-real"
GIAB=s3://giab/release/AshkenazimTrio/HG002_NA24385_son/NISTv4.2.1/GRCh38
V=HG002_GRCh38_1_22_v4.2.1_

aws s3 ls --region "$REGION" "$BUCKET/inputs/bwa-samtools/chr20.fa" > /dev/null \
  || { echo "missing chr20.fa -- stage recipes/bwa-samtools first" >&2; exit 1; }

for f in "${V}benchmark.vcf.gz" "SupplementaryFiles/${V}benchmark_hifiasm_v11_phasetransfer.vcf.gz" \
         "SupplementaryFiles/${V}benchmark_phased_MHCassembly_StrandSeqANDTrio.vcf.gz"; do
  for x in "" .tbi; do
    aws s3 ls --region "$REGION" "$P/giab/${f##*/}$x" > /dev/null 2>&1 \
      || aws s3 cp --only-show-errors --source-region us-east-1 --region "$REGION" "$GIAB/$f$x" "$P/giab/${f##*/}$x"
  done
done

( cd "$HERE" && grep -E ' (lib\.sh|prep\.sh|phase\.sh|check\.py)$' pins.sha256 | shasum -a 256 -c - )
for f in lib.sh prep.sh phase.sh check.py pins.sha256; do
  aws s3 cp --only-show-errors --region "$REGION" "$HERE/$f" "$P/$f"
done

if ! aws s3 ls --region "$REGION" "$P/prep/prep.sha256" > /dev/null 2>&1; then
  spec=$(python3 "$ROOT/scripts/spec_arch.py" phasing arm64 "${BUCKET#s3://}" "$(date +%s)" "$HERE/00-prep.task.json")
  spawn task run --spec "$spec" --region "$REGION" --wait
fi
# what 00-prep wrote and measured must be what pins.sha256 pins
aws s3 cp --only-show-errors --region "$REGION" "$P/prep/prep.sha256" - | while read -r h f; do
  grep -q "^$h  $f\$" "$HERE/pins.sha256" || { echo "staged $f ($h) is not the pinned file" >&2; exit 1; }
done
aws s3 cp --only-show-errors --region "$REGION" "$P/prep/results.tsv" - | awk -F'\t' '$1 ~ /_vcf_sha256$/{print $2}' \
  | while read -r h; do grep -q "^$h  " "$HERE/pins.sha256" || { echo "a GIAB VCF ($h) is not the pinned file" >&2; exit 1; }; done
echo "staged $P/"
