#!/usr/bin/env bash
# Stage seurat-real: the 10x Genomics pbmc_10k_v3 filtered matrix (Cell Ranger 3.0.0 release path,
# unchanged since 2018-11-15), verified against its sha256 before it reaches the bucket, plus the
# scripts the task runs (staged and pinned in the spec: EC2 user data is capped at 16 KiB, spawn#1063).
set -euo pipefail
BUCKET="s3://${1:?usage: stage-inputs.sh <bucket>}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
URL=https://cf.10xgenomics.com/samples/cell-exp/3.0.0/pbmc_10k_v3/pbmc_10k_v3_filtered_feature_bc_matrix.tar.gz
SHA=060af9e4370c6ad877e2987cf8cdc63a6d5c84e57875580f2be6c8e90748cf63
DEST="$BUCKET/inputs/seurat-real/pbmc10k.tar.gz"
if ! aws s3 ls --region "$REGION" "$DEST" >/dev/null 2>&1; then
  T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
  curl -sSfL -o "$T/pbmc10k.tar.gz" "$URL"
  echo "$SHA  $T/pbmc10k.tar.gz" | { sha256sum -c 2>/dev/null || shasum -a 256 -c; }
  aws s3 cp --region "$REGION" --only-show-errors "$T/pbmc10k.tar.gz" "$DEST"
fi
files="scanpy_leg.py seurat_leg.R compare.py run.sh"
for f in $files; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$BUCKET/inputs/seurat-real/$f"
done
(cd "$HERE" && { sha256sum $files 2>/dev/null || shasum -a 256 $files; })
echo "done: $BUCKET/inputs/seurat-real/"
