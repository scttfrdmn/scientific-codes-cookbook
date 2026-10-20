#!/usr/bin/env bash
# Stage one whole GFS forecast cycle at 0.25 deg -- every forecast hour of gfs.20251001/00 (the
# cycle recipes/gfs-thickness reads at 1 deg), 209 GRIB2 files plus the 209 wgrib2 inventories
# NOAA publishes beside them, 113.1 GB -- and this directory's script, into the bucket.
#
# S3 TO S3. The copy is server-side (CopyObject / UploadPartCopy from the us-east-1 RODA bucket
# into your us-west-2 one); no GFS byte passes through the machine running this script.
#
# THE PIN. Every object in noaa-gfs-bdp-pds is a single-part upload, so its source ETag IS its
# MD5. gfs-cycle.md5 (committed) is that list, read from the source bucket; gfs-cycle.sha256
# (committed) is the sha256 of the same bytes, computed on AWS by pin.task.json, which first checks
# every staged file against the source MD5. The copies in your bucket are multipart, so their own
# ETags are not MD5s -- the in-task sha256sum -c is the gate.
set -euo pipefail

B="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
D="$(cd "$(dirname "$0")" && pwd)"
SRC=noaa-gfs-bdp-pds
CYC="gfs.20251001/00/atmos"
DST="s3://$B/inputs/gfs-thickness-real"

echo "== the source cycle: 209 forecast hours f000-f384 (hourly to f120, then 3-hourly) =="
aws s3api list-objects-v2 --bucket "$SRC" --prefix "$CYC/gfs.t00z.pgrb2.0p25.f" --no-sign-request \
  --region us-east-1 --query 'Contents[].[Key,Size,ETag]' --output text \
  | awk '{k=$1; sub(/.*\//,"",k); e=$3; gsub(/"/,"",e); if (e ~ /-/) {print "FAIL multipart " k > "/dev/stderr"; exit 1}; print e "  " k}' \
  | sort -k2 > "$D/gfs-cycle.md5.new"
n=$(grep -c . "$D/gfs-cycle.md5.new")
[ "$n" = 418 ] || { echo "FAIL: expected 418 objects (209 GRIB2 + 209 .idx), got $n"; exit 1; }
if [ -f "$D/gfs-cycle.md5" ]; then
  cmp -s "$D/gfs-cycle.md5" "$D/gfs-cycle.md5.new" || { echo "FAIL: the source cycle changed since it was pinned"; exit 1; }
  rm "$D/gfs-cycle.md5.new"
else
  mv "$D/gfs-cycle.md5.new" "$D/gfs-cycle.md5"
fi
echo "  418 objects, every one single-part (ETag = MD5)"

echo "== server-side copy into $DST/cycle/ =="
aws s3 sync "s3://$SRC/$CYC/" "$DST/cycle/" --exclude '*' --include 'gfs.t00z.pgrb2.0p25.f*' \
  --source-region us-east-1 --region "$REGION" --only-show-errors
got=$(aws s3 ls "$DST/cycle/" --region "$REGION" | awk '{n++; s+=$3} END{print n, s}')
want=$(aws s3 ls "s3://$SRC/$CYC/" --no-sign-request --region us-east-1 \
  | awk '$4 ~ /^gfs\.t00z\.pgrb2\.0p25\.f/ {n++; s+=$3} END{print n, s}')
[ "$got" = "$want" ] || { echo "FAIL: staged count/bytes $got != source $want"; exit 1; }
echo "  staged: $got (objects bytes) == source"

for f in gfs.py compare.py gfs-cycle.md5; do aws s3 cp "$D/$f" "$DST/$f" --region "$REGION" --only-show-errors; done
[ -f "$D/gfs-cycle.sha256" ] && aws s3 cp "$D/gfs-cycle.sha256" "$DST/gfs-cycle.sha256" --region "$REGION" --only-show-errors
( cd "$D" && shasum -a 256 gfs.py compare.py )
echo "staged to $DST/  (113.1 GB in S3 Standard, ~\$2.60/month: delete inputs/gfs-thickness-real/cycle/ when done)"
