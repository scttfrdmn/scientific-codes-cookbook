#!/usr/bin/env bash
# The direct run for one chip: the five steps as plain spawn tasks, in the DAG's order
# (mafft -> tree-mafft and muscle -> tree-muscle side by side, then rf).
#   bash measurements/nf-spawn-real/direct-chain.sh <arm64|x86_64|amd> <bucket> <nonce>
set -uo pipefail
A=$1 B=$2 N=$3
HERE="$(cd "$(dirname "$0")" && pwd)"
spec() { python3 "$HERE/../../scripts/spec_arch.py" nf-spawn "$A" "$B" "$N" "$HERE/$1.task.json"; }
run() { spawn task run --spec "$(spec "$1")" --wait; }
( run 10-mafft && run 12-tree-mafft ) & p1=$!
( run 11-muscle && run 13-tree-muscle ) & p2=$!
wait $p1; r1=$?; wait $p2; r2=$?
[ $r1 -eq 0 ] && [ $r2 -eq 0 ] || { echo "a branch failed: $r1 $r2" >&2; exit 1; }
run 14-rf
