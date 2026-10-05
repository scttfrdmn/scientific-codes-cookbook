#!/usr/bin/env bash
# Stage four RefSeq bacterial genomes as SEPARATE objects, for a wildcard fan-out.
#
# WHY SEPARATE OBJECTS. Snakemake's unit of parallelism is a wildcard, so the job count is
# discovered from the data rather than written into the workflow. That is the whole point of
# this recipe as distinct from nf-spawn's fixed DAG, and it needs one object per sample --
# a tar would collapse four jobs into one.
#
# DERIVED, FROM AN ALREADY-PINNED SOURCE. These come out of `inputs/genomes20/genomes20.tar`,
# which recipes/mash already stages and pins (sha256 6fa8884c...). Nothing new is fetched
# from the internet here; this re-cuts bytes the catalog already trusts.
#
# THE TRUTH TRAVELS IN THE TAR. labels.tsv carries NCBI's own recorded length for each
# assembly, so the per-sample check is against a published number rather than an internal
# consistency claim -- the same sourcing move as SIESTA's committed reference
# (practices/reference-from-tests.md). Four independent checks, and their sum is the
# conservation identity the fan-out has to satisfy.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=snakemake does this}"
SRC="$BUCKET/inputs/genomes20/genomes20.tar"
TAR_SHA=6fa8884c45af0178a24c28413ccfdf7018915e843f106667787f749b4a7aaadc
SAMPLES="GCF_000005845.2 GCF_000006945.2 GCF_000009045.1 GCF_000195955.2"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

echo "== the source tar must be the bytes mash pinned =="
if ! aws s3 ls "$SRC" >/dev/null 2>&1; then
  echo "genomes20.tar is not staged. Run: make stage RECIPE=mash" >&2
  exit 1
fi
aws s3 cp "$SRC" "$tmp/g20.tar" --only-show-errors
echo "$TAR_SHA  $tmp/g20.tar" | shasum -a 256 -c - >/dev/null || {
  echo "genomes20.tar does not match the pin recorded by recipes/mash" >&2; exit 1; }
echo "  OK: source tar matches $TAR_SHA"

tar -xf "$tmp/g20.tar" -C "$tmp"
test -f "$tmp/labels.tsv" || { echo "labels.tsv missing from the tar" >&2; exit 1; }

echo "== four genomes, each with NCBI's recorded length from labels.tsv =="
: > "$tmp/expected.tsv"
printf 'sample\torganism\tncbi_length\n' >> "$tmp/expected.tsv"
TOTAL=0
for s in $SAMPLES; do
  # the tar stores entries as ./NAME
  f="$tmp/./$s.fna.gz"; [ -f "$f" ] || f="$tmp/$s.fna.gz"
  test -f "$f" || { echo "$s not in the tar" >&2; exit 1; }
  ORG=$(awk -F'\t' -v s="$s" '$1==s{print $2}' "$tmp/labels.tsv")
  LEN=$(awk -F'\t' -v s="$s" '$1==s{print $4}' "$tmp/labels.tsv")
  test -n "$LEN" || { echo "no recorded length for $s in labels.tsv" >&2; exit 1; }
  printf '%s\t%s\t%s\n' "$s" "$ORG" "$LEN" >> "$tmp/expected.tsv"
  TOTAL=$(( TOTAL + LEN ))
  printf '  %-18s %-30s %s bp\n' "$s" "$ORG" "$LEN"
  cp "$f" "$tmp/$s.fna.gz" 2>/dev/null || true
done
printf 'TOTAL\t-\t%s\n' "$TOTAL" >> "$tmp/expected.tsv"
echo "  expected total: $TOTAL bp -- this is the identity the fan-out must reproduce"

echo "== upload one object per sample, plus the expected table =="
for s in $SAMPLES; do
  aws s3 cp "$tmp/$s.fna.gz" "$BUCKET/inputs/snakemake/genomes/$s.fna.gz" --only-show-errors
  echo "  -> genomes/$s.fna.gz"
done
aws s3 cp "$tmp/expected.tsv" "$BUCKET/inputs/snakemake/expected.tsv" --only-show-errors
echo "  -> expected.tsv"
echo "done."
