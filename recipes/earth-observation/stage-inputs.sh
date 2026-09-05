#!/usr/bin/env bash
# Stage one Sentinel-2 L2A COG band to S3, once, pinned by digest.
#
# The earth-observation recipe is the first cookbook recipe over REAL imagery (the geo
# core recipe is synthetic). The canonical, durable source is the AWS Open Data
# `sentinel-cogs` bucket (RODA) — append-only, so a scene at a given path is immutable.
# Scene S2B_11SKA_20240704_0_L2A: Sentinel-2 L2A over central California, 2024-07-04,
# ~0.0005% cloud. Its 60 m coastal-aerosol band (B01) is a 1830x1830 uint16 COG, ~6 MB —
# small enough to stage, and its STAC item declares EPSG:32611 / shape [1830,1830] /
# uint16 / nodata 0, which the recipe cross-checks against the fetched pixels.
set -euo pipefail

BUCKET="s3://scicookbook-942542972736-us-east-1"
SRC="https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/11/S/KA/2024/7/S2B_11SKA_20240704_0_L2A/B01.tif"
SHA="8626d4bb645a0ec92ba0099b2c6aab94d0ee328a12ada9190d1c2d0d1771a3f0"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"

echo "== fetch B01 from the sentinel-cogs RODA bucket =="
curl -fsSL "$SRC" -o B01.tif

echo "== verify against the pinned sha256 =="
printf '%s  %s\n' "$SHA" B01.tif > pins.sha256
shasum -a 256 -c pins.sha256

echo "== sanity: it is a GeoTIFF (II*\\0 or MM\\0* magic) =="
head -c 2 B01.tif | grep -qE "^(II|MM)$" || { echo "not a TIFF"; exit 1; }

echo "== upload to $BUCKET/inputs/earth-observation/ =="
aws s3 cp B01.tif "$BUCKET/inputs/earth-observation/B01.tif"
echo "done."
