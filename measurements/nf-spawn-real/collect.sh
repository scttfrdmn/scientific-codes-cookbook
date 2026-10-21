#!/usr/bin/env bash
# Pull each chip's evidence into results/<chip>/ (not the alignments or trees, which stay in the
# bucket), then run compare.py over the bucket.
#   bash measurements/nf-spawn-real/collect.sh <bucket> <nonce>
set -euo pipefail
B="${1:?bucket}"; N="${2:?nonce}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ACCT=$(aws sts get-caller-identity --query Account --output text)
S="s3://spawn-results-$ACCT-us-west-2/tasks"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs; t= ;; x86_64) p=runs-x86_64; t=-x8664 ;; amd) p=runs-amd; t=-amd ;; esac
  P="s3://$B/$p/nf-spawn-real/$N"
  aws s3 ls "$P/driver/trace-1.txt" > /dev/null 2>&1 || { echo "$chip: no engine run"; continue; }
  R="$HERE/results/$chip"
  mkdir -p "$R/driver" "$R/direct" "$R/engine" "$R/spawn"
  aws s3 cp --recursive --only-show-errors "$P/driver/" "$R/driver/"
  aws s3 cp --recursive --only-show-errors --exclude '*_aln.fa' --exclude '*.treefile' --exclude 'pf01255*' --exclude '*.mldist' --exclude '*.ckp.gz' --exclude '*.bionj' --exclude '*.iqtree' --exclude '*.uniqueseq.phy' \
    "$P/direct/" "$R/direct/"
  for step in mafft muscle tree-mafft tree-muscle rf; do
    id="cookbook-nf-spawn-real-$step$t-$N"
    aws s3 cp --only-show-errors "$S/$id/completion.json" "$R/spawn/direct-$step.completion.json" || true
  done
  # each engine process's streams, exit code, tool log and spawn record, from run 1's trace
  for run in 1 4; do
    tail -n +2 "$R/driver/trace-$run.txt" | while IFS=$'\t' read -r _ hash _ name status _ _ _ _ _ _ wd _; do
      [ "$status" = CACHED ] && continue
      step=$(echo "$name" | sed -e 's/^TREE (\(.*\))$/tree-\1/' -e 's/ (1)$//' | tr 'A-Z_' 'a-z-' | sed 's/^observe-rf$/rf/')
      d="$R/engine/run$run-$step"; mkdir -p "$d"
      for f in .command.sh .command.out .command.err .exitcode; do
        aws s3 cp --only-show-errors "$wd/$f" "$d/${f#.}" || true
      done
      aws s3 cp --recursive --only-show-errors --exclude '*' --include '*.log' "$wd/" "$d/" || true
      h=$(echo "$wd" | awk -F/ '{print $(NF-1) $NF}' | cut -c1-12)
      aws s3 cp --only-show-errors "$S/nf-$h/completion.json" "$d/completion.json" || true
      aws s3 cp --only-show-errors "$S/nf-$h/command.log" "$d/spawn-command.log" || true
    done
  done
done
python3 -I "$HERE/compare.py" "$B" "$N" "$HERE/results"
