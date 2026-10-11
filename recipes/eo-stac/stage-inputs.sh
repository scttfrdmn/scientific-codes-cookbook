#!/usr/bin/env bash
# Stage the checks only. All three data inputs are already staged by the earth-observation
# recipe and are READ, not copied: a second copy under a second prefix is a second conversion to
# keep true, and the cross-library comparison only means something on identical bytes. Pinned to
# the full sha256 of the objects that recipe verified, so a re-stage fails here rather than
# silently comparing against something else.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=eo-stac does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.py" .
python3 -c "import ast,io; ast.parse(io.open('identities.py',encoding='utf-8').read())" \
  || { echo "  identities.py does not parse" >&2; exit 1; }
printf '  %-16s %6s bytes\n' identities.py "$(wc -c < identities.py | tr -d '[:space:]')"

echo "== the shared inputs must be the SAME BYTES earth-observation verified =="
check() {
  local key="$1" want="$2" bytes="$3" name="$4"
  aws s3 cp --region "$REGION" "$BUCKET/$key" "$name" >/dev/null 2>&1 \
    || { echo "  $key is not staged -- run 'make stage RECIPE=earth-observation' first" >&2; exit 1; }
  local got; got="$(sha256sum "$name" | cut -d' ' -f1)"
  test "$got" = "$want" || { echo "  $key is $got, expected $want" >&2; exit 1; }
  # `wc -c <` pads with leading spaces on macOS and not on Linux; this runs on both.
  test "$(wc -c < "$name" | tr -d '[:space:]')" = "$bytes" \
    || { echo "  $key wrong size" >&2; exit 1; }
  printf '  %-14s %s…  %12s bytes\n' "$(basename "$key")" "${got:0:16}" "$bytes"
}
check inputs/earth-observation/SCL.tif \
  258f36e59c6c8a6b29c075fda34eb11d1a37d58251f8a9e351a46aa70fd51805 2571146 SCL.tif
check inputs/earth-observation/B01.tif \
  8626d4bb645a0ec92ba0099b2c6aab94d0ee328a12ada9190d1c2d0d1771a3f0 6192186 B01.tif
check inputs/earth-observation/item.json \
  053ecac854fbd139ffe1a9bd58a5ab0dcdfbc421fcb299c552734d17c47d13fb 22880 item.json

echo "== the item must still carry the metadata every check below depends on =="
# A pin fixes bytes, not meaning. Three things are load-bearing: the asset transforms (the grid
# both libraries are asserted to reproduce), ESA's class percentages (the external anchor), and
# proj:epsg in the RAW json -- the recipe's headline is that pystac renames it on parse, so if
# upstream ever ships proj:code directly the premise changes rather than the check failing.
python3 - <<'PY'
import json, sys
d = json.load(open("item.json"))
p, a = d["properties"], d["assets"]
if "proj:epsg" not in p:
    sys.exit("  the item no longer carries proj:epsg in its raw json -- re-derive the premise")
print("  raw json proj:epsg = %s" % p["proj:epsg"])
for k, want_shape in (("scl", [5490, 5490]), ("coastal", [1830, 1830])):
    if a[k].get("proj:shape") != want_shape:
        sys.exit("  asset %s declares %s, expected %s" % (k, a[k].get("proj:shape"), want_shape))
    if "proj:transform" not in a[k]:
        sys.exit("  asset %s has no proj:transform -- odc-stac's native grid comes from it" % k)
    print("  asset %-8s shape=%s transform=%s" % (k, want_shape, a[k]["proj:transform"][:6]))
need = [n for n in ("vegetation", "not_vegetated", "water", "unclassified", "dark_features",
                    "cloud_shadow", "snow_ice", "nodata_pixel", "thin_cirrus",
                    "medium_proba_clouds", "high_proba_clouds", "saturated_defective_pixel")]
miss = [n for n in need if "s2:%s_percentage" % n not in p]
if miss:
    sys.exit("  the item lacks percentages for %s" % ", ".join(miss))
print("  all %d class percentages present, summing to %.6f" %
      (len(need), sum(float(p["s2:%s_percentage" % n]) for n in need)))
PY

sha256sum identities.py > pins.sha256
aws s3 cp --region "$REGION" identities.py "$BUCKET/inputs/eo-stac/identities.py" >/dev/null
aws s3 cp --region "$REGION" pins.sha256  "$BUCKET/inputs/eo-stac/pins.sha256"  >/dev/null
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/eo-stac/  (the scene is read from earth-observation's prefix)"
