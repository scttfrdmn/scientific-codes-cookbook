#!/usr/bin/env bash
# Stage CIFAR-10 (AWS Open Data: the fast.ai image-classification bucket) and the training script.
#   measurements/pytorch-real/stage-inputs.sh <bucket>
# The archive is pinned by sha256 and verified before upload; the task re-verifies it after stage-in.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket, e.g. \$(make -s print-bucket)}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="s3://fast-ai-imageclas/cifar10.tgz"
SHA="637c5814e11aefcb6ee76d5f59c67ddc8de7f5b5077502a195b0833d1e3e4441"   # 135,107,811 bytes
DEST="$BUCKET/inputs/pytorch-real"

sum256() { sha256sum "$1" 2>/dev/null | cut -d' ' -f1 || shasum -a 256 "$1" | cut -d' ' -f1; }

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
aws s3 cp --no-sign-request --only-show-errors "$SRC" cifar10.tgz
got="$(sum256 cifar10.tgz)"
[ "$got" = "$SHA" ] || { echo "cifar10.tgz is $got, pinned $SHA" >&2; exit 1; }
cp "$SCRIPT_DIR/cifar.py" .
python3 -c "import ast,io; ast.parse(io.open('cifar.py',encoding='utf-8').read())"
printf '%s  cifar10.tgz\n%s  cifar.py\n' "$SHA" "$(sum256 cifar.py)" > pins.sha256
for f in cifar10.tgz cifar.py pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$f" "$DEST/$f"
done
cat pins.sha256
echo "staged to $DEST/"
