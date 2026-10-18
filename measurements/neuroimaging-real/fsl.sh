#!/usr/bin/env bash
# measurements/neuroimaging-real, task 1 of 2: FSL's dtifit over the cohort -- the independent code
# DIPY is checked against in task 2. dtifit's default is OLS on the log signal; task 2 matches it.
#   fsl.sh <parallel jobs> [first N subjects; default all]     (or: fsl.sh one <id>, per subject)
set +e
set -uo pipefail

if [ "$1" = one ]; then
  id=$2; d=/tmp/dwi/sub-${id}_ses-BAS1_dwi; o=/tmp/fslout/$id; W=/tmp/work/$id
  fail() { printf '%s\tFAIL\t%s\n' "$id" "$1" > /tmp/res/$id.tsv; echo "FAIL $id: $1" >&2; exit 1; }
  mkdir -p "$o" "$W"; cd "$W"
  t0=$(date +%s.%N)
  # the voxels where every volume is positive, i.e. where the log signal exists
  fslmaths "$d.nii.gz" -Tmin -bin "$o/pos" -odt char || fail pos
  t1=$(date +%s.%N)
  dtifit -k "$d.nii.gz" -o dti -m "$o/pos" -r "$d.bvec" -b "$d.bval" > dtifit.log 2>&1 || { cat dtifit.log >&2; fail dtifit; }
  t2=$(date +%s.%N)
  for m in FA MD L1 L2 L3; do mv "dti_$m.nii.gz" "$o/" || fail "no dti_$m"; done
  nvol=$(fslval "$d.nii.gz" dim4 | tr -d ' ')
  npos=$(fslstats "$o/pos" -V | awk '{print $1}')
  cd /tmp; rm -rf "$W"
  awk -v id="$id" -v n="$nvol" -v p="$npos" -v a="$t0" -v b="$t1" -v c="$t2" \
    'BEGIN{printf "%s\tOK\t%s\t%s\t%.2f\t%.2f\n", id, n, p, b-a, c-b}' > /tmp/res/$id.tsv
  exit 0
fi

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

echo "== the staged scans must be the pinned bytes =="
(cd /tmp/dwi && sha256sum -c --quiet /tmp/dwi.sha256) || { echo "FAIL: a DWI file does not match dwi.sha256"; exit 1; }
echo "  $(wc -l < /tmp/dwi.sha256) files match"

SUB=linux-$( [ "$(uname -m)" = aarch64 ] && echo aarch64 || echo 64 )
echo "== FSL (+fdt) from the staged packages, offline ($SUB) =="
awk -v d=/tmp/pkgs/$SUB -v x=/tmp/xpkgs/$SUB '/^https?:/{n=split($0,a,"/"); split(a[n],b,"#");
  f = d "/" b[1]; if ((getline t < f) < 0) f = x "/" b[1]; close(d "/" b[1]); print "file://" f "#" b[2]; next} {print}' \
  "/tmp/locks/fsl-dtifit-$SUB.lock" > local.lock
micromamba create -y -q -p /tmp/fsl --offline -f local.lock > /tmp/install.log 2>&1 \
  || { echo "FAIL: FSL did not install from the staged, lock-verified packages"; tail -20 /tmp/install.log; exit 1; }
export FSLDIR=/tmp/fsl PATH=/tmp/fsl/bin:$PATH FSLOUTPUTTYPE=NIFTI_GZ
. $FSLDIR/etc/fslconf/fsl.sh
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
for t in dtifit fslmaths fslval fslstats; do command -v $t >/dev/null || { echo "FAIL: $t not installed"; exit 1; }; done
{ echo "arch	$(uname -m)"
  lscpu 2>/dev/null | awk -F: '/^Model name|^Thread\(s\) per core|^Core\(s\) per socket|^Socket\(s\)/{gsub(/^ +/,"",$2); print $1 "\t" $2}'
  grep -o 'fsl-fdt-[^/#]*' "/tmp/locks/fsl-dtifit-$SUB.lock" | sed 's/^/fdt\t/'
  python3 -c "
import ctypes, glob
try:
    f = ctypes.CDLL(sorted(glob.glob('/tmp/fsl/lib/libopenblas*.so*'))[0]).openblas_get_corename
    f.restype = ctypes.c_char_p; print('openblas_corename\t' + f().decode())
except Exception as e:
    print('openblas_corename\tunavailable (%s)' % type(e).__name__)" 2>&1
} | tee /tmp/host.txt

mkdir -p /tmp/fslout /tmp/res /tmp/work
if [ "$N" -gt 0 ]; then awk -v n=$N 'NR<=n' subjects.txt > ids.txt; else cp subjects.txt ids.txt; fi
NRUN=$(wc -l < ids.txt)
echo "== $NRUN subjects, $P at a time =="
T0=$(date +%s)
xargs -a ids.txt -P "$P" -n 1 bash /tmp/fsl.sh one 2> /tmp/subjects.err
T1=$(date +%s)
kill $SPID 2>/dev/null; wait $SPID 2>/dev/null
WALL=$((T1-T0))
{ printf 'id\tstatus\tnvol\tn_pos\tt_pos\tt_dtifit\n'
  while read -r id; do cat "/tmp/res/$id.tsv" 2>/dev/null || printf '%s\tFAIL\tno result\n' "$id"; done < ids.txt; } > /tmp/results.tsv
tar -cf /tmp/dtifit.tar -C /tmp/fslout .
{ echo "cohort_wall_s	$WALL"
  awk -F'\t' -v wall="$WALL" 'NR==1{cu0=$3} {if($2>mc)mc=$2; if($4>tk)tk=$4; cu=$3}
    END{printf "peak_rss_mib\t%.0f\ntmpfs_peak_mib\t%.0f\n", mc/1048576, tk/1024; if(wall>0) printf "avg_cores\t%.2f\n", (cu-cu0)/(wall*1e6)}' /tmp/samples.tsv
  cat /tmp/host.txt
  awk -F'\t' -v n=$NRUN 'NR>1{k++; if($2=="OK"){ok++; tp+=$5; td+=$6; if($3!=137){print "FAIL: " $1 " has " $3 " volumes"; bad++}} else {print "FAIL: " $1 " " $3; bad++}}
    END{printf "subjects\t%d run, %d OK\nsum_t_pos\t%.1f\nsum_t_dtifit\t%.1f\n", k, ok, tp, td; if(k!=n||ok!=n) bad++; exit bad>0}' /tmp/results.tsv
  echo "rc	$?"; } > /tmp/summary.txt
cat /tmp/summary.txt
grep -q '^rc	0$' /tmp/summary.txt || { echo "FAIL: dtifit did not complete on every subject"; exit 1; }
echo DONE
