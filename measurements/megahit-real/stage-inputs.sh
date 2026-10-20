#!/usr/bin/env bash
# Stage the megahit-real inputs.
#
# Reads: ERR2984773, the whole MiSeq 2x151 run of the ZymoBIOMICS D6300 even standard (Nicholls et
# al. 2019, PRJEB29504), 8,785,731 pairs, 2.65 Gbp. Tier: RODA. metaphlan-real already copied it
# from the AWS Open Data SRA mirror and converted it once, tied to ENA's read and base counts; this
# recipe reuses those bytes (same sha256, in pins.sha256), it does not stage a second copy.
#
# Truth: Zymo's own reference genomes for the standard, ZymoBIOMICS.STD.refseq.v2.zip, from Zymo's
# public S3 bucket, copied server-side by object VERSION id (immutable), pinned by sha256.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
P="$BUCKET/inputs/megahit-real"
ZIP=ZymoBIOMICS.STD.refseq.v2.zip
ZIP_VERSION=EUTQETaO74jRt1OlOC0beobnF_uT1PSS

for m in 1 2; do
  aws s3 ls --region "$REGION" "$BUCKET/inputs/metaphlan-real/prep/ERR2984773/ERR2984773_$m.fastq.gz" >/dev/null \
    || { echo "stage metaphlan-real first: bash measurements/metaphlan-real/stage-inputs.sh" >&2; exit 1; }
done
if ! aws s3 ls --region "$REGION" "$P/$ZIP" >/dev/null 2>&1; then
  aws s3api copy-object --region "$REGION" --copy-source "zymo-files/BioPool/$ZIP?versionId=$ZIP_VERSION" \
    --bucket "${BUCKET#s3://}" --key "inputs/megahit-real/$ZIP" >/dev/null
fi
for f in assemble.sh evaluate.sh canon.py compare.py pins.sha256; do
  aws s3 cp --only-show-errors --region "$REGION" "$HERE/$f" "$P/$f"
done
echo "staged under $P/ -- every task checks its inputs against pins.sha256"
