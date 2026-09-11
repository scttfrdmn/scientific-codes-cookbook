#!/usr/bin/env bash
# Stage this recipe's three inputs: Ensembl 116 chromosome 20 (verbatim), the
# chr20 records of the Ensembl 116 annotation, and a 200,000-read-pair slice of a
# Geuvadis RNA-seq run.
#
# chr20 rather than the whole genome for a hard reason, not convenience: the spawn
# task path gets an 8 GiB root disk (~6.1 GiB usable), and a full human STAR index
# is ~30 GiB. A chr20 index is ~2 GiB and fits with room for the reads and output.
# See README.md.
#
# Ensembl release-116/ paths are immutable, which is what makes them pinnable;
# current_*/ is not and would not qualify.
set -euo pipefail

BUCKET="${1:?pass your bucket -- make stage RECIPE=NAME does this}"
PREFIX="inputs/star"
E="https://ftp.ensembl.org/pub/release-116"
FA="$E/fasta/homo_sapiens/dna/Homo_sapiens.GRCh38.dna.chromosome.20.fa.gz"
GTF="$E/gtf/homo_sapiens/Homo_sapiens.GRCh38.116.gtf.gz"
ENA="https://ftp.sra.ebi.ac.uk/vol1/fastq/ERR188/ERR188026/ERR188026"
READS=200000
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

# Reference: whole per-chromosome object, unmodified.
curl -sSf -o chr20.fa.gz "$FA"
gzip -dc chr20.fa.gz > chr20.fa
[ "$(grep -c '^>' chr20.fa)" -eq 1 ] || { echo "expected exactly one sequence" >&2; exit 1; }
# Ensembl names this chromosome "20", not "chr20"; the GTF must agree with it.
head -n 1 chr20.fa | grep -q '^>20 ' || { echo "unexpected fasta header" >&2; exit 1; }
BASES=$(tail -n +2 chr20.fa | tr -d '\n' | wc -c | tr -d ' ')
echo "chr20 bases: $BASES"

# Annotation: chr20 records only. The whole GTF is 141 MB compressed and STAR
# warns (correctly) about annotation on absent chromosomes, so subset it. Records
# are TAB-separated with the chromosome in field 1; keep the header comments out.
curl -sSf -o gtf.gz "$GTF"
{ gzip -dc gtf.gz || true; } | awk -F'\t' '$1=="20"' > chr20.gtf
GENES=$(awk -F'\t' '$3=="gene"' chr20.gtf | wc -l | tr -d ' ')
EXONS=$(awk -F'\t' '$3=="exon"' chr20.gtf | wc -l | tr -d ' ')
[ "$GENES" -gt 300 ] || { echo "only $GENES genes on chr20, expected >300" >&2; exit 1; }
[ "$EXONS" -gt 5000 ] || { echo "only $EXONS exons, expected >5000" >&2; exit 1; }
# Every kept record must really be chr20, and no coordinate may exceed its length.
[ "$(awk -F'\t' '$1!="20"' chr20.gtf | wc -l | tr -d ' ')" -eq 0 ] || { echo "non-chr20 record kept" >&2; exit 1; }
[ "$(awk -F'\t' -v n="$BASES" '$5>n' chr20.gtf | wc -l | tr -d ' ')" -eq 0 ] \
  || { echo "annotation past end of chromosome" >&2; exit 1; }

# Reads: first $READS pairs, same fixed offsets as the salmon recipe uses.
LINES=$((READS * 4))
for i in 1 2; do
  curl -sSf -r 0-26214399 -o "part_${i}.gz" "${ENA}_${i}.fastq.gz"
  { gzip -dc "part_${i}.gz" 2>/dev/null || true; } | head -n "$LINES" > "r${i}.fq"
  [ "$(wc -l < "r${i}.fq")" -eq "$LINES" ] || { echo "mate $i short" >&2; exit 1; }
  gzip -n -9 "r${i}.fq"
done
a=$({ gzip -dc r1.fq.gz || true; } | head -n 1); b=$({ gzip -dc r2.fq.gz || true; } | head -n 1)
[ "${a%%/*}" = "${b%%/*}" ] || { echo "mates are not in the same order" >&2; exit 1; }

gzip -n -9 chr20.gtf
aws s3 cp chr20.fa.gz   "s3://$BUCKET/$PREFIX/ensembl116_chr20.fa.gz" --only-show-errors
aws s3 cp chr20.gtf.gz  "s3://$BUCKET/$PREFIX/ensembl116_chr20.gtf.gz" --only-show-errors
aws s3 cp r1.fq.gz      "s3://$BUCKET/$PREFIX/ERR188026_sub_1.fq.gz" --only-show-errors
aws s3 cp r2.fq.gz      "s3://$BUCKET/$PREFIX/ERR188026_sub_2.fq.gz" --only-show-errors
echo "--- pins (record these in README.md):"
sha256sum chr20.fa.gz chr20.gtf.gz r1.fq.gz r2.fq.gz
echo "--- chr20 bases: $BASES   genes: $GENES   exons: $EXONS   read pairs: $READS"
