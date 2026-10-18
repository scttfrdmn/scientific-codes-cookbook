#!/usr/bin/env bash
# Stage scanpy-real: one CELLxGENE Census source dataset from RODA, plus the scripts the task runs.
#
# Dataset: ed5d841d-6346-47d4-ab2f-7119ad7e3a35, "nygc multimodal pbmc" -- Hao et al. 2021,
# "Integrated analysis of multimodal single-cell data" (Cell, doi:10.1016/j.cell.2021.04.048), the
# Seurat v4 PBMC reference: 161,764 PBMCs from 8 donors, 10x 3' v3 CITE-seq, integer RNA counts in
# raw.X and the authors' RNA+protein (WNN) annotation in obs['celltype.l1'/'celltype.l2'].
# Pinned by the Census LTS release it is read from (2025-01-30, `do_not_delete` in release.json)
# and by sha256; the task re-verifies the hash before reading a byte.
#
# The scripts are staged and pinned in the spec, not inlined: EC2 user data is 16 KiB (spawn#1063).
set -euo pipefail
BUCKET="s3://${1:?usage: stage-inputs.sh <bucket>}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ID=ed5d841d-6346-47d4-ab2f-7119ad7e3a35
SRC="s3://cellxgene-census-public-us-west-2/cell-census/2025-01-30/h5ads/$ID.h5ad"
SHA=c55594bd58d86624f9d55626f841cb97aabf401e2ffed918933df214c693aa29   # checked in-task (hao2021.task.json)
SIZE=2665741119
ETAG='"a7356ed78fe70f3303cce288468c253f-318"'
DEST="$BUCKET/inputs/scanpy-real/$ID.h5ad"

if ! aws s3 ls --region "$REGION" "$DEST" >/dev/null 2>&1; then
  # Server-side, RODA to bucket: the 2.67 GB never touches this machine. The size and multipart
  # ETag are checked here; the sha256 is checked in-task, on the box, before a byte is read.
  got=$(aws s3api head-object --no-sign-request --region us-west-2 --bucket cellxgene-census-public-us-west-2 \
        --key "${SRC#s3://cellxgene-census-public-us-west-2/}" --query '[ContentLength,ETag]' --output text)
  test "$got" = "$SIZE	$ETAG" || { echo "Census object is '$got', expected '$SIZE $ETAG' -- do not proceed" >&2; exit 1; }
  aws s3 cp --region "$REGION" --only-show-errors --source-region us-west-2 "$SRC" "$DEST"
fi
files="pipeline.py compare.py run.sh"
for f in $files; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$BUCKET/inputs/scanpy-real/$f"
done
(cd "$HERE" && { sha256sum $files 2>/dev/null || shasum -a 256 $files; })
echo "done: $BUCKET/inputs/scanpy-real/"
