#!/usr/bin/env bash
# Stage the model builder. There is no scientific DATA to stage -- MODFLOW 6's conda package
# ships no example problems (probed: bin/mf6, libmf6.so and get-modflow, nothing else), so the
# model is built in flopy at run time and its answer is known in closed form.
#
# The script travels as a staged, pinned input rather than inline in the TaskSpec because a
# spawn task command rides in EC2 user data, capped at 16,384 bytes.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=modflow does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/model.py" .

echo "== the builder must be valid Python before it costs an instance =="
python3 -c "import ast,io; ast.parse(io.open('model.py',encoding='utf-8').read())" \
  || { echo "  model.py does not parse" >&2; exit 1; }
printf '  %-12s %7s bytes\n' model.py "$(wc -c < model.py)"

shasum -a 256 model.py > pins.sha256
sed 's/^/  /' pins.sha256
for f in model.py pins.sha256; do
  aws s3 cp "$f" "$BUCKET/inputs/modflow/$f" --region "$REGION" --only-show-errors
done
echo "done."
echo "  $BUCKET/inputs/modflow/  (no scientific data: the model is built in flopy)"
