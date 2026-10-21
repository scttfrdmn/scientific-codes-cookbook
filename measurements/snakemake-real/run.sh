#!/usr/bin/env bash
# Drive measurements/snakemake-real from the submitting machine, as a reader would run the recipe.
# Snakemake runs here; every job runs on its own spawn instance of the chosen chip.
#
#   run.sh <arm64|x86_64|amd> <phase> <bucket> [workdir root]
#
#   probe   one file (the largest), same rules, its own prefix      -> sizing
#   r1      the cohort, cold                                          -> 11 stats + 1 cohort
#   noop    the same command again                                    -> nothing to do
#   touch1  truth/ERR188026_1.fastq.gz.tsv re-uploaded (same bytes)  -> that file's stats + cohort
#   touch2  cohort-truth.tsv re-uploaded (same bytes)                -> cohort only
#   direct  the same commands without Snakemake (direct.sh, one spawn task) -> results/<chip>/direct/
#   jobs4   the cold run again with --jobs 4, its own prefix         -> what #19 does (observation)
#   any other name: the same command again, for whatever the plan says (e.g. a lost output)
#
# Each phase writes, under results/<chip>/: <phase>.plan.txt (the dry run, before), <phase>.log
# (Snakemake's own output), <phase>.objects.tsv (every output object with its S3 mtime and ETag,
# after), <phase>.wall (start, end) -- and copies the workflow's small outputs.
# --jobs 1: the executor keys a job's spawn task_id on its rule name, so concurrent jobs of one
# rule share a completion record (snakemake-executor-plugin-spawn#19).
set -euo pipefail
CHIP=${1:?arm64|x86_64|amd}; PHASE=${2:?phase}; B=${3:?bucket}
ROOT=${4:-${HOME}/.xtmp/snakemake-real}
HERE="$(cd "$(dirname "$0")" && pwd)"
case $CHIP in
  arm64)  RUNS=runs;        FAM=c8g ;;
  x86_64) RUNS=runs-x86_64; FAM=c8i ;;
  amd)    RUNS=runs-amd;    FAM=c8a ;;
  *) echo "unknown chip $CHIP" >&2; exit 1 ;;
esac
IN="s3://$B/inputs/snakemake-real"
OUT="$HERE/results/$CHIP"; mkdir -p "$OUT"
if [ "$PHASE" = direct ]; then
  RC=0
  spawn task run --wait --spec "$(python3 "$HERE/mkspec.py" "$CHIP" "$B" "${NONCE:-$(date +%s)}")" > "$OUT/direct.spawn.txt" 2>&1 || RC=$?
  mkdir -p "$OUT/direct"
  aws s3 cp --recursive --only-show-errors "s3://$B/$RUNS/snakemake-real/direct/" "$OUT/direct/"
  REC=$(awk '/^Completion:/{print $2}' "$OUT/direct.spawn.txt")   # spawn's record: window and cost
  [ -n "$REC" ] && aws s3 cp --only-show-errors "$REC" "$OUT/direct/completion.json"
  echo "$CHIP direct exit $RC"; exit $RC
fi
EXTRA=(); JOBS=1
if [ "$PHASE" = probe ]; then
  PREFIX="s3://$B/$RUNS/snakemake-real/probe"; EXTRA=(only=ERR2935805_2.fastq.gz); WD="$ROOT/wd-$CHIP-probe"
elif [ "$PHASE" = jobs4 ]; then
  PREFIX="s3://$B/$RUNS/snakemake-real/jobs4"; JOBS=4; WD="$ROOT/wd-$CHIP-jobs4"
else
  PREFIX="s3://$B/$RUNS/snakemake-real/r1"; WD="$ROOT/wd-$CHIP"
fi
mkdir -p "$WD"
# -p: a rule script's mtime is one of Snakemake's rerun triggers, so a fresh copy of stats.sh
# re-runs every stats job ("Code has changed since last execution").
cp -p "$HERE/Snakefile" "$HERE/stats.sh" "$HERE/cohort.py" "$WD/"
cd "$WD"

touch_obj() {   # a new S3 mtime, the same bytes
  aws s3 cp --only-show-errors --metadata-directive REPLACE "$1" "$1"
  aws s3api head-object --bucket "$B" --key "${1#s3://$B/}" --query '[LastModified,ETag]' --output text
}
case $PHASE in
  touch1) touch_obj "$IN/truth/ERR188026_1.fastq.gz.tsv" | tee "$OUT/$PHASE.touched" ;;
  touch2) touch_obj "$IN/cohort-truth.tsv" | tee "$OUT/$PHASE.touched" ;;
esac

# Snakemake forwards the submitter's tmpdir to every job as a default resource, and a job cannot
# use a path that exists only on this machine. Its own temporaries here are a few KB.
export TMPDIR=/tmp
SM=(snakemake -s Snakefile --config bucket="$B" "${EXTRA[@]}"
    --executor spawn --shared-fs-usage none
    --spawn-region us-west-2 --spawn-ttl 60m --spawn-cost-limit 0.05 --spawn-instance-type "$FAM.large"
    --default-storage-provider s3 --default-storage-prefix "$PREFIX"
    --jobs $JOBS ${TARGETS:-})   # TARGETS: explicit targets, e.g. one stats file to re-create
"${SM[@]}" -n > "$OUT/$PHASE.plan.txt" 2>&1 || true
date +%s > "$OUT/$PHASE.wall"
set +e
"${SM[@]}" > "$OUT/$PHASE.log" 2>&1
RC=$?
set -e
date +%s >> "$OUT/$PHASE.wall"
echo "$RC" >> "$OUT/$PHASE.wall"

aws s3api list-objects-v2 --bucket "$B" --prefix "${PREFIX#s3://$B/}/" \
  --query 'Contents[].[Key,LastModified,ETag,Size]' --output text | sort > "$OUT/$PHASE.objects.tsv"
D="$OUT/outputs-$PHASE"; mkdir -p "$D"
aws s3 cp --recursive --only-show-errors --exclude 'snakemake-workflow-sources*' "$PREFIX/" "$D/"
echo "$CHIP $PHASE exit $RC  wall $(( $(sed -n 2p "$OUT/$PHASE.wall") - $(sed -n 1p "$OUT/$PHASE.wall") )) s"
exit $RC
