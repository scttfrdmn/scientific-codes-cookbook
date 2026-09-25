#!/usr/bin/env bash
# Stage this recipe's two inputs: the Ensembl 116 human transcriptome (verbatim)
# and a 200,000-read-pair slice of a Geuvadis RNA-seq run.
#
# Why copies exist: a TaskSpec input manifest stages whole S3 objects, so anything
# not already in S3 has to be materialised into our own bucket first, and anything
# large has to be sliced before it gets there. The transcriptome is copied byte for
# byte (no derivation, so its sha256 is the sha256 of the Ensembl release object);
# the reads are a fixed-offset slice, so re-running this reproduces them exactly.
set -euo pipefail

BUCKET="${1:?pass your bucket -- make stage RECIPE=NAME does this}"
PREFIX="inputs/salmon"
ENS="https://ftp.ensembl.org/pub/release-116/fasta/homo_sapiens/cdna/Homo_sapiens.GRCh38.cdna.all.fa.gz"
ENA="https://ftp.sra.ebi.ac.uk/vol1/fastq/ERR188/ERR188026/ERR188026"
READS=200000                      # read pairs
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

# Transcriptome: whole object, unmodified. Ensembl release paths are immutable
# (release-116 will never change), which is why this is pinnable at all —
# ftp.ensembl.org/pub/current_* is not, and would not qualify.
curl -sSf -o cdna.fa.gz "$ENS"
[ "$(gzip -dc cdna.fa.gz | grep -c '^>')" -gt 100000 ] || { echo "transcriptome too small" >&2; exit 1; }
TX="$(gzip -dc cdna.fa.gz | grep -c '^>')"
echo "transcripts: $TX"

# Reads: first $READS pairs. 25 MiB of each gzip stream decompresses to well over
# 4*$READS lines; we cut at exactly that for whole records. `head` closing the
# pipe is expected, hence the tolerated gzip exit.
LINES=$((READS * 4))
for i in 1 2; do
  curl -sSf -r 0-26214399 -o "part_${i}.gz" "${ENA}_${i}.fastq.gz"
  { gzip -dc "part_${i}.gz" 2>/dev/null || true; } | head -n "$LINES" > "r${i}.fq"
  [ "$(wc -l < "r${i}.fq")" -eq "$LINES" ] || { echo "mate $i short" >&2; exit 1; }
  gzip -n -9 "r${i}.fq"
done

# Mates must be in the same order: same first and last read name.
for pos in first last; do
  case "$pos" in
    first) a=$({ gzip -dc r1.fq.gz || true; } | head -n 1); b=$({ gzip -dc r2.fq.gz || true; } | head -n 1) ;;
    last)  a=$(gzip -dc r1.fq.gz | tail -n 4 | { head -n 1 || true; }); b=$(gzip -dc r2.fq.gz | tail -n 4 | { head -n 1 || true; }) ;;
  esac
  [ "${a%%/*}" = "${b%%/*}" ] || { echo "mate order differs at $pos read" >&2; exit 1; }
done

aws s3 cp cdna.fa.gz "s3://$BUCKET/$PREFIX/ensembl116_cdna.fa.gz" --only-show-errors
aws s3 cp r1.fq.gz   "s3://$BUCKET/$PREFIX/ERR188026_sub_1.fq.gz" --only-show-errors
aws s3 cp r2.fq.gz   "s3://$BUCKET/$PREFIX/ERR188026_sub_2.fq.gz" --only-show-errors
echo "--- pins (record these in README.md):"
sha256sum cdna.fa.gz r1.fq.gz r2.fq.gz
echo "--- transcript count: $TX"

# ---------------------------------------------------------------------------
# The REAL workload: the COMPLETE ERR188026 run (not a 200k-pair slice) plus the
# full Ensembl 116 cDNA set. ~2.2 GiB total, fetched once into your own bucket.
# ---------------------------------------------------------------------------
echo "== stage the full transcriptome + the complete ERR188026 run =="
E=https://ftp.ensembl.org/pub/release-116
curl -fsSL "$E/fasta/homo_sapiens/cdna/Homo_sapiens.GRCh38.cdna.all.fa.gz" -o cdna.fa.gz
aws s3 cp cdna.fa.gz "s3://$BUCKET/inputs/salmon-real/ensembl116_cdna.fa.gz" --only-show-errors
for r in 1 2; do
  curl -fsSL "https://ftp.sra.ebi.ac.uk/vol1/fastq/ERR188/ERR188026/ERR188026_${r}.fastq.gz" -o "ERR188026_${r}.fastq.gz"
  aws s3 cp "ERR188026_${r}.fastq.gz" "s3://$BUCKET/inputs/salmon-real/" --only-show-errors
done
echo "staged inputs/salmon-real/ -- transcriptome + both read files"
