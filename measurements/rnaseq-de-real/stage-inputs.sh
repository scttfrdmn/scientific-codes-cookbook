#!/usr/bin/env bash
# Stage the airway study and the two R scripts for rnaseq-de-real.
#
# DATA TIER: a versioned release. Bioconductor's `airway` 1.30.0 (Bioconductor 3.22) carries
# the whole GSE52778 experiment (Himes et al. 2014) as two count matrices -- `gse` (tximeta /
# salmon, 58,294 genes x 8) and `airway` (summarizeOverlaps, 63,677 genes x 8). The version is
# in the filename and the tarball is pinned by sha256 below; when 3.22 is superseded the same
# file moves to the Archive URL, which is tried second. The version match is load-bearing:
# rnaseqGene 1.34.0, whose published numbers task 1 reproduces, was built against exactly
# airway 1.30.0 and DESeq2 1.50.0.
#
# The scripts are staged as pinned files rather than inlined in the spec (EC2 user data is
# 16 KiB, spawn#1063); each spec checks their sha256 before running them.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
DST="$BUCKET/inputs/rnaseq-de-real"
HERE="$(cd "$(dirname "$0")" && pwd)"

TAR=airway_1.30.0.tar.gz
TAR_SHA=78f2179b25983313d5450876236f92b58c10d0845d62b7a832329e32c871cad1
URLS="https://bioconductor.org/packages/3.22/data/experiment/src/contrib/$TAR
https://bioconductor.org/packages/3.22/data/experiment/src/contrib/Archive/airway/$TAR"

work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
for u in $URLS; do
  curl -fsSL -o "$work/$TAR" "$u" && break || true
done
echo "$TAR_SHA  $work/$TAR" | shasum -a 256 -c -
aws s3 cp "$work/$TAR" "$DST/$TAR" --region "$REGION"
for f in deseq2.R voom.R; do
  aws s3 cp "$HERE/$f" "$DST/$f" --region "$REGION"
  shasum -a 256 "$HERE/$f"
done
