#!/usr/bin/env bash
# Stage the Census TIGER/Line 2024 county boundaries, pinned by digest.
#
# TIGER is versioned by year and served from a stable path, so TIGER2024 is a durable id.
# The reason this file and not a prettier one: the shapefile carries Census's OWN computed
# land and water areas (ALAND / AWATER, in m^2) alongside the geometry, so recomputing the
# area from the polygons is a reproduction of a published number rather than just
# "geopandas returned a float". 3,235 counties, 8,235,114 exterior vertices.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=NAME does this}"
SRC="https://www2.census.gov/geo/tiger/TIGER2024/COUNTY/tl_2024_us_county.zip"
SHA="04e668d3502757c837c13444730547cd967f28a2c49aeffb873d1792ab2cb97b"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"

echo "== fetch TIGER2024 county boundaries =="
curl -fsSL "$SRC" -o tiger.zip

echo "== verify against the pinned sha256 =="
printf '%s  %s\n' "$SHA" tiger.zip > pins.sha256
shasum -a 256 -c pins.sha256

echo "== sanity: a zip holding the four shapefile parts =="
# The zip travels as ONE flat file -- geopandas reads it in place, so there is no directory
# to stage (practices/container-path.md). Confirm the members exist before uploading.
# List ONCE into a file: `unzip -l … | grep -q` would have grep exit on the first match,
# SIGPIPE unzip, and fail the whole pipeline under `set -o pipefail` (exit 141). Measured
# here, not theorised -- it reported "missing .shp" for a zip that contains it.
unzip -l tiger.zip > listing.txt
for ext in shp shx dbf prj; do
  grep -q "tl_2024_us_county\.$ext\$" listing.txt || { echo "missing .$ext"; exit 1; }
done
awk '/tl_2024_us_county\.(shp|dbf)$/ {printf "  %s  %s B\n", $4, $1}' listing.txt

echo "== upload =="
aws s3 cp tiger.zip "$BUCKET/inputs/geo-ml/tiger.zip" --only-show-errors
echo "  -> $BUCKET/inputs/geo-ml/tiger.zip"
echo "done."
