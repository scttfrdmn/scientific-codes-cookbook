#!/usr/bin/env bash
# Stage a REAL Oxford Nanopore plasmid run plus the depositors' own reference sequence.
#
# WHY THIS REPLACES THE SYNTHETIC FIXTURE. The first version of this recipe polished a draft
# using 27x of error-FREE 1 kb reads, and said so in its own "Make it yours" table: *a
# consensus cannot beat its evidence, so exact recovery here is a property of perfect reads,
# not of racon*. That is an honest caveat and also a gap worth closing: a polisher's whole
# job is removing errors that real basecalling leaves behind, and error-free reads cannot
# exercise it.
#
# ONT's plasmid sequencing dataset is on the Registry of Open Data (tier 1: RODA first), and
# it carries the thing that makes this measurable -- **the depositors' own full reference for
# each sample**, so the truth is published rather than planted by us.
#
#   reads  plasmid_2025.04/basecalls/hac/FBC24981/barcode01/...barcode01.fastq
#          R10.4.1 / SQK-RBK114-96, hac basecalls. 112,741,893 bytes, mean read 4,420 bp.
#          Chemistry matters: medaka picks its model from the basecaller, and the image's
#          bundled r1041_e82_400bps_hac model is the match for exactly these reads.
#   truth  analysis/inputs/references/full_reference/sample_01.full_reference.fasta
#          6,361 bp, and sample_sheet.csv independently states approx_size 6361 for
#          sample_01 <-> barcode01. Two sources agreeing on the length is a free check that
#          the right reference is paired with the right barcode.
#
# ONT also publish their own pipeline's result for this sample -- a 6,361 bp assembly at mean
# quality Q54.79 (analysis/outputs/.../sample_01.assembly_stats.tsv) -- which is a published
# number to measure against rather than only an internal consistency check
# (practices/reference-from-tests.md).
#
# Reads longer than the plasmid are expected and are not an error: a circular 6.4 kb
# construct read by a long-read sequencer produces reads that wrap past the origin, so the
# observed 12,697 bp maximum is real.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=polish does this}"
SRC=s3://ont-open-data/plasmid_2025.04
FQ_KEY="plasmid_2025.04/basecalls/hac/FBC24981/barcode01/ce156b78d924b8568aa7640fdd6e276c55da8547_SQK-RBK114-96_barcode01.fastq"
READS=165        # ~120x of 6,361 bp -- see below

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

echo "== the depositors' reference: the truth this recipe is scored against =="
aws s3 cp "$SRC/analysis/inputs/references/full_reference/sample_01.full_reference.fasta" \
  "$tmp/truth.fa" --no-sign-request --only-show-errors
LEN=$(awk '!/^>/{n+=length($0)} END{print n}' "$tmp/truth.fa")
echo "  sample_01 reference: $LEN bp"

echo "== cross-check the length against the sample sheet, which is a separate file =="
aws s3 cp "$SRC/analysis/inputs/sample_sheet.csv" "$tmp/ss.csv" --no-sign-request --only-show-errors
SHEET=$(awk -F, '$1=="sample_01"{print $3}' "$tmp/ss.csv")
BC=$(awk -F, '$1=="sample_01"{print $2}' "$tmp/ss.csv")
if [ "$LEN" != "$SHEET" ] || [ "$BC" != "barcode01" ]; then
  echo "MISMATCH: reference is $LEN bp, sheet says $SHEET for $BC -- wrong reference/barcode pairing" >&2
  exit 1
fi
echo "  OK: sheet agrees -- sample_01 is $BC at $SHEET bp, so reference and reads are paired"

echo "== $READS reads from the head of the run (~120x) =="
# A RANGE GET, not the whole 112 MB object: 8 MB is far more than 165 reads need, and the
# subsample is the FIRST $READS records of it, which is deterministic.
#
# `aws s3api get-object --range`, NOT `aws s3 cp` (which has no --range), and NOT piped into
# `head`: head closing the pipe sends SIGPIPE upstream, which under `pipefail` fails the
# pipeline. This project has paid for that one more than once.
aws s3api get-object --bucket ont-open-data --key "$FQ_KEY" \
  --range "bytes=0-8000000" --no-sign-request "$tmp/head.fastq" >/dev/null
awk -v n="$READS" 'NR<=n*4' "$tmp/head.fastq" > "$tmp/reads.fastq"
# dorado writes `@id<TAB>st:Z:... RG:Z:...`. Keep only the id: the tab-delimited tags confuse
# tools that split on whitespace, and nothing downstream needs them.
awk 'NR%4==1{split($0,a,"\t"); print a[1]; next} {print}' "$tmp/reads.fastq" > "$tmp/reads.clean.fastq"
mv "$tmp/reads.clean.fastq" "$tmp/reads.fastq"
awk 'NR%4==2{n++; L+=length($0)} END{printf "  %d reads, %.2f Mb, %.0fx of 6361 bp\n", n, L/1e6, L/6361}' "$tmp/reads.fastq"

echo "== pin both staged objects by content =="
# The subsample is derived, so ITS hash is what the recipe depends on -- not the 112 MB
# source object's. Pinning the derived bytes is the thing a re-stage must reproduce.
cat > "$tmp/pins.sha256" <<'EOF'
84b52268ce9b404f699159ba5de2a02ddb94f9854d6ab0bf3969afea353ef607  truth.fa
8eeaa55ec09e952fd1497158784f31a37bf150faee554e86164f1a58ee5a4db8  reads.fastq
EOF
( cd "$tmp" && shasum -a 256 reads.fastq > reads.sha256 )
( cd "$tmp" && shasum -a 256 -c pins.sha256 ) || {
  echo "staged bytes do not match their pins -- upstream changed, or the subsample drifted" >&2
  exit 1
}
echo "  OK: reference and the 165-read subsample both match their pinned sha256"
( cd "$tmp" && shasum -a 256 reads.fastq | tee reads.sha256 )
echo "  reads.fastq hash recorded above; the task re-checks it before polishing"

echo "== upload =="
for f in truth.fa reads.fastq reads.sha256; do
  aws s3 cp "$tmp/$f" "$BUCKET/inputs/polish/$f" --only-show-errors
  echo "  -> $BUCKET/inputs/polish/$f"
done
echo "done."
