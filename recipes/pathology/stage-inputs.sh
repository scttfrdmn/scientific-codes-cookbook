#!/usr/bin/env bash
# Stage the checks. The SCIENCE is not staged: CAMELYON16 lives on RODA in us-west-2, the same
# region the task runs in, and the task reads it straight from there -- so there is no second
# copy to keep true and no egress. What this script pins is the publisher's two manifests
# (checksums.md5, reference.csv) by sha256, because every reference below is read out of them
# at run time and a swapped manifest would silently redefine the expected answer.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=pathology does this}"
REGION="${AWS_REGION:-us-west-2}"
RODA="s3://camelyon-dataset/CAMELYON16"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.py" .

echo "== the checks must parse =="
python3 -c "import ast,io; ast.parse(io.open('identities.py',encoding='utf-8').read())" \
  || { echo "  identities.py does not parse" >&2; exit 1; }
printf '  %-16s %6s bytes\n' identities.py "$(wc -c < identities.py)"

echo "== the publisher's manifests, pinned by sha256 =="
# These are the two files every reference in the recipe is read from: checksums.md5 supplies
# the md5 of all three data objects, reference.csv supplies the slide's ground-truth class.
# Pinning them is what stops a re-published manifest redefining the expected answer.
aws s3 cp --region "$REGION" "$RODA/checksums.md5" checksums.md5 >/dev/null
aws s3 cp --region "$REGION" "$RODA/evaluation/reference.csv" reference.csv >/dev/null
cat > manifests.sha256 <<'EOF'
5e4a4efe198cd0427d32f000c7f556f52fbf7f8a422d5210a7c1e5a12ab4ce08  checksums.md5
12c526868219a5c54931ac052057c6e9ad05df3b67d2b5dadf22ec8aa764601c  reference.csv
EOF
sha256sum -c manifests.sha256 || { echo "  publisher manifest changed -- re-derive before trusting any reference" >&2; exit 1; }
printf '  %-16s %6s bytes, %s md5 entries\n' checksums.md5 "$(wc -c < checksums.md5)" "$(wc -l < checksums.md5)"
printf '  %-16s %6s bytes, %s slides\n' reference.csv "$(wc -c < reference.csv)" "$(( $(wc -l < reference.csv) - 1 ))"

echo "== the three objects must be covered by checksums.md5, before paying for a box =="
# A missing line would make the strongest check in the recipe silently unassertable.
for k in images/tumor_091.tif masks/tumor_091_mask.tif annotations/tumor_091.xml; do
  line="$(grep -E " \*?${k}$" checksums.md5 || true)"
  test -n "$line" || { echo "  $k has no checksums.md5 entry" >&2; exit 1; }
  printf '  %s\n' "$line"
done

echo "== the slide must be the class the recipe reproduces =="
grep -q '^tumor_091.tif,tumor,macro,' reference.csv \
  || { echo "  reference.csv no longer classes tumor_091 as tumor/macro" >&2; exit 1; }
printf '  %s\n' "$(grep '^tumor_091.tif,' reference.csv)"

echo "== the RODA objects must exist and be the expected size =="
for k in images/tumor_091.tif:546437217 masks/tumor_091_mask.tif:8357394 annotations/tumor_091.xml:85520; do
  key="${k%:*}"; want="${k##*:}"
  got="$(aws s3api head-object --bucket camelyon-dataset --key "CAMELYON16/$key" \
          --region "$REGION" --query ContentLength --output text)"
  test "$got" = "$want" || { echo "  $key is $got bytes, expected $want" >&2; exit 1; }
  printf '  %-34s %12s bytes\n' "$key" "$got"
done

sha256sum identities.py checksums.md5 reference.csv > pins.sha256
aws s3 cp --region "$REGION" identities.py  "$BUCKET/inputs/pathology/identities.py"  >/dev/null
aws s3 cp --region "$REGION" checksums.md5  "$BUCKET/inputs/pathology/checksums.md5"  >/dev/null
aws s3 cp --region "$REGION" reference.csv  "$BUCKET/inputs/pathology/reference.csv"  >/dev/null
aws s3 cp --region "$REGION" pins.sha256    "$BUCKET/inputs/pathology/pins.sha256"    >/dev/null
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/pathology/  (the slide itself is read from RODA, not copied)"
