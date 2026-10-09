#!/usr/bin/env bash
# Stage a real Hi-C pairs file and its chromsizes, both from cooler's own test data at the tag
# matching the cooler in the image.
#
# WHY THIS SOURCE. 4DN is not on the AWS Open Data Registry (probed: 404), and pairtools' own
# test data is 1.9 KB of mock reads -- the identities would hold but prove nothing at scale.
# cooler ships REAL Hi-C at v0.10.4: aag2.sample1.pairs is 9,998 Aedes aegypti contacts whose
# read IDs still carry their SRA runs (SRR5319284, SRR5319279). Pairs and chromsizes come from
# the SAME tag, so the pin is one self-consistent set rather than two sources to keep aligned.
#
# THE FILE IS SELF-DESCRIBING, AND THAT IS LOAD-BEARING. It declares its own column order,
# sortedness, shape and chromsizes:
#     ## pairs format v1.0.0
#     #columns: readID chrom1 pos1 chrom2 pos2 strand1 strand2 pair_type sam1 sam2
# so `cooler cload` gets c1=2 p1=3 c2=4 p2=5 from the file rather than from an assumption.
# `hg19.sample1.pairs` in the same directory is HEADERLESS with a different column order, so
# indices are NOT transferable between them -- loading one with the other's indices would
# silently build a matrix out of read IDs.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=hic does this}"
REGION="${AWS_REGION:-us-west-2}"
TAG=v0.10.4
RAW="https://raw.githubusercontent.com/open2c/cooler/$TAG/tests/data"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"

if aws s3 ls "$BUCKET/inputs/hic/aag2.sample1.pairs" --region "$REGION" >/dev/null 2>&1; then
  echo "Hi-C pairs already staged"; exit 0
fi

echo "== fetch the pairs and chromsizes from cooler $TAG =="
curl -fsSL -o aag2.sample1.pairs "$RAW/aag2.sample1.pairs"
curl -fsSL -o aag2.chrom.sizes  "$RAW/aag2.chrom.sizes"
printf '  %-22s %8s bytes\n' aag2.sample1.pairs "$(wc -c < aag2.sample1.pairs)"
printf '  %-22s %8s bytes\n' aag2.chrom.sizes  "$(wc -c < aag2.chrom.sizes)"

echo "== the file must declare the format the task relies on =="
grep -q '^## pairs format v1.0.0' aag2.sample1.pairs || { echo "  not pairs v1.0.0" >&2; exit 1; }
COLS=$(grep '^#columns:' aag2.sample1.pairs | head -1)
echo "  $COLS"
echo "$COLS" | grep -q 'readID chrom1 pos1 chrom2 pos2' || {
  echo "  unexpected column order -- cooler cload indices would be wrong" >&2; exit 1; }
grep -q '^#shape: upper triangle' aag2.sample1.pairs || { echo "  not upper-triangle" >&2; exit 1; }

echo "== every chromosome in the pairs must exist in the chromsizes =="
# cooler cload silently drops pairs on unknown chromosomes, which would break the conservation
# identity for a reason that has nothing to do with cooler. Checked here instead.
awk '!/^#/{print $2; print $4}' aag2.sample1.pairs | sort -u > pc
awk '{print $1}' aag2.chrom.sizes | sort -u > cc
NP=$(wc -l < pc | tr -d ' '); MISS=$(comm -23 pc cc | wc -l | tr -d ' ')
echo "  distinct chroms in pairs: $NP   missing from chromsizes: $MISS"
test "$MISS" -eq 0 || { echo "  $MISS chromosomes would be dropped by cload" >&2; exit 1; }

NREC=$(grep -vc '^#' aag2.sample1.pairs)
echo "  records: $NREC"
test "$NREC" -gt 5000 || { echo "  expected >5000 contacts" >&2; exit 1; }

shasum -a 256 aag2.sample1.pairs aag2.chrom.sizes > pins.sha256
printf 'input_records\t%s\nn_contigs\t%s\n' "$NREC" "$(wc -l < aag2.chrom.sizes | tr -d ' ')" > expected.tsv
cat pins.sha256 expected.tsv | sed 's/^/  /'

for f in aag2.sample1.pairs aag2.chrom.sizes pins.sha256 expected.tsv; do
  aws s3 cp "$f" "$BUCKET/inputs/hic/$f" --region "$REGION" --only-show-errors
done
echo "done."
echo "  $BUCKET/inputs/hic/  ($NREC real Hi-C contacts, $(wc -l < aag2.chrom.sizes | tr -d ' ') contigs)"
