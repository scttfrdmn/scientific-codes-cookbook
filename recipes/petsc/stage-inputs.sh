#!/usr/bin/env bash
# Stage the petsc recipe's two scripts: ex2.py (the KSP problems) and ksp.sh (the checks).
# Staged, and pinned by sha256 in the spec, because spawn carries the task script in EC2 user
# data, which is capped at 16 KiB (spawn#1063); inline, this spec was rejected on both chips.
set -euo pipefail
BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=petsc does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for f in ex2.py ksp.sh; do
  aws s3 cp --region "$REGION" --only-show-errors "$SCRIPT_DIR/$f" "$BUCKET/inputs/petsc/$f"
done
(cd "$SCRIPT_DIR" && { sha256sum ex2.py ksp.sh 2>/dev/null || shasum -a 256 ex2.py ksp.sh; })
echo "done."
echo "  $BUCKET/inputs/petsc/"
