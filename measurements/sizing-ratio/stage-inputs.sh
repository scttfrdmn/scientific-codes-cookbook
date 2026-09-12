#!/usr/bin/env bash
# Stage legible-scale inputs for the sizing-ratio measurement batch.
#   stage-inputs.sh <bucket> [ecoli|rest|all]   (default all)
#   ecoli = just the assembler reads (the megahit canary needs only these)
#   rest  = GRCh38 + STAR align reads + the chr1 BAM slice
# Runs locally (curl/aws) + one docker samtools step for the CRAM slice.
# Prints sha256 for provenance; measurement tasks do shape checks, not sha256 gates.
set -euo pipefail
BUCKET="${1:?usage: stage-inputs.sh <bucket> [ecoli|rest|all]}"
GROUP="${2:-all}"
P="inputs/sizing"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT; cd "$WORK"
sha(){ shasum -a 256 "$@" 2>/dev/null || sha256sum "$@"; }

stage_ecoli(){
  echo "== E. coli K-12 MG1655 Illumina PE (ERR022075) -> first 2.3M pairs (~100x) =="
  local EB="https://ftp.sra.ebi.ac.uk/vol1/fastq/ERR022/ERR022075"
  local i
  for i in 1 2; do
    curl -fsSL -r 0-499999999 "$EB/ERR022075_${i}.fastq.gz" -o "raw_${i}.gz"
    ( gzip -dc "raw_${i}.gz" 2>/dev/null || true ) | head -n 9200000 | gzip -n -6 > "ecoli_R${i}.fq.gz"
  done
  sha ecoli_R1.fq.gz ecoli_R2.fq.gz
  aws s3 cp ecoli_R1.fq.gz "s3://$BUCKET/$P/ecoli_R1.fq.gz" --only-show-errors
  aws s3 cp ecoli_R2.fq.gz "s3://$BUCKET/$P/ecoli_R2.fq.gz" --only-show-errors
}

stage_grch38(){
  echo "== GRCh38 primary assembly + GTF (Ensembl release-116, immutable) =="
  local E="https://ftp.ensembl.org/pub/release-116"
  curl -fsSL "$E/fasta/homo_sapiens/dna/Homo_sapiens.GRCh38.dna.primary_assembly.fa.gz" -o GRCh38.fa.gz
  curl -fsSL "$E/gtf/homo_sapiens/Homo_sapiens.GRCh38.116.gtf.gz" -o GRCh38.gtf.gz
  sha GRCh38.fa.gz GRCh38.gtf.gz
  aws s3 cp GRCh38.fa.gz  "s3://$BUCKET/$P/GRCh38.primary_assembly.fa.gz" --only-show-errors
  aws s3 cp GRCh38.gtf.gz "s3://$BUCKET/$P/GRCh38.116.gtf.gz" --only-show-errors
}

stage_reads(){
  echo "== human RNA-seq reads for STAR align (ERR188026, first ~2M pairs) =="
  local RB="https://ftp.sra.ebi.ac.uk/vol1/fastq/ERR188/ERR188026/ERR188026"
  local i
  for i in 1 2; do
    curl -fsSL -r 0-209715199 "${RB}_${i}.fastq.gz" -o "rr_${i}.gz"
    ( gzip -dc "rr_${i}.gz" 2>/dev/null || true ) | head -n 8000000 | gzip -n -6 > "r${i}.fq.gz"
  done
  sha r1.fq.gz r2.fq.gz
  aws s3 cp r1.fq.gz "s3://$BUCKET/$P/ERR188026_R1.fq.gz" --only-show-errors
  aws s3 cp r2.fq.gz "s3://$BUCKET/$P/ERR188026_R2.fq.gz" --only-show-errors
}

stage_chr1(){
  echo "== picard: chr1:1-100,000,000 30x slice of HG00096 (same CRAM as the fixture) =="
  local CRAM="https://1000genomes.s3.amazonaws.com/1000G_2504_high_coverage/data/ERR3240114/HG00096.final.cram"
  local SIMG="quay.io/aarchbio/samtools@sha256:1191739637fb6f46ef97c02b28f693b25ca3ca61f90e1337f349b7b7cc0be4f7"
  docker run --rm --platform linux/arm64 --user "$(id -u):$(id -g)" -v "$WORK:/d" -w /d \
    --entrypoint bash "$SIMG" -c "
      export REF_PATH='https://www.ebi.ac.uk/ena/cram/md5/%s' REF_CACHE=/d/rc
      samtools view -b -h '$CRAM' chr1:1-100000000 -o chr1.bam
      samtools index chr1.bam"
  sha chr1.bam
  aws s3 cp chr1.bam     "s3://$BUCKET/$P/HG00096.chr1_1-100Mb.30x.bam"     --only-show-errors
  aws s3 cp chr1.bam.bai "s3://$BUCKET/$P/HG00096.chr1_1-100Mb.30x.bam.bai" --only-show-errors
}

case "$GROUP" in
  ecoli) stage_ecoli;;
  rest)  stage_grch38; stage_reads; stage_chr1;;
  all)   stage_ecoli; stage_grch38; stage_reads; stage_chr1;;
  *) echo "unknown group '$GROUP' (want ecoli|rest|all)"; exit 1;;
esac
echo "staged group=$GROUP under s3://$BUCKET/$P/"
