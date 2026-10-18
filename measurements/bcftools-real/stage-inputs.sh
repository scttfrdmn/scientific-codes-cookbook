#!/usr/bin/env bash
# Stage 1000 Genomes Phase 3 chr20-22 (all 2,504 samples), the panel, a chr20-22 reference and
# the workload script for measurements/bcftools-real.
#
# Tiers: the VCFs and the panel are RODA (s3://1000genomes/release/20130502, the consortium's own
# release directory); they are copied server-side into the bucket so staging is in-region. The
# reference is BUILT: chromosomes 20, 21 and 22 cut verbatim from hs37d5.fa.gz (RODA,
# technical/reference/phase2_reference_assembly_sequence -- the assembly Phase 3 was called on).
# The cut is deterministic, so its sha256 is a real pin. Every staged byte is re-checked in-task.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PREFIX="$BUCKET/inputs/bcftools-real"
RODA=s3://1000genomes/release/20130502

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"

# The VCFs are NOT copied here: measurements/plink-real already holds the same RODA objects under
# inputs/plink-real/, and the spec reads them there (one copy of the bytes, verified by sha256 in
# the task). Stage them only if that prefix is absent.
for C in 20 21 22; do
  f=ALL.chr$C.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz
  aws s3 ls "$BUCKET/inputs/plink-real/$f" > /dev/null 2>&1 || \
    aws s3 cp --only-show-errors --source-region us-east-1 --region "$REGION" "$RODA/$f" "$BUCKET/inputs/plink-real/$f"
done
aws s3 cp --only-show-errors --no-sign-request "$RODA/integrated_call_samples_v3.20130502.ALL.panel" .
aws s3 cp --only-show-errors --no-sign-request \
  s3://1000genomes/technical/reference/phase2_reference_assembly_sequence/hs37d5.fa.gz .
# hs37d5.fa.gz is RAZF, so gzip warns "trailing garbage" after the last sequence; the text is complete.
{ gzip -dc hs37d5.fa.gz 2>/dev/null || true; } \
  | awk '/^>/{p=($1==">20"||$1==">21"||$1==">22")} p' > hs37d5.chr20-22.fa
cp "$SCRIPT_DIR/run.sh" .

cat > pins.sha256 <<'PINS'
2555eae3e0dda43c50b70741883d6cd28346cc8286c23e0fa37fb3788c5e6e37  hs37d5.fa.gz
13cf156fb860a2c327648854d913a4600dc657c271df185c81e397c0aa7808e3  hs37d5.chr20-22.fa
b4023dc6ee2d62ee89c8d4d347db4d348e65518d66d346574cdae7a4bbd76858  integrated_call_samples_v3.20130502.ALL.panel
PINS
shasum -a 256 -c pins.sha256
# hs37d5.fa.gz is the source of the cut, not a task input
grep -v ' hs37d5.fa.gz$' pins.sha256 > p && mv p pins.sha256
cat >> pins.sha256 <<'PINS'
4a0b61d6d7ed46b029d4f07164c554c6b8124e40c4eb9807a45c624eb714afa8  ALL.chr20.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz
086fc3a2e13cdf256f217509477669f7ec1f609c21cdb6f58c17588bf99acc39  ALL.chr21.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz
5d52145d6e3be132dc19ff525af2ae2e792511e60c49c148cb8eee669f114c2c  ALL.chr22.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz
PINS
shasum -a 256 run.sh >> pins.sha256

for f in hs37d5.chr20-22.fa integrated_call_samples_v3.20130502.ALL.panel run.sh pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$f" "$PREFIX/$f"
done
cat pins.sha256
echo "staged to $PREFIX/"
