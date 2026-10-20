#!/usr/bin/env bash
# Copy each chip's evidence (not the 253 MB sketch) into results/<chip>/ (mash) and
# results/<chip>/sourmash/, the prep record into results/prep/, spawn's completion records into
# results/billing.txt, and compare the deterministic outputs across chips.
#
#   bash collect.sh <bucket> <nonce>
set -euo pipefail
B="s3://${1:?pass your bucket}" N=${2:?nonce}
HERE="$(cd "$(dirname "$0")" && pwd)"
ACCT=$(aws sts get-caller-identity --query Account --output text)
REGION="${AWS_REGION:-us-west-2}"
mkdir -p "$HERE/results"; : > "$HERE/results/billing.txt"
for c in arm64 x86_64 amd; do
  case $c in arm64) p=runs t="" ;; x86_64) p=runs-x86_64 t=-x8664 ;; amd) p=runs-amd t=-amd ;; esac
  aws s3 cp --only-show-errors --recursive --exclude "all.msh" "$B/$p/mash-real/" "$HERE/results/$c/"
  aws s3 cp --only-show-errors --recursive "$B/$p/mash-real-sourmash/" "$HERE/results/$c/sourmash/"
  for task in cookbook-mash-real$t-$N cookbook-mash-real-sourmash$t-$N; do
    aws s3 cp --only-show-errors "s3://spawn-results-$ACCT-$REGION/tasks/$task/completion.json" - \
      | python3 -c 'import json,sys,datetime as d; r=json.load(sys.stdin); f=lambda s: d.datetime.fromisoformat(s.replace("Z","+00:00"))
w=(f(r["ended_at"])-f(r["started_at"])).total_seconds(); print("%s\t%s\t%d s\t$%.6f\t$%.4f/hr" % (sys.argv[1], r["state"], w, r["cost_estimate"], r["cost_estimate"]/w*3600))' "$task" \
      >> "$HERE/results/billing.txt"
  done
done
aws s3 cp --only-show-errors --recursive --exclude "genomes.tar" "$B/inputs/mash-real/prep/" "$HERE/results/prep/"
cat "$HERE/results/billing.txt"
{
  echo "== mash: sketches, distances, screen and every derived table; sourmash: its screen. sha256 per chip =="
  for c in arm64 x86_64 amd; do
    sed "s|^|$c  |" "$HERE/results/$c/fingerprints.txt"
    sed "s|^|$c  sourmash/|" "$HERE/results/$c/sourmash/fingerprints.txt"
  done
  echo "== files whose bytes differ across chips =="
  { cat "$HERE"/results/{arm64,x86_64,amd}/fingerprints.txt
    cat "$HERE"/results/{arm64,x86_64,amd}/sourmash/fingerprints.txt | sed 's|  |  sourmash/|'; } \
    | sort -u | awk '{print $2}' | sort | uniq -d
  echo "== end =="
} > "$HERE/results/crosschip.txt"
cat "$HERE/results/crosschip.txt"
