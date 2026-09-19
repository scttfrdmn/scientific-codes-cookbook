#!/usr/bin/env bash
# Stage GATK4's reference index — chr20.fa.fai + chr20.dict — and nothing else.
#
# Why this exists: GATK HaplotypeCaller requires a .fai index and a .dict sequence
# dictionary alongside the reference, but the aarch.bio gatk4 image ships only gatk
# (no samtools), so the index can't be built in the call task. We build it here with
# samtools (run in docker locally — the same pattern recipes/bcftools uses to build
# its fixture), once, and stage the two small artifacts.
#
# What this deliberately does NOT do: copy chr20.fa. The reference itself is staged
# by bwa-samtools and every caller reads THOSE bytes — a cross-code comparison is only
# meaningful on an identical reference, and a second copy is a second thing to keep
# true (CLAUDE.md). So chr20.fa is re-derived here only transiently, to index it, and
# discarded; the call task reads the real reference that bwa-samtools staged.
#
# Deterministic + pinnable: the .fai is a pure function of the fasta; the .dict is
# pinned only after `-u chr20.fa` fixes its URI field (samtools otherwise embeds the
# absolute build path, which would differ per machine). Both sha256s are asserted.
set -euo pipefail

BUCKET="${1:?pass your bucket -- make stage RECIPE=NAME does this}"
PREFIX="inputs/gatk4"
SIMG="quay.io/aarchbio/samtools@sha256:1191739637fb6f46ef97c02b28f693b25ca3ca61f90e1337f349b7b7cc0be4f7"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

# chr20 of GRCh38_full_analysis_set_plus_decoy_hla — the same byte range bwa-samtools
# stages, so the index matches the reference the callers actually use. Transient here.
printf '>chr20\n' > chr20.fa
aws s3api get-object --bucket 1000genomes \
  --key "technical/reference/GRCh38_reference_genome/GRCh38_full_analysis_set_plus_decoy_hla.fa" \
  --range bytes=2751788762-2817153559 chr20.seq > /dev/null
cat chr20.seq >> chr20.fa
[ "$(tail -n +2 chr20.fa | tr -d '\n' | wc -c)" -eq 64444167 ] || { echo "chr20.fa wrong length" >&2; exit 1; }

docker run --rm --platform linux/arm64 --user "$(id -u):$(id -g)" -v "$WORK:/d" -w /d \
  --entrypoint bash "$SIMG" -c 'samtools faidx chr20.fa && samtools dict -u chr20.fa chr20.fa -o chr20.dict'

# Assert the pinned index bytes (deterministic; guards a samtools change).
cat > idx.sha256 <<'SHA'
295950bb320e5f27b37360000d77303187e6399bf8e7705aa26fd4a1c88ba115  chr20.fa.fai
b9e597b77989d8861aae55670fff9ef6cf554d6c1ea257f2207674855e25977c  chr20.dict
SHA
sha256sum -c idx.sha256

aws s3 cp chr20.fa.fai "s3://$BUCKET/$PREFIX/chr20.fa.fai" --only-show-errors
aws s3 cp chr20.dict   "s3://$BUCKET/$PREFIX/chr20.dict"   --only-show-errors
echo "staged s3://$BUCKET/$PREFIX/{chr20.fa.fai,chr20.dict}"
