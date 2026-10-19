#!/usr/bin/env bash
# Stage measurements/openmm-mdanalysis-real: the Amber20 benchmark suite (ambermd.org), plus this
# directory's scripts.
#
# Tier: a versioned public release. The suite is the archive OpenMM's own
# examples/benchmarks/benchmark.py downloads for its amber20-* tests; it has no DOI, so it is pinned
# by sha256 (inputs.sha256). 00-fetch.task.json downloads it ON AWS and records that sha256 --
# nothing passes through this machine -- and every task re-checks it before reading it.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DST="s3://$BUCKET/inputs/openmm-mdanalysis-real"

aws s3 ls --region "$REGION" "$DST/amber20/Amber20_Benchmark_Suite.tar.gz" >/dev/null 2>&1 \
  || spawn task run --wait --spec "$(python3 "$ROOT/scripts/spec_arch.py" openmm-mdanalysis arm64 "$BUCKET" "$(date +%s)" "$HERE/00-fetch.task.json")"
got="$(aws s3 cp --region "$REGION" --only-show-errors "$DST/amber20/suite.sha256" - | awk '{print $1}')"
want="$(awk '{print $1}' "$HERE/inputs.sha256")"
[ "$got" = "$want" ] || { echo "FAIL: staged suite is $got, pinned $want" >&2; exit 1; }

for f in inputs.sha256 sander.sh run.sh real.py check.py; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
echo "staged to $DST/"
