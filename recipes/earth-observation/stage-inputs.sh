#!/usr/bin/env bash
# Stage one Sentinel-2 L2A scene's red, NIR and scene-classification bands, plus the
# scene's own STAC item, pinned by digest.
#
# The canonical durable source is the AWS Open Data `sentinel-cogs` bucket (RODA) —
# append-only, so a scene at a given path is immutable. Scene S2B_11SKA_20240704_0_L2A:
# central California, 2024-07-04, 0.000471% cloud.
#
# The STAC item is staged too, and that is the point of the recipe rather than a
# convenience: it carries ESA's OWN scene-classification percentages, computed by the L2A
# processor from the SCL band. Staging it means the assertion compares the run's pixels
# against the depositors' published numbers read from their file — not against numbers
# typed into a smoke check, which a typo could make pass.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=NAME does this}"
SRC="s3://sentinel-cogs/sentinel-s2-l2a-cogs/11/S/KA/2024/7/S2B_11SKA_20240704_0_L2A"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"

echo "== fetch from the sentinel-cogs RODA bucket (us-west-2, unsigned) =="
for f in B04.tif B08.tif SCL.tif S2B_11SKA_20240704_0_L2A.json; do
  aws s3 cp "$SRC/$f" "$f" --no-sign-request --region us-west-2 --only-show-errors
done
mv S2B_11SKA_20240704_0_L2A.json item.json

echo "== verify against the pinned sha256s, before anything is uploaded =="
cat > pins.sha256 <<'EOF'
8786ece1f55d3bf309478a8a79c651194cf446e83701903cf373dea96f05c6ab  B04.tif
52030ffc4ba79f40e9e50eaf9502656b3b54fac74a2f0f9a65078799101f8a95  B08.tif
258f36e59c6c8a6b29c075fda34eb11d1a37d58251f8a9e351a46aa70fd51805  SCL.tif
053ecac854fbd139ffe1a9bd58a5ab0dcdfbc421fcb299c552734d17c47d13fb  item.json
EOF
shasum -a 256 -c pins.sha256

echo "== independent re-check: TIFF magic, and the item declares the grid the task assumes =="
# A pinned digest already fixes the bytes; this catches a pin updated without re-reading
# what it points at. The grids are load-bearing: the task aggregates 10 m NDVI 2x2 onto
# the 20 m SCL grid, which is exact only if both bands are 10980^2 on the same origin and
# the SCL is exactly half that.
for f in B04.tif B08.tif SCL.tif; do
  head -c 2 "$f" | grep -qE "^(II|MM)$" || { echo "$f: not a TIFF"; exit 1; }
done
python3 - <<'PY'
import json, sys
d = json.load(open("item.json"))
a = d["assets"]
assert d["properties"]["proj:epsg"] == 32611, d["properties"]["proj:epsg"]
for key, shape, px in (("red", [10980, 10980], 10.0), ("nir", [10980, 10980], 10.0),
                       ("scl", [5490, 5490], 20.0)):
    got = a[key]["proj:shape"]
    tr = a[key]["proj:transform"]
    assert got == shape, f"{key}: shape {got} != {shape}"
    assert tr[0] == px and tr[2] == 199980.0 and tr[5] == 4100040.0, f"{key}: transform {tr}"
# ESA's published percentages must be present and sum to 100 -- if the item ever stops
# carrying them the recipe has no reference and should fail here, not on the box.
keys = ["vegetation", "not_vegetated", "water", "unclassified", "dark_features",
        "cloud_shadow", "snow_ice", "medium_proba_clouds", "high_proba_clouds",
        "thin_cirrus", "nodata_pixel"]
pct = {k: d["properties"][f"s2:{k}_percentage"] for k in keys}
total = sum(pct.values())
assert abs(total - 100.0) < 1e-4, f"published percentages sum to {total}"
print(f"item.json: EPSG:32611, grids 10980^2 @10m / 5490^2 @20m, "
      f"{len(pct)} published percentages summing to {total:.6f}")
PY

echo "== upload =="
for f in B04.tif B08.tif SCL.tif item.json; do
  aws s3 cp "$f" "$BUCKET/inputs/earth-observation/$f" --only-show-errors
  echo "  -> $BUCKET/inputs/earth-observation/$f"
done
echo "done."
