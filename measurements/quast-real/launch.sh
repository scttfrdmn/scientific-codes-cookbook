#!/usr/bin/env bash
# Run the quast-real evaluation on one chip: arm64 (Graviton4), x86_64 (Intel) or amd (AMD EPYC).
#
#   bash launch.sh <bucket> <arm64|x86_64|amd>
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
spec=$(python3 "$ROOT/scripts/spec_arch.py" quast "${2:?arch}" "${1:?bucket}" "$(date +%s)" "$HERE/01-evaluate.task.json")
cat "$spec" | grep -E '"(container|families|ttl|cost_limit)"|c8' || true
spawn task run --spec "$spec" --region "${AWS_REGION:-us-west-2}" --wait
