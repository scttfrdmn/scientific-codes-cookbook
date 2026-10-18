#!/usr/bin/env bash
# measurements/ants-real: the whole NFBS cohort (125 T1w scans with manually corrected brain masks)
# through the recipe's identities and antsRegistrationSyNQuick.sh to MNI152 2 mm, one subject per vCPU.
#   run.sh <parallel jobs> [first N subjects; default all]
set +e
set -uo pipefail
P=${1:?parallel jobs}; N=${2:-0}
export TMPDIR=/tmp HOME=/tmp
# one subject per vCPU: ITK single-threaded, so P is the only parallelism
export ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
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

echo "== the NFBS archive and the MNI152 template must be the pinned bytes =="
sha256sum -c nfbs.sha256 || { echo "FAIL: NFBS_Dataset.tar.gz does not match its pin"; exit 1; }
sha256sum -c mni.sha256 || { echo "FAIL: the MNI152 template does not match its pin"; exit 1; }
antsRegistration --version 2>&1 | head -1 | sed 's/^/binary\t/'
for t in antsRegistration antsRegistrationSyNQuick.sh antsApplyTransforms ImageMath ThresholdImage PrintHeader \
         ConvertTransformFile CreateJacobianDeterminantImage ImageIntensityStatistics; do
  command -v $t >/dev/null || { echo "FAIL: $t is not in the image"; exit 1; }; done
echo "linked BLAS: $(ldd /opt/ants/bin/antsRegistration 2>/dev/null | grep -ci -E 'blas|lapack') libraries"

echo "== extract the curated brains and masks =="
mkdir -p /tmp/nfbs /tmp/res /tmp/work /tmp/logs
tar -xzf NFBS_Dataset.tar.gz -C /tmp/nfbs --wildcards '*_T1w_brain.nii.gz' '*_T1w_brainmask.nii.gz' || { echo "FAIL: extract"; exit 1; }
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
  END{printf "peak_rss_mib\t%.0f\ntmpfs_peak_mib\t%.0f\n", mc/1048576, tk/1024; if(wall>0) printf "avg_cores\t%.2f\n", (cu-cu0)/(wall*1e6)}' /tmp/samples.tsv | tee /tmp/usage.txt

{ printf 'id\tstatus\tvox_mm\tid_absdiff\tplanted_mm\tresid_mm\tself_dev\tdice_aff\tdice_syn\tr_aff\tr_syn\tjac_nonpos\tjac_min_all\tjac_min_brain\tjac_max_brain\tic_mean\tic_p95\tic_max\twarp_mean\twarp_max\tt_rigid\tt_syn\tt_post\taff_sha16\twarp_sha16\trigid_sha16\taff_params\tid_rel\n'
  while read -r id; do cat "/tmp/res/$id.tsv" 2>/dev/null || printf '%s\tFAIL\tno result\n' "$id"; done < ids.txt; } > /tmp/results.tsv
tar -cf /tmp/logs.tar -C /tmp/logs .

awk -F'\t' -v nall=$NALL -v full=$([ "$N" -gt 0 ] && echo 0 || echo 1) '
function bad_(m) { print "FAIL: " $1 " " m; bad++ }
function num(v) { return v != "" && v == v + 0 }
NR==1 { next }
{ k++ }
$2 != "OK" { bad++; print "FAIL: " $1 " did not complete (" $3 ")"; next }
{ ok++
  ia[ok]=$28; pm[ok]=$5; rm[ok]=$6; sd[ok]=$7; da[ok]=$8; ds[ok]=$9; ra[ok]=$10; rs[ok]=$11
  jm[ok]=$14; icm[ok]=$16; icp[ok]=$17; icx[ok]=$18; wm[ok]=$19; trg+=$21; tsy+=$22; tpo+=$23
  # Not bit-exact on real headers (the probe: ~3e-8 summed over the volume). The claim is that the
  # identity adds nothing beyond float32 rounding: sum|out - in| / sum|in| below one float32 ulp, 2^-24.
  # A real resampling error -- a 0.01-voxel offset -- is of order 1e-3 on a scan.
  if (!(num($28) && $28 < 5.96e-8))       bad_("identity transform changed the scan: sum|out - in| / sum|in| = " $28)
  if (!(num($5) && $5 > 10))              bad_("planted motion only " $5 " mm")
  if (!(num($6) && $6 < $3 / 4))          bad_("rigid registration left " $6 " mm of the planted motion (bound a quarter voxel)")
  if (!(num($7) && $7 < 1e-6))            bad_("self-registration is not the identity: max|T - I| = " $7)
  if (!(num($9) && num($8) && $9 > $8))   bad_("SyN Dice " $9 " does not improve on its own affine stage " $8)
  if (!(num($11) && num($10) && $11 > $10)) bad_("SyN does not raise the correlation to MNI152: " $10 " -> " $11)
  if (!(num($12) && $12 + 0 == 0))        bad_("the SyN warp folds: " $12 " voxels with det J <= 0")
  # A wrong or missing inverse leaves the warp itself (mean ~4 mm in the brain); 0.1 mm is 1/20 of a template voxel
  if (!(num($16) && $16 < 0.1))           bad_("inverse consistency: mean |W^-1(W(x)) - x| " $16 " mm in the brain")
}
function srt(a, m,   i, j, t) { for (i = 2; i <= m; i++) { t = a[i]; for (j = i - 1; j >= 1 && a[j] > t; j--) a[j+1] = a[j]; a[j+1] = t } }
function q(a, m, p) { return a[int((m - 1) * p + 1.5)] }
function line(name, a) { srt(a, ok); printf "%-14s min %.6g  p05 %.6g  median %.6g  p95 %.6g  max %.6g\n", name, a[1], q(a,ok,.05), q(a,ok,.5), q(a,ok,.95), a[ok] }
END {
  printf "subjects       %d run, %d OK (archive holds %d)\n", k, ok, nall
  if (full && k != nall) { print "FAIL: ran " k " of the archive'"'"'s " nall; bad++ }
  if (ok > 0) {
    line("id_rel", ia); line("planted_mm", pm); line("resid_mm", rm); line("self_dev", sd)
    line("dice_aff", da); line("dice_syn", ds); line("r_aff", ra); line("r_syn", rs)
    line("jac_min_brain", jm); line("ic_mean_mm", icm); line("ic_p95_mm", icp); line("ic_max_mm", icx); line("warp_mean_mm", wm)
    printf "mean_s         rigid %.1f  syn %.1f  post %.1f\n", trg/ok, tsy/ok, tpo/ok
  }
  if (bad) { print "ANTS-REAL FAILED (" bad ")"; exit 1 }
  print "ANTS-REAL OK"
}' /tmp/results.tsv | tee /tmp/summary.txt
RC=${PIPESTATUS[0]}
{ cat /tmp/usage.txt; echo "cohort_wall_s	$WALL"; echo "parallel	$P"; uname -m | awk '{print "arch\t"$1}'
  grep -m1 "model name" /proc/cpuinfo | cut -d: -f2 | awk '{$1=$1; print "cpu_model\t"$0}'
  lscpu 2>/dev/null | awk -F: '/^Thread\(s\) per core|^Core\(s\) per socket|^Socket\(s\)|^CPU\(s\)/{gsub(/^ +/,"",$2); print $1 "\t" $2}'
  grep -m1 -o -w -E 'avx512f' /proc/cpuinfo | awk '{print "avx512f\tyes"}'
  antsRegistration --version 2>&1 | head -1 | sed 's/^/ants\t/'; } >> /tmp/summary.txt
test "$RC" = 0 || { echo "FAIL: see summary"; tail -30 /tmp/subjects.err; exit 1; }
echo DONE
