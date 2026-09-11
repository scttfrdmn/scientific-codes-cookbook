#!/usr/bin/env bash
# Stage a real NCEP air-temperature netCDF to S3, once, pinned by digest.
#
# The climate recipe exercises the netCDF reader path on a REAL file (the env's D3 only
# round-trips a self-generated one). pydata/xarray-data air_temperature.nc: NCEP
# reanalysis, variable "air", (time=2920, lat=25, lon=53). Pinned at a commit + sha256.
set -euo pipefail
BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=NAME does this}"
COMMIT="2baf0c22d9671a2058415f17e71b3fe06239a3dd"
SRC="https://raw.githubusercontent.com/pydata/xarray-data/${COMMIT}/air_temperature.nc"
SHA="c606b89c35970a2983b914b76df4adbb409003ef34aa7cfd7f582e41f307482b"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT; cd "$tmp"
echo "== fetch air_temperature.nc (pydata/xarray-data) =="
curl -fsSL "$SRC" -o air.nc
echo "== verify against pinned sha256 =="
printf '%s  %s\n' "$SHA" air.nc > pins.sha256; shasum -a 256 -c pins.sha256
echo "== sanity: netCDF magic (CDF or HDF5) =="
head -c 3 air.nc | grep -q "CDF" || head -c 4 air.nc | grep -q "HDF" || { echo "not a netCDF"; exit 1; }
echo "== upload =="
aws s3 cp air.nc "$BUCKET/inputs/climate/air.nc"
echo "done."
