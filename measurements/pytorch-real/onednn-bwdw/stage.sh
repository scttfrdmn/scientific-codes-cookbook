#!/usr/bin/env bash
# Stage the oneDNN backward-weights probes: PyTorch's own aarch64 wheel (pinned by the sha256 that
# download.pytorch.org publishes) and the probe scripts. The tasks re-verify all of it after stage-in.
#   measurements/pytorch-real/onednn-bwdw/stage.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket, e.g. \$(make -s print-bucket)}"
DIR="$(cd "$(dirname "$0")" && pwd)"
WHL="torch-2.14.1+cpu-cp314-cp314-manylinux_2_28_aarch64.whl"
SHA="f56cd23a3bf003bfbed3da0645ba09b3ccc66c0a2f2ac726a9b98f34e0e4926f"
DEST="$BUCKET/inputs/pytorch-real/onednn-bwdw"
sum256() { sha256sum "$1" 2>/dev/null | cut -d' ' -f1 || shasum -a 256 "$1" | cut -d' ' -f1; }

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
curl -sSfLo "$tmp/$WHL" "https://download.pytorch.org/whl/cpu/torch-2.14.1%2Bcpu-cp314-cp314-manylinux_2_28_aarch64.whl"
[ "$(sum256 "$tmp/$WHL")" = "$SHA" ] || { echo "$WHL does not match its pin" >&2; exit 1; }
cp "$DIR"/convprobe.py "$DIR"/convtime.py "$DIR"/run.sh "$DIR"/run2.sh "$tmp/"
( cd "$tmp"
  printf '%s  %s\n%s  convprobe.py\n%s  run.sh\n' "$SHA" "$WHL" "$(sum256 convprobe.py)" "$(sum256 run.sh)" > pins.sha256
  printf '%s  convtime.py\n%s  run2.sh\n%s  %s\n' "$(sum256 convtime.py)" "$(sum256 run2.sh)" "$SHA" "$WHL" > pins2.sha256 )
aws s3 cp --only-show-errors --recursive "$tmp/" "$DEST/"
echo "staged to $DEST/"
