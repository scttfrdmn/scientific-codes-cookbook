#!/usr/bin/env bash
# measurements/fsl-real: the whole NFBS cohort (125 T1w scans with manually corrected brain masks)
# through BET, a planted-motion FLIRT and FLIRT+FNIRT to MNI152, one subject per core.
#   run.sh <parallel jobs> [first N subjects; default all]
set +e
set -uo pipefail
P=${1:?parallel jobs}; N=${2:-0}
export TMPDIR=/tmp HOME=/tmp MAMBA_ROOT_PREFIX=/tmp/mamba
cd /tmp

CG=/sys/fs/cgroup
sampler() { : > /tmp/samples.tsv
  while : ; do
    printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$(cat $CG/memory.current 2>/dev/null || echo 0)" \
      "$(awk '/^usage_usec/{print $2; exit}' $CG/cpu.stat 2>/dev/null || echo 0)" \
      "$(df -k /tmp 2>/dev/null | awk 'NR==2{print $3}')" >> /tmp/samples.tsv
    sleep 2
  done; }
sampler & SPID=$!

echo "== the NFBS archive must be the pinned bytes =="
sha256sum -c nfbs.sha256 || { echo "FAIL: NFBS_Dataset.tar.gz does not match its pin"; exit 1; }

SUB=linux-$( [ "$(uname -m)" = aarch64 ] && echo aarch64 || echo 64 )
echo "== FSL (+fnirt, fugue) from the staged packages, offline ($SUB) =="
awk -v d=/tmp/pkgs/$SUB -v x=/tmp/xpkgs/$SUB '/^https?:/{n=split($0,a,"/"); split(a[n],b,"#");
  f = d "/" b[1]; if ((getline t < f) < 0) f = x "/" b[1]; close(d "/" b[1]); print "file://" f "#" b[2]; next} {print}' \
  "/tmp/locks/fsl-real-$SUB.lock" > local.lock
micromamba create -y -q -p /tmp/fsl --offline -f local.lock > /tmp/install.log 2>&1 \
  || { echo "FAIL: FSL did not install from the staged, lock-verified packages"; tail -20 /tmp/install.log; exit 1; }
export FSLDIR=/tmp/fsl PATH=/tmp/fsl/bin:$PATH FSLOUTPUTTYPE=NIFTI_GZ
. $FSLDIR/etc/fslconf/fsl.sh
# one subject per core: keep each tool single-threaded so P is the only parallelism
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
for t in bet flirt fnirt applywarp fslreorient2std convert_xfm; do command -v $t >/dev/null || { echo "FAIL: $t not installed"; exit 1; }; done

echo "== extract =="
mkdir -p /tmp/nfbs /tmp/res /tmp/masks /tmp/work
tar -xzf NFBS_Dataset.tar.gz -C /tmp/nfbs || { echo "FAIL: extract"; exit 1; }
ls /tmp/nfbs/NFBS_Dataset | sort > all.txt
NALL=$(wc -l < all.txt); echo "subjects_in_archive	$NALL"
if [ "$N" -gt 0 ]; then awk -v n=$N 'NR<=n' all.txt > ids.txt; else cp all.txt ids.txt; fi
NRUN=$(wc -l < ids.txt)

echo "== $NRUN subjects, $P at a time =="
T0=$(date +%s)
xargs -a ids.txt -P "$P" -n 1 bash /tmp/subject.sh 2> /tmp/subjects.err
T1=$(date +%s)
kill $SPID 2>/dev/null; wait $SPID 2>/dev/null
WALL=$((T1-T0))
echo "cohort_wall_s	$WALL"
awk -F'\t' -v wall="$WALL" 'NR==1{cu0=$3} {if($2>mc)mc=$2; if($4>tk)tk=$4; cu=$3}
  END{printf "peak_rss_mib\t%.0f\ntmpfs_peak_mib\t%.0f\n", mc/1048576, tk/1024; if(wall>0) printf "avg_cores\t%.2f\n", (cu-cu0)/(wall*1e6)}' /tmp/samples.tsv

{ printf 'id\tstatus\tbet_dice\tbet_contain\tbet_vox\tref_vox\tbet_sha16\tvox_mm\tplanted_mm\tresid_mm\tdice_flirt\tdice_fnirt\tt_bet\tt_flirt6\tt_flirt12\tt_fnirt\tbet_raw_dice\n'
  while read -r id; do cat "/tmp/res/$id.tsv" 2>/dev/null || printf '%s\tFAIL\tno result\n' "$id"; done < ids.txt; } > /tmp/results.tsv
tar -cf /tmp/bet-masks.tar -C /tmp/masks .

awk -F'\t' -v n=$NRUN -v nall=$NALL -v full=$([ "$N" -gt 0 ] && echo 0 || echo 1) '
NR==1 { next }
{ k++ }
$2 != "OK" { bad++; print "FAIL: " $1 " did not complete (" $3 ")"; next }
{ ok++
  bd[ok]=$3; bc[ok]=$4; br[ok]=$17; pm[ok]=$9; rm[ok]=$10; da[ok]=$11; dn[ok]=$12
  tb+=$13; tf6+=$14; tf12+=$15; tn+=$16
  # a gross-failure guard, not a reference: in the 8-subject probe the worst BET was Dice 0.880
  if (!($3 >= 0.80))   { print "FAIL: " $1 " BET Dice " $3 " against the curated mask"; bad++ }
  if (!($9 > 10))      { print "FAIL: " $1 " planted motion only " $9 " mm"; bad++ }
  if (!($10 < $8/4))   { print "FAIL: " $1 " FLIRT left " $10 " mm of the planted motion (bound " $8/4 " mm, a quarter voxel)"; bad++ }
  if (!($12 > $11))    { print "FAIL: " $1 " FNIRT Dice " $12 " does not improve on its own affine start " $11; bad++ }
}
function srt(a, m,   i, j, t) { for (i = 2; i <= m; i++) { t = a[i]; for (j = i - 1; j >= 1 && a[j] > t; j--) a[j+1] = a[j]; a[j+1] = t } }
function q(a, m, p) { return a[int((m - 1) * p + 1.5)] }
function line(name, a) { srt(a, ok); printf "%-14s min %.4f  p05 %.4f  median %.4f  p95 %.4f  max %.4f\n", name, a[1], q(a,ok,.05), q(a,ok,.5), q(a,ok,.95), a[ok] }
END {
  printf "subjects       %d run, %d OK (archive holds %d)\n", k, ok, nall
  if (full && k != nall) { print "FAIL: ran " k " of the archive'"'"'s " nall; bad++ }
  if (ok > 0) {
    line("bet_raw_dice", br); line("bet_dice", bd); line("bet_contain", bc); line("planted_mm", pm); line("resid_mm", rm)
    line("dice_flirt", da); line("dice_fnirt", dn)
    printf "mean_s         bet %.1f  flirt6 %.1f  flirt12 %.1f  fnirt %.1f\n", tb/ok, tf6/ok, tf12/ok, tn/ok
  }
  if (bad) { print "FSL-REAL FAILED (" bad ")"; exit 1 }
  print "FSL-REAL OK"
}' /tmp/results.tsv | tee /tmp/summary.txt
RC=${PIPESTATUS[0]}
{ echo "cohort_wall_s	$WALL"; echo "parallel	$P"; nproc | awk '{print "nproc\t"$1}'; uname -m | awk '{print "arch\t"$1}'
  grep -m1 "CPU part" /proc/cpuinfo | awk '{print "cpu_part\t"$4}'; grep -m1 "model name" /proc/cpuinfo | cut -d: -f2 | awk '{$1=$1; print "cpu_model\t"$0}'
  micromamba list -p /tmp/fsl 2>/dev/null | awk '$1 ~ /^fsl-(bet2|flirt|fnirt|fugue|avwutils)$/ {printf "%s\t%s\n", $1, $2}'; } >> /tmp/summary.txt
test "$RC" = 0 || { echo "FAIL: see summary"; tail -30 /tmp/subjects.err; exit 1; }
echo DONE
