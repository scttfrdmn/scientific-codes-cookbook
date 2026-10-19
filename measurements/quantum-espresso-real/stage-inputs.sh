#!/usr/bin/env bash
# Stage measurements/quantum-espresso-real: the QE Foundation's AUSURF112 benchmark (a 112-atom
# Au surface slab: ausurf.in and its ultrasoft Au pseudopotential, 322 KB) at QEF/benchmarks commit
# eff15189, streamed straight into the bucket, plus this directory's scripts. Each staged object is
# re-read from the bucket and checked against inputs.sha256; the task checks them again.
#   stage-inputs.sh <bucket>
set -euo pipefail
B="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
D="$(cd "$(dirname "$0")" && pwd)"
P="s3://$B/inputs/quantum-espresso-real"
U=https://raw.githubusercontent.com/QEF/benchmarks/eff15189e7b1120f5facc6fef202cbd6ff0ed26b/AUSURF112
for f in ausurf.in Au.pbe-nd-van.UPF; do
  curl -fsSL --max-time 120 "$U/$f" | aws s3 cp - "$P/$f" --region "$REGION" --only-show-errors
  got=$(aws s3 cp "$P/$f" - --region "$REGION" | shasum -a 256 | cut -d' ' -f1)
  want=$(awk -v f="$f" '$2==f{print $1}' "$D/inputs.sha256")
  [ "$got" = "$want" ] || { echo "FAIL: $f staged as $got, pinned $want"; exit 1; }
  echo "ok  $f  $got"
done
for f in inputs.sha256 run.sh check.py; do
  aws s3 cp "$D/$f" "$P/$f" --region "$REGION" --only-show-errors
done
echo "staged $P/"
