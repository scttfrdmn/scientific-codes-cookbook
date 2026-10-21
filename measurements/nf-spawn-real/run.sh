#!/usr/bin/env bash
# nf-spawn-real, the ENGINE run: recipes/nf-spawn/main.nf, unchanged, through nf-spawn, three times
# from one launch directory per chip:
#   1  cold: five processes, five instances
#   2  -resume, nothing changed: every process must come from the cache, no instance launched
#   3  -resume with NF_REAL_EDIT=1 (OBSERVE_RF's image named by tag+digest, same bytes): measured,
#      everything stays cached -- the image is not in nf-spawn's cache key (real.config)
#   4  -resume with OBSERVE_RF's cache off: exactly OBSERVE_RF re-runs, on cached tree outputs
#      that run 1's instances wrote, and must reproduce run 1's bytes
#
#   bash measurements/nf-spawn-real/run.sh <arm64|x86_64|amd> <bucket> <nonce> <1|2|3|4>
#
# Nextflow runs here, on the machine you launch from; every process runs on its own instance.
set -euo pipefail
CHIP=$1 B=$2 N=$3 RUN=$4
HERE="$(cd "$(dirname "$0")" && pwd)"
MAIN="$HERE/../../recipes/nf-spawn/main.nf"
case $CHIP in
  arm64)  FAM=c8g PROFILE=arm64 RUNS=runs ;;
  x86_64) FAM=c8i PROFILE=x86   RUNS=runs-x86_64 ;;
  amd)    FAM=c8a PROFILE=x86   RUNS=runs-amd ;;
  *) echo "chip: arm64 | x86_64 | amd" >&2; exit 2 ;;
esac
export COOKBOOK_BUCKET=$B AWS_REGION=${AWS_REGION:-us-west-2}
export NF_REAL_FAM=$FAM NF_REAL_RUN=$CHIP-$N NF_REAL_PUB="s3://$B/$RUNS/nf-spawn-real/$N/engine-run$RUN"
export JAVA_HOME=${JAVA_HOME:?set JAVA_HOME to a JDK 17+}
L="${NF_REAL_LAUNCH:-$HOME/.xtmp/nf-spawn-real}/launch-$CHIP-$N"
mkdir -p "$L"
cd "$L"
args=(run "$MAIN" -c "$HERE/real.config" -profile "$PROFILE"
      --seqs "s3://$B/inputs/mafft-real/pf01255.25_unaligned.fa" --expect_seqs 919 --expect_residues 207712
      -with-trace "trace-$RUN.txt")
case $RUN in
  1) ;;
  2) args+=(-resume) ;;
  3) args+=(-resume); export NF_REAL_EDIT=1 ;;
  4) args+=(-resume); export NF_REAL_EDIT=1 NF_REAL_NOCACHE=1 ;;
  *) echo "run: 1 | 2 | 3 | 4" >&2; exit 2 ;;
esac
s=$(date +%s)
set +e
nextflow -log "nextflow-$RUN.log" "${args[@]}" 2>&1 | tee "console-$RUN.txt"
rc=${PIPESTATUS[0]}
set -e
e=$(date +%s)
printf 'chip %s\nrun %s\nrc %s\nstart %s\nend %s\nwall_s %s\n' "$CHIP" "$RUN" "$rc" "$s" "$e" "$((e-s))" | tee "timing-$RUN.txt"
for f in "trace-$RUN.txt" "nextflow-$RUN.log" "console-$RUN.txt" "timing-$RUN.txt"; do
  aws s3 cp --only-show-errors "$f" "s3://$B/$RUNS/nf-spawn-real/$N/driver/$f"
done
exit "$rc"
