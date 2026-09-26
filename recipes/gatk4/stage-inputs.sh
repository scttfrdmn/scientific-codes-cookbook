#!/usr/bin/env bash
# Stage what GATK4 needs that is small enough to build here: the reference index
# (chr20.fa.fai + chr20.dict) and the GIAB HG001 truth set restricted to chr20.
#
# The BAM is NOT built here. It comes out of the published NA12878 30x CRAM and that is a
# ~1 GB same-cloud job, so it runs as `00-prep-bam.task.json` on the box instead of over
# a home uplink. Run that once, then this, then 01/02.
#
# What this deliberately does NOT do: copy chr20.fa. The reference itself is staged by
# bwa-samtools and every caller reads THOSE bytes — a cross-code comparison is only
# meaningful on an identical reference, and a second copy is a second thing to keep true
# (CLAUDE.md). chr20.fa is re-derived here only transiently, to index it, and discarded.
set -euo pipefail

BUCKET="${1:?pass your bucket -- make stage RECIPE=NAME does this}"
PREFIX="inputs/gatk4"
SIMG="quay.io/aarchbio/samtools@sha256:1191739637fb6f46ef97c02b28f693b25ca3ca61f90e1337f349b7b7cc0be4f7"
GIAB="https://s3.amazonaws.com/giab/release/NA12878_HG001/NISTv4.2.1/GRCh38"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

# ---------- 1. the reference index ----------
# chr20 of GRCh38_full_analysis_set_plus_decoy_hla — the same byte range bwa-samtools stages,
# and (verified) the same M5 b18e6c531b0bd70e949a7fc20859cb01 that the NA12878 CRAM was
# compressed against, which is what lets a chr20-only reference decode that CRAM at all.
printf '>chr20\n' > chr20.fa
aws s3api get-object --bucket 1000genomes \
  --key "technical/reference/GRCh38_reference_genome/GRCh38_full_analysis_set_plus_decoy_hla.fa" \
  --range bytes=2751788762-2817153559 chr20.seq > /dev/null
cat chr20.seq >> chr20.fa
[ "$(tail -n +2 chr20.fa | tr -d '\n' | wc -c)" -eq 64444167 ] || { echo "chr20.fa wrong length" >&2; exit 1; }

docker run --rm --platform linux/arm64 --user "$(id -u):$(id -g)" -v "$WORK:/d" -w /d \
  --entrypoint bash "$SIMG" -c 'export PATH=/opt/conda/bin:$PATH
    samtools faidx chr20.fa && samtools dict -u chr20.fa chr20.fa -o chr20.dict'

# Assert the pinned index bytes (deterministic; guards a samtools change).
cat > idx.sha256 <<'SHA'
295950bb320e5f27b37360000d77303187e6399bf8e7705aa26fd4a1c88ba115  chr20.fa.fai
b9e597b77989d8861aae55670fff9ef6cf554d6c1ea257f2207674855e25977c  chr20.dict
SHA
sha256sum -c idx.sha256 2>/dev/null || shasum -a 256 -c idx.sha256

# ---------- 2. the GIAB HG001 v4.2.1 truth set, chr20 ----------
# This is the whole reason the recipe can claim accuracy rather than plausibility: NIST ships
# NA12878's benchmark variants AND the BED of regions where that benchmark is confident. The
# chr20 slice is a region query on the published release, so it is derived-but-deterministic;
# what is pinned is the upstream release (v4.2.1) plus the record counts asserted below.
docker run --rm --platform linux/arm64 --user "$(id -u):$(id -g)" -v "$WORK:/d" -w /d \
  --entrypoint bash "$SIMG" -c "export PATH=/opt/conda/bin:\$PATH
    tabix -h '$GIAB/HG001_GRCh38_1_22_v4.2.1_benchmark.vcf.gz' chr20 | bgzip > truth.chr20.vcf.gz
    tabix -p vcf truth.chr20.vcf.gz"

curl -fsSL "$GIAB/HG001_GRCh38_1_22_v4.2.1_benchmark.bed" -o bench.bed
awk '$1=="chr20"' bench.bed > truth.chr20.bed

# Content assertions, not byte assertions: the slice is recompressed locally, so its bytes
# depend on the bgzip build while its CONTENT does not. Assert what the science depends on.
NREC=$(gzip -dc truth.chr20.vcf.gz | grep -vc '^#')
NSNV=$(gzip -dc truth.chr20.vcf.gz | awk '!/^#/ && length($4)==1 && length($5)==1' | wc -l | tr -d ' ')
SM=$(gzip -dc truth.chr20.vcf.gz | awk '/^#CHROM/{print $10; exit}')
NBED=$(wc -l < truth.chr20.bed | tr -d ' ')
BEDBP=$(awk '{s+=$3-$2} END{print s}' truth.chr20.bed)
echo "truth records $NREC  snvs $NSNV  sample $SM  bed_intervals $NBED  bed_bases $BEDBP"
[ "$NREC"  -eq 82818 ]    || { echo "truth record count changed: $NREC != 82818" >&2; exit 1; }
[ "$NSNV"  -eq 71316 ]    || { echo "truth SNV count changed: $NSNV != 71316" >&2; exit 1; }
[ "$SM"    = "HG001" ]    || { echo "truth sample is $SM, not HG001" >&2; exit 1; }
[ "$NBED"  -eq 13529 ]    || { echo "bed interval count changed: $NBED != 13529" >&2; exit 1; }
[ "$BEDBP" -eq 56000154 ] || { echo "bed base count changed: $BEDBP != 56000154" >&2; exit 1; }

for f in chr20.fa.fai chr20.dict truth.chr20.vcf.gz truth.chr20.vcf.gz.tbi truth.chr20.bed; do
  aws s3 cp "$f" "s3://$BUCKET/$PREFIX/$f" --only-show-errors
done
echo "staged s3://$BUCKET/$PREFIX/{chr20.fa.fai,chr20.dict,truth.chr20.vcf.gz,truth.chr20.vcf.gz.tbi,truth.chr20.bed}"
echo "next: spawn task run --spec 00-prep-bam.task.json   (builds NA12878.chr20.30x.bam on the box)"
