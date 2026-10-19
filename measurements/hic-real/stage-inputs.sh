#!/usr/bin/env bash
# Stage measurements/hic-real: 4DN's in situ Hi-C on GM12878 (experiment set 4DNESLLA3R1V, MboI,
# 4DN Hi-C pipeline v0.3.0, 2024 reprocessing on GRCh38) from the 4DN Data Portal's RODA bucket,
# s3://4dn-open-data-public, copied S3-to-S3 into the bucket -- nothing is downloaded locally.
#
#   4DNFILGIU5G7.pairs.gz  replicate 1 contact list (pairtools 1.1.0 parse/sort/dedup/select)
#   4DNFI4EZMKAO.pairs.gz  replicate 2 contact list
#   4DNFIHMMV629.pairs.gz  the combined list: both replicates merged, then mapq1>30 and mapq2>30
#   4DNFIFBPTHCU.mcool     the published multi-resolution matrix built from the combined list
#   4DNFI823LSII.chrom.sizes  hg38.mainonly, the chromsizes every step above used
#
# `aws s3 cp` between buckets is a server-side multipart copy. The pins are checked on the box,
# where the bytes are: inputs.md5 is the md5 the 4DN portal publishes for each file, inputs.sha256
# ours (from the first verifying run); each task checks both before it runs anything.
#   stage-inputs.sh <bucket>
set -euo pipefail
B="${1:?pass your bucket}"
P="inputs/hic-real"
D="$(cd "$(dirname "$0")" && pwd)"
SRC=s3://4dn-open-data-public/fourfront-webprod
OBJ="wfoutput/8e4f23a3-1c7a-4675-81ac-ede7ea1a865e/4DNFILGIU5G7.pairs.gz:1686615976
wfoutput/6402b283-f35b-42ef-8e3f-260c08f4a8df/4DNFI4EZMKAO.pairs.gz:1556716551
wfoutput/26e452d0-97f8-460c-a13d-ad423d1aa1ba/4DNFIHMMV629.pairs.gz:2775408552
wfoutput/4e3aab1e-4b51-4149-a89a-743a130db856/4DNFIFBPTHCU.mcool:1094269473
files/4a6d10ee-2edb-4402-a98f-0edb1d58f5e9/4DNFI823LSII.chrom.sizes:365"
for e in $OBJ; do
  o=${e%%:*}; n=${e##*:}; f=$(basename "$o")
  got=$(aws s3api head-object --bucket "$B" --key "$P/$f" --region us-west-2 --query ContentLength --output text 2>/dev/null || true)
  if [ "$got" != "$n" ]; then
    aws s3 cp "$SRC/$o" "s3://$B/$P/$f" --source-region us-east-1 --region us-west-2 --only-show-errors
    got=$(aws s3api head-object --bucket "$B" --key "$P/$f" --region us-west-2 --query ContentLength --output text)
  fi
  [ "$got" = "$n" ] || { echo "FAIL: $f is $got bytes, 4DN publishes $n"; exit 1; }
  echo "ok  $f  $n bytes"
done
for f in inputs.sha256 inputs.md5 pairs.sh cool.sh ident.py; do
  aws s3 cp "$D/$f" "s3://$B/$P/$f" --region us-west-2 --only-show-errors
done
echo "staged s3://$B/$P/"
