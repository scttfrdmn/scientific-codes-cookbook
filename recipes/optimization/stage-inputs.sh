#!/usr/bin/env bash
# Stage one Netlib LP instance: afiro, the smallest of the classic test set.
#
# WHY NOT netlib.org DIRECTLY. `https://netlib.org/lp/data/afiro` is 794 bytes and is NOT MPS --
# it is netlib's compressed `emps` format, with no ROWS/COLUMNS/RHS/ENDATA sections. Using it
# would mean compiling emps.c, which is build-tier machinery for a recipe that should be a
# one-liner. (Measured: fetched it and inspected; it is binary.)
#
# WHERE THE REFERENCE VALUE COMES FROM. The published optimal objective is netlib's own, from
# https://netlib.org/lp/data/readme:
#     AFIRO    28  32  88  794  -4.6475314286E+02
# so the number this recipe asserts is the depositors' 1985-vintage published optimum, not
# anything a solver here produced. The MPS *bytes* come from HiGHS's test corpus at an
# immutable tag, which is a sourcing choice; the reference is netlib's.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=optimization does this}"
REGION="${AWS_REGION:-us-west-2}"
SRC="https://raw.githubusercontent.com/ERGO-Code/HiGHS/v1.11.0/check/instances/afiro.mps"
PIN=9cd304f02717cbd6f85068cb777b69d28539b22a4868ae0f0fb425f514f0eea5

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

if aws s3 ls "$BUCKET/inputs/optimization/afiro.mps" --region "$REGION" >/dev/null 2>&1; then
  echo "afiro.mps already staged"; exit 0
fi

echo "== fetch afiro.mps from an immutable git tag (3 KB) =="
curl -fsSL --retry 3 -o "$tmp/afiro.mps" "$SRC"
echo "$PIN  $tmp/afiro.mps" | shasum -a 256 -c - >/dev/null || {
  echo "afiro.mps does not match its pin -- do not proceed" >&2; exit 1; }
echo "  OK: matches $PIN"

echo "== it must actually be MPS, not netlib's compressed emps =="
grep -q '^ROWS'    "$tmp/afiro.mps" || { echo "no ROWS section"    >&2; exit 1; }
grep -q '^COLUMNS' "$tmp/afiro.mps" || { echo "no COLUMNS section" >&2; exit 1; }
grep -q '^RHS'     "$tmp/afiro.mps" || { echo "no RHS section"     >&2; exit 1; }
grep -q '^ENDATA'  "$tmp/afiro.mps" || { echo "no ENDATA section"  >&2; exit 1; }
head -1 "$tmp/afiro.mps" | sed 's/^/  header: /'
echo "  OK: plain MPS with all four required sections"

# The published optimum travels WITH the instance, so the box never has to trust a number
# typed into a task spec.
printf 'instance\tafiro\npublished_optimum\t-4.6475314286E+02\nsource\thttps://netlib.org/lp/data/readme\n' \
  > "$tmp/afiro.reference.tsv"

aws s3 cp "$tmp/afiro.mps" "$BUCKET/inputs/optimization/afiro.mps" --region "$REGION" --only-show-errors
aws s3 cp "$tmp/afiro.reference.tsv" "$BUCKET/inputs/optimization/afiro.reference.tsv" --region "$REGION" --only-show-errors
echo "done."
echo "  $BUCKET/inputs/optimization/afiro.mps            (3,271 B)"
echo "  $BUCKET/inputs/optimization/afiro.reference.tsv  (netlib's published optimum)"
