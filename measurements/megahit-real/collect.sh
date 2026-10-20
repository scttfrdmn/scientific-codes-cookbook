#!/usr/bin/env bash
# Copy each chip's small evidence files (not the contigs) into results/<chip>/<accession>/, and the
# evaluation into results/evaluate/.
#
#   bash collect.sh <bucket> [chip ...]      (default: arm64 arm64-repeat x86_64 amd arm64-t1 x86_64-t1 amd-t1 evaluate)
set -euo pipefail
B="s3://${1:?pass your bucket}"
shift
HERE="$(cd "$(dirname "$0")" && pwd)"
CHIPS="${*:-arm64 arm64-repeat x86_64 amd arm64-t1 x86_64-t1 amd-t1 evaluate}"
for c in $CHIPS; do
  case $c in
    arm64) src="$B/runs/megahit-real/" ;;
    x86_64) src="$B/runs-x86_64/megahit-real/" ;;
    amd) src="$B/runs-amd/megahit-real/" ;;
    evaluate) src="$B/runs/megahit-real/evaluate/" ;;
    arm64-repeat) src="$B/runs/megahit-real-repeat/" ;;
    arm64-t1) src="$B/runs/megahit-real-t1/" ;;
    x86_64-t1) src="$B/runs-x86_64/megahit-real-t1/" ;;
    amd-t1) src="$B/runs-amd/megahit-real-t1/" ;;
  esac
  ex=(--exclude "*final.contigs.fa" --exclude "*samples.txt" --exclude "*/not_aligned/*")
  [ "$c" = evaluate ] || ex+=(--exclude "evaluate/*")
  aws s3 cp --only-show-errors --recursive "${ex[@]}" "$src" "$HERE/results/$c/"
done
for c in $CHIPS; do
  for f in "$HERE"/results/$c/*/checks.txt; do echo "== $f"; cat "$f"; done
done
