#!/usr/bin/env bash
# Stage the clair3-real inputs. Reads, truth VCF and reference are already in the bucket:
#   reads   HG002 PacBio HiFi Revio 48x, chr20 (phasing-real's 00-prep slice, sha256-pinned there)
#   truth   GIAB NISTv4.2.1 GRCh38 benchmark, chr20 (same) + its confident-region BED (RODA, copied here)
#   strata  GIAB genome-stratifications v3.3 GRCh38, eight BEDs (RODA), md5-checked against GIAB's list
#   scorer  RTG.jar from bioconda's noarch rtg-tools-3.13-hdfd78af_0, sha256-checked against anaconda.org
#   scripts lib.sh call.sh eval.sh strat.sh
# Every task checks its staged inputs, scripts included, against pins.sha256 before it runs.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
P="$BUCKET/inputs/clair3-real"
GIAB=s3://giab/release
BENCH=AshkenazimTrio/HG002_NA24385_son/NISTv4.2.1/GRCh38/HG002_GRCh38_1_22_v4.2.1_benchmark_noinconsistent.bed
ST=genome-stratifications/v3.3/GRCh38@all
STRATA="easy=Union/GRCh38_notinalldifficultregions.bed.gz
alldifficult=Union/GRCh38_alldifficultregions.bed.gz
lowmapsegdup=Union/GRCh38_alllowmapandsegdupregions.bed.gz
notlowmapsegdup=Union/GRCh38_notinalllowmapandsegdupregions.bed.gz
trhomopolymer=LowComplexity/GRCh38_AllTandemRepeatsandHomopolymers_slop5.bed.gz
segdups=SegmentalDuplications/GRCh38_segdups.bed.gz
lowmap=Mappability/GRCh38_lowmappabilityall.bed.gz
complexsv=GenomeSpecific/GRCh38_HG002_v4.2.1_complexandSVs.bed.gz"
s3cp(){ aws s3 cp --only-show-errors --region "$REGION" "$@"; }
have(){ aws s3 ls --region "$REGION" "$1" > /dev/null 2>&1; }
sha_of(){ s3cp "$1" - | shasum -a 256 | awk '{print $1}'; }
md5h(){ if command -v md5sum > /dev/null; then md5sum | awk '{print $1}'; else md5 -q; fi; }
pin(){ awk -v f="$1" '$2==f{print $1}' "$HERE/pins.sha256"; }

for f in hifi.chr20.bam bench.chr20.vcf.gz; do
  have "$BUCKET/inputs/phasing-real/prep/$f" || { echo "missing $f -- stage measurements/phasing-real first" >&2; exit 1; }
done

# RODA -> bucket, server-side
have "$P/giab/${BENCH##*/}" || s3cp --source-region us-east-1 "$GIAB/$BENCH" "$P/giab/${BENCH##*/}"
[ "$(sha_of "$P/giab/${BENCH##*/}")" = "$(pin bench.bed)" ] || { echo "benchmark BED is not the pinned file" >&2; exit 1; }
MD5S=$(s3cp --region us-east-1 "$GIAB/$ST/GRCh38-genome-stratifications-md5s.txt" -)
echo "$STRATA" | while IFS== read -r name path; do
  have "$P/strat/$name.bed.gz" || s3cp --source-region us-east-1 "$GIAB/$ST/$path" "$P/strat/$name.bed.gz"
  want=$(echo "$MD5S" | awk -v p="$path" '$2==p{print $1}')
  got=$(s3cp "$P/strat/$name.bed.gz" - | md5h)
  [ "$got" = "$want" ] || { echo "$path md5 $got != GIAB's $want" >&2; exit 1; }
  [ "$(sha_of "$P/strat/$name.bed.gz")" = "$(pin "strat.$name.bed.gz")" ] || { echo "$name is not the pinned file" >&2; exit 1; }
done

# the scorer: RTG.jar out of bioconda's own noarch package, whose sha256 anaconda.org publishes
if ! have "$P/RTG.jar"; then
  T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
  curl -sSLo "$T/rtg.tar.bz2" https://conda.anaconda.org/bioconda/noarch/rtg-tools-3.13-hdfd78af_0.tar.bz2
  echo "3af3c5796843e431e0c647c9c3dbb8cd9c9c14b61adfdfafd2bc09796f22303d  $T/rtg.tar.bz2" | shasum -a 256 -c -
  tar xjf "$T/rtg.tar.bz2" -C "$T" share/rtg-tools-3.13-0/RTG.jar
  s3cp "$T/share/rtg-tools-3.13-0/RTG.jar" "$P/RTG.jar"
fi
[ "$(sha_of "$P/RTG.jar")" = "$(pin RTG.jar)" ] || { echo "RTG.jar is not the pinned file" >&2; exit 1; }

( cd "$HERE" && grep -E ' (lib|call|eval|strat)\.sh$' pins.sha256 | shasum -a 256 -c - )
for f in lib.sh call.sh eval.sh strat.sh pins.sha256; do s3cp "$HERE/$f" "$P/$f"; done
echo "staged $P/"
