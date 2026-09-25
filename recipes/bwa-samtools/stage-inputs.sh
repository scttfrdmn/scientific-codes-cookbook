#!/usr/bin/env bash
# Build this recipe's three inputs from pinned byte ranges on RODA objects.
#
# Why derived copies exist: a TaskSpec input manifest stages WHOLE S3 objects
# (`aws s3 cp` in the generated wrapper), so there is no way to ask for a slice
# of a 3.26 GB reference or a 1.9 GB fastq. The subsample has to be materialised
# first. Every byte here is determined by a fixed offset+length on a RODA object,
# so re-running this reproduces the same three files bit-for-bit — verify with
# the sha256 sums in README.md, which the align task also re-checks on the box.
set -euo pipefail

BUCKET="${1:?pass your bucket -- make stage RECIPE=NAME does this}"
PREFIX="inputs/bwa-samtools"
SRC_REF="technical/reference/GRCh38_reference_genome/GRCh38_full_analysis_set_plus_decoy_hla.fa"
SRC_FQ="phase3/data/HG00096/sequence_read/SRR062634"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

# chr20 only. Offsets come from the reference's own .fai: chr20 starts at byte
# 2751788762 and is 64444167 bases at 70 bases / 71 bytes per line, so the
# sequence occupies 65364798 bytes ending at 2817153559. We supply our own
# ">chr20" header rather than range-guessing the (variable-length) original one.
printf '>chr20\n' > chr20.fa
aws s3api get-object --bucket 1000genomes --key "$SRC_REF" \
  --range bytes=2751788762-2817153559 chr20.seq > /dev/null
cat chr20.seq >> chr20.fa

# 400,000 read pairs. The first 40 MiB of each mate's gzip stream decompresses to
# well over 1,600,000 lines; we cut at exactly that to get 400,000 whole records.
# `head` closing the pipe is expected, hence the tolerated gzip exit.
for i in 1 2; do
  aws s3api get-object --bucket 1000genomes --key "${SRC_FQ}_${i}.filt.fastq.gz" \
    --range bytes=0-41943039 "part_${i}.gz" > /dev/null
  { gzip -dc "part_${i}.gz" 2>/dev/null || true; } | head -n 1600000 > "r${i}.fq"
  [ "$(wc -l < "r${i}.fq")" -eq 1600000 ] || { echo "mate $i short" >&2; exit 1; }
  gzip -n -9 "r${i}.fq"
done

# Sanity: the extracted chromosome must be exactly the length the .fai declares,
# and contain nothing but ACGTN.
[ "$(tail -n +2 chr20.fa | tr -d '\n' | wc -c)" -eq 64444167 ]
[ "$(tail -n +2 chr20.fa | tr -d '\nACGTNacgtn' | wc -c)" -eq 0 ]

aws s3 cp chr20.fa "s3://$BUCKET/$PREFIX/chr20.fa"
aws s3 cp r1.fq.gz "s3://$BUCKET/$PREFIX/HG00096_chr20smoke_1.fq.gz"
aws s3 cp r2.fq.gz "s3://$BUCKET/$PREFIX/HG00096_chr20smoke_2.fq.gz"
sha256sum chr20.fa r1.fq.gz r2.fq.gz

# ---------------------------------------------------------------------------
# The REAL workload's inputs: the *published* GRCh38 bwa index and one complete
# sequencing run, cached from the 1000genomes RODA bucket into your own bucket so
# every run stages same-region. Byte-identical to upstream -- a cache, not a derived
# copy. Signed reads work against this public bucket (no requester-pays).
#
# bwa mem reads <prefix>.amb/.ann/.bwt/.pac/.sa only, so the 3.0 GiB .fa is skipped:
# staging is 8.9 GiB instead of 12.
# ---------------------------------------------------------------------------
echo "== cache the published GRCh38 bwa index + SRR062634 (8.9 GiB, one time) =="
RODA_REF="technical/reference/GRCh38_reference_genome/GRCh38_full_analysis_set_plus_decoy_hla.fa"
RODA_FQ="phase3/data/HG00096/sequence_read"
for e in amb ann bwt pac sa fai; do
  aws s3 cp "s3://1000genomes/$RODA_REF.$e" \
            "s3://$BUCKET/inputs/bwa-real/$(basename "$RODA_REF").$e" --only-show-errors
done
for r in 1 2; do
  aws s3 cp "s3://1000genomes/$RODA_FQ/SRR062634_${r}.filt.fastq.gz" \
            "s3://$BUCKET/inputs/bwa-real/SRR062634_${r}.filt.fastq.gz" --only-show-errors
done
echo "staged inputs/bwa-real/ -- 6 index files + 2 fastq"
