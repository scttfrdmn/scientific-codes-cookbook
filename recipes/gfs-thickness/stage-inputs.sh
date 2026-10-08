#!/usr/bin/env bash
# Stage one GFS analysis in GRIB2, plus the inventory NOAA publishes beside it.
#
# WHY GFS AND NOT ERA5. ERA5 on the AWS Open Data Registry (`nsf-ncar-era5`) is NetCDF
# throughout and 1.37 GB per file -- wrong format for an eccodes recipe and too big for a cheap
# proof run. GFS ships as GRIB2, which is what operational weather data actually looks like and
# what this catalog could not previously read.
#
# WHY THE .idx TOO. NOAA publishes a wgrib2 inventory beside every GRIB2 file: one line per
# message with its byte offset, variable and level. That makes it the file's OWN manifest, so
# the recipe can check what eccodes decodes against what the publisher says is in there --
# decode statistics against an external statement rather than against itself.
#
# THE BYTES ARE CHECKABLE AT SOURCE. Both objects are single-part uploads, so their S3 ETag IS
# the MD5. The pins below were verified against those ETags at staging time, and a sha256 is
# recorded for the in-task gate.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=gfs-thickness does this}"
REGION="${AWS_REGION:-us-west-2}"
SRC_BUCKET=noaa-gfs-bdp-pds
KEY="gfs.20251001/00/atmos/gfs.t00z.pgrb2.1p00.f000"
MD5_GRIB=7e2c7fb92b5cd5ad5327d288ade6bb88
MD5_IDX=113550982f99a37d0a1930af4cc49e64
SIZE_GRIB=42302512
SIZE_IDX=31097

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"

if aws s3 ls "$BUCKET/inputs/gfs-thickness/gfs.pgrb2.1p00.f000" --region "$REGION" >/dev/null 2>&1; then
  echo "GFS analysis already staged"; exit 0
fi

echo "== fetch the analysis (40.3 MiB) and its inventory from RODA =="
aws s3 cp "s3://$SRC_BUCKET/$KEY"     gfs.pgrb2.1p00.f000     --no-sign-request --region us-east-1 --only-show-errors
aws s3 cp "s3://$SRC_BUCKET/$KEY.idx" gfs.pgrb2.1p00.f000.idx --no-sign-request --region us-east-1 --only-show-errors

echo "== sizes and md5s must match what the publisher serves =="
for pair in "gfs.pgrb2.1p00.f000 $SIZE_GRIB $MD5_GRIB" "gfs.pgrb2.1p00.f000.idx $SIZE_IDX $MD5_IDX"; do
  set -- $pair; f=$1; want_sz=$2; want_md5=$3
  got_sz=$(wc -c < "$f" | tr -d ' ')
  got_md5=$(md5 -q "$f" 2>/dev/null || md5sum "$f" | awk '{print $1}')
  printf '  %-26s %s bytes  md5 %s\n' "$f" "$got_sz" "$got_md5"
  test "$got_sz" = "$want_sz"   || { echo "  size mismatch (want $want_sz)" >&2; exit 1; }
  test "$got_md5" = "$want_md5" || { echo "  md5 mismatch (want $want_md5)" >&2; exit 1; }
done
echo "  OK: both match the ETags S3 serves for them"

echo "== the inventory must contain the fields the thickness check needs =="
for want in "HGT:500 mb" "TMP:500 mb" "RH:500 mb" "HGT:1000 mb" "TMP:1000 mb" "RH:1000 mb"; do
  grep -q ":${want}:" gfs.pgrb2.1p00.f000.idx || { echo "  inventory lacks $want" >&2; exit 1; }
done
NMSG=$(grep -c . gfs.pgrb2.1p00.f000.idx)
NTRH=$(grep -cE ":(HGT|TMP|RH):" gfs.pgrb2.1p00.f000.idx)
echo "  inventory: $NMSG messages total, $NTRH of them HGT/TMP/RH"
echo "  OK: all six thickness fields present at 1000 and 500 mb"

shasum -a 256 gfs.pgrb2.1p00.f000 gfs.pgrb2.1p00.f000.idx > pins.sha256
cat pins.sha256 | sed 's/^/  /'

for f in gfs.pgrb2.1p00.f000 gfs.pgrb2.1p00.f000.idx pins.sha256; do
  aws s3 cp "$f" "$BUCKET/inputs/gfs-thickness/$f" --region "$REGION" --only-show-errors
done
echo "done."
echo "  $BUCKET/inputs/gfs-thickness/  (GFS 2025-10-01 00Z analysis, 1 degree, $NMSG messages)"
