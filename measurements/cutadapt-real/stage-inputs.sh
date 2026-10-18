#!/usr/bin/env bash
# Stage the cutadapt-real inputs. The reads are the two whole Illumina runs of the ZymoBIOMICS
# standards from Nicholls et al. 2019 (GigaScience 8:giz043, PRJEB29504), Kapa HyperPlus libraries
# with TruSeq Y-adapters -- the recipe's adapters:
#   ERR2984773  D6300 even  MiSeq 2x151        8,785,731 pairs, 2.65 Gbp
#   ERR2935805  D6310 log   HiSeq 1500 2x101  47,832,553 pairs, 9.66 Gbp
# Tier: RODA. measurements/metaphlan-real/stage-inputs.sh copied the .sra runs server-side from the
# AWS Open Data SRA mirror and dumped them once to FASTQ.gz, tied to ENA's read_count and
# base_count; this measurement reads those same objects (a second copy would be a second conversion
# to keep true). Their sha256 are in pins.sha256 and are checked inside the task. Only the scripts
# are uploaded here; nothing large passes through this machine.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
for a in ERR2984773 ERR2935805; do
  for m in 1 2; do
    aws s3 ls --region "$REGION" "$BUCKET/inputs/metaphlan-real/prep/$a/${a}_$m.fastq.gz" >/dev/null \
      || { echo "stage the reads first: bash measurements/metaphlan-real/stage-inputs.sh <bucket>" >&2; exit 1; }
  done
done
(cd "$HERE" && shasum -a 256 -c pins.sha256 --ignore-missing)
for f in trim.sh check.py pins.sha256; do
  aws s3 cp --only-show-errors --region "$REGION" "$HERE/$f" "$BUCKET/inputs/cutadapt-real/$f"
done
echo "staged under $BUCKET/inputs/cutadapt-real/"
