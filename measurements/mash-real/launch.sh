#!/usr/bin/env bash
# Run mash-real on one chip: 01-mash, then 02-sourmash against that chip's own mash screen.
#
#   bash launch.sh <bucket> <arm64|x86_64|amd> <nonce>
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
B=${1:?bucket} A=${2:?arch} N=${3:?nonce}
m=$(python3 "$ROOT/scripts/spec_arch.py" mash "$A" "$B" "$N" "$HERE/01-mash.task.json")
s=$(python3 "$ROOT/scripts/spec_arch.py" sourmash "$A" "$B" "$N" "$HERE/02-sourmash.task.json")
spawn task run --spec "$m" --region "${AWS_REGION:-us-west-2}" --wait
spawn task run --spec "$s" --region "${AWS_REGION:-us-west-2}" --wait
