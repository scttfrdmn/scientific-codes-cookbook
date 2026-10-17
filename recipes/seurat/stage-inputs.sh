#!/usr/bin/env bash
# Stage the seurat recipe's scripts (the Scanpy leg, the Seurat leg, the comparison, and the shell
# that runs them). The matrix itself is the scanpy recipe's pbmc3k, staged by `make stage
# RECIPE=scanpy`. Staged, and pinned by sha256 in the spec, because spawn carries the task script
# in EC2 user data, which is capped at 16 KiB (spawn#1063); inline, this spec was rejected.
set -euo pipefail
BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=seurat does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
files="scanpy_leg.py seurat_leg.R compare.py crosscheck.sh"
for f in $files; do
  aws s3 cp --region "$REGION" --only-show-errors "$SCRIPT_DIR/$f" "$BUCKET/inputs/seurat/$f"
done
(cd "$SCRIPT_DIR" && { sha256sum $files 2>/dev/null || shasum -a 256 $files; })
aws s3 ls --region "$REGION" "$BUCKET/inputs/scanpy/pbmc3k.tar.gz" >/dev/null \
  || echo "  note: $BUCKET/inputs/scanpy/pbmc3k.tar.gz is missing -- run make stage RECIPE=scanpy" >&2
echo "done."
echo "  $BUCKET/inputs/seurat/"
