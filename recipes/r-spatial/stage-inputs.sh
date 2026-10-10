#!/usr/bin/env bash
# Stage the checks only. BOTH data inputs are already staged by other recipes and are read, not
# copied: geo-ml's TIGER counties and earth-observation's Sentinel-2 SCL band plus its STAC item.
# A second copy under a second prefix would be a second conversion to keep true, and the whole
# point of the cross-check is that the bytes are identical -- so this script verifies their
# sha256 against the siblings' published pins and refuses to continue otherwise.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=r-spatial does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.R" .
test -s identities.R || { echo "  identities.R is empty" >&2; exit 1; }
printf '  %-16s %6s bytes\n' identities.R "$(wc -c < identities.R)"

echo "== the shared inputs must be the SAME BYTES the sibling recipes verified =="
# Pinned to the full sha256 of the objects those recipes staged. If either recipe re-stages a
# different file, this fails here rather than silently cross-checking against something else.
check() {
  local key="$1" want="$2" bytes="$3" name="$4"
  aws s3 cp --region "$REGION" "$BUCKET/$key" "$name" >/dev/null 2>&1 \
    || { echo "  $key is not staged -- run 'make stage RECIPE=$5' first" >&2; exit 1; }
  local got; got="$(sha256sum "$name" | cut -d' ' -f1)"
  test "$got" = "$want" || { echo "  $key is $got, expected $want" >&2; exit 1; }
  # `wc -c <` pads with leading spaces on macOS and not on Linux, and this script runs on
  # both, so the count is stripped before comparing.
  test "$(wc -c < "$name" | tr -d "[:space:]")" = "$bytes" \
    || { echo "  $key wrong size" >&2; exit 1; }
  printf '  %-26s %s  %s bytes\n' "$(basename "$key")" "${got:0:16}…" "$bytes"
}
check inputs/geo-ml/tiger.zip \
  04e668d3502757c837c13444730547cd967f28a2c49aeffb873d1792ab2cb97b 83913260 tiger.zip geo-ml
check inputs/earth-observation/SCL.tif \
  258f36e59c6c8a6b29c075fda34eb11d1a37d58251f8a9e351a46aa70fd51805 2571146 SCL.tif earth-observation
check inputs/earth-observation/item.json \
  053ecac854fbd139ffe1a9bd58a5ab0dcdfbc421fcb299c552734d17c47d13fb 22880 item.json earth-observation

echo "== the references must still be inside the data, before paying for a box =="
# ALAND/AWATER are Census's own areas and live in the .dbf; the STAC item carries ESA's own class
# percentages. Both are the reference, so a re-publish that dropped them would make the recipe
# assert nothing. Checked without GDAL: the dbf field names are ASCII in the zip's header.
python3 - <<'PY'
import json, re, sys, zipfile
with zipfile.ZipFile("tiger.zip") as z:
    dbf = [n for n in z.namelist() if n.lower().endswith(".dbf")][0]
    head = z.read(dbf)[:4096]
for f in (b"ALAND", b"AWATER"):
    if f not in head:
        sys.exit("  %s absent from the TIGER dbf header" % f.decode())
print("  TIGER dbf carries ALAND and AWATER")
props = json.load(open("item.json"))["properties"]
keys = [k for k in props if k.startswith("s2:") and k.endswith("_percentage")
        and "degraded" not in k and "nodata_pixel" not in k
        and "saturated_defective" not in k]
need = ["s2:vegetation_percentage", "s2:not_vegetated_percentage", "s2:water_percentage"]
for k in need:
    if k not in props:
        sys.exit("  %s absent from the STAC item" % k)
print("  STAC item carries %d class percentages, including %s" % (len(keys), ", ".join(need)))
PY

sha256sum identities.R > pins.sha256
aws s3 cp --region "$REGION" identities.R "$BUCKET/inputs/r-spatial/identities.R" >/dev/null
aws s3 cp --region "$REGION" pins.sha256  "$BUCKET/inputs/r-spatial/pins.sha256"  >/dev/null
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/r-spatial/  (TIGER and SCL are read from the sibling recipes' prefixes)"
