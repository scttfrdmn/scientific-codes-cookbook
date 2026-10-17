#!/usr/bin/env bash
# Stage a full year of NYC TLC yellow-taxi trip records (2023, 12 monthly Parquet files, 635 MB,
# 38,310,226 trips) plus this measurement's two R scripts into the cookbook bucket.
#
#   bash measurements/r-real/stage-inputs.sh <bucket>
#
# DATA TIER. RODA lists the TLC data as s3://nyc-tlc, but that bucket now refuses anonymous
# ListObjects *and* GetObject (403, checked 2026-10-17). The TLC's own distribution is the CloudFront
# origin its trip-record page links to -- a stable public source, not a versioned one, and the TLC
# has re-issued months before. So the pin is the sha256 of each file as fetched on 2026-10-17; a
# re-issued month fails here, before anything reaches S3, rather than silently changing the year.
#
# THE REFERENCES IN check.R (duckdb-reference.py). Computed from these exact bytes by DuckDB 1.5.6 (an independent Parquet
# reader and engine), with month taken from the file name and money as round(total_amount*100):
#   SELECT count(*), sum(round(total_amount*100)::HUGEINT)                    -> 38310226, 109038341189
#   GROUP BY month, payment_type / PULocationID, ORDER BY ALL, as "k[,k],n,cents" lines -> sha256
#   the model filter (RatecodeID=1, 0<fare<=500, 0<distance<=100, 1<minutes<=180) -> 34184942
set -euo pipefail

BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
DST="$BUCKET/inputs/r-real"
HERE="$(cd "$(dirname "$0")" && pwd)"
URL=https://d37ci6vzurychx.cloudfront.net/trip-data

PINS="32df6f67578fa86c484a6b5ef23a5281992ff085521082340b0f9e5889e9a572  yellow_tripdata_2023-01.parquet
4809e6aaac64f05a62d16a25d55713be1537ad64fc261e895eaf2d2120fe750a  yellow_tripdata_2023-02.parquet
e7d44943111b007bf0e7084863511886e0db29f862f9a96239383a1f86c6c26e  yellow_tripdata_2023-03.parquet
95c01c53c865e06489179bdecce3a59603697a1557c4d5d183c20ae13a144dc6  yellow_tripdata_2023-04.parquet
9bd7d1c557bb9d0413619ba296b4828b884852a70bd07f2230b83f080d9a8591  yellow_tripdata_2023-05.parquet
3e60e29b5df45683948b68acbfb503aa459d14162c610622585c6580fdbfc73a  yellow_tripdata_2023-06.parquet
98504ae351d920e9e0b61241f5707f90ee1588cfb199c378459234b5035805ab  yellow_tripdata_2023-07.parquet
84ce36a2f84fa14c12fd976c79a7d4f30c871f1782b08ca7c27bb76ab946e66b  yellow_tripdata_2023-08.parquet
091bf156bea5bb10bd217f5f12375f966f8789349af45d5ee4153e3e1e077a87  yellow_tripdata_2023-09.parquet
dc5f069476d350df802ed8120f3b39ec321c34cfa905381e87f608c78ca0c645  yellow_tripdata_2023-10.parquet
af203de0e73a92d640121f02a34326f25037d62380fc406237c87c2a4231ae58  yellow_tripdata_2023-11.parquet
ab4f4db11cb31484747f62eb507c13fcfaff290aaf59c13e6465dea0b1166b9c  yellow_tripdata_2023-12.parquet"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
echo "== TLC yellow 2023, 12 months =="
while read -r sha f; do
  if aws s3 ls --region "$REGION" "$DST/tlc/$f" >/dev/null 2>&1; then echo "  $f already staged"; continue; fi
  curl -fsSL --retry 5 -o "$tmp/$f" "$URL/$f"
  got=$(shasum -a 256 "$tmp/$f" | cut -d' ' -f1)
  [ "$got" = "$sha" ] || { echo "  $f: sha256 $got, pinned $sha -- the TLC re-issued it" >&2; exit 1; }
  aws s3 cp --region "$REGION" --only-show-errors "$tmp/$f" "$DST/tlc/$f"
  echo "  $f verified and staged"
  rm -f "$tmp/$f"
done <<< "$PINS"

echo "== scripts =="
for s in analysis.R check.R; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$s" "$DST/$s"
  echo "  $s  $(shasum -a 256 "$HERE/$s" | cut -d' ' -f1)"
done
echo "$DST/"
