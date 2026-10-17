#!/usr/bin/env bash
# Stage the single-cell real workload into your bucket, then check every object against pins.sha256.
#
#   reads       Tabula Muris Senis droplet channel 10X_P5_13 (1-month male lung, mouse 1-M-62),
#               10x Chromium 3' v2, both lanes, R1 + R2 -- RODA s3://czb-tabula-muris-senis,
#               copied server-side (same region) so the bytes cannot change under the pin.
#   reference   Ensembl release 116 mouse cDNA (GRCm39), whole file, unmodified.
#   permit list 10x's own v2 barcode list, 737K-august-2016.txt, from 10XGenomics/supernova at a
#               pinned commit (Cell Ranger itself is licence-restricted; the list is the same file).
#   published   the Tabula Muris Senis cell metadata (cell barcodes + n_counts), RODA.
# The task scripts (map.sh, quant.sh) go up too; the specs check their sha256 before running them.
set -euo pipefail
BUCKET="${1:?usage: stage-inputs.sh <bucket>}"
DST="s3://$BUCKET/inputs/single-cell-real"
HERE="$(cd "$(dirname "$0")" && pwd)"
RODA=s3://czb-tabula-muris-senis
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

for l in L001 L002; do for r in R1 R2; do
  f=10X_P5_13_S14_${l}_${r}_001.fastq.gz
  aws s3 cp "$RODA/10x/FASTQs/10X_P5_13/$f" "$DST/$f" --only-show-errors
done; done
aws s3 cp "$RODA/Metadata/tabula-muris-senis-full-metadata.csv" "$DST/" --only-show-errors

curl -fsSL -o "$WORK/Mus_musculus.GRCm39.cdna.all.fa.gz" \
  https://ftp.ensembl.org/pub/release-116/fasta/mus_musculus/cdna/Mus_musculus.GRCm39.cdna.all.fa.gz
curl -fsSL -o "$WORK/737K-august-2016.txt" \
  https://raw.githubusercontent.com/10XGenomics/supernova/b82c3d8efa68bda2d95f30621cd6d91308ce11a2/tenkit/lib/python/tenkit/barcodes/737K-august-2016.txt
( cd "$WORK" && grep -hE "Mus_musculus|737K" "$HERE/pins-map.sha256" "$HERE/pins-quant.sha256" | shasum -a 256 -c - )
aws s3 cp "$WORK/Mus_musculus.GRCm39.cdna.all.fa.gz" "$DST/" --only-show-errors
aws s3 cp "$WORK/737K-august-2016.txt" "$DST/" --only-show-errors

for f in map.sh quant.sh pins-map.sha256 pins-quant.sha256; do aws s3 cp "$HERE/$f" "$DST/$f" --only-show-errors; done
echo "staged $DST -- each task re-checks its staged inputs against pins-{map,quant}.sha256 before running"
