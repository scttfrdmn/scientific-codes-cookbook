#!/usr/bin/env bash
# measurements/neuroimaging-real, task 2 of 2: DIPY's tensor fits over the cohort, checked against
# dtifit (task 1's output, same chip), a planted tensor, a rotation invariant, fslmaths and AFNI.
#   dipy.sh <parallel jobs> [first N subjects; default all]
set +e
set -uo pipefail
P=${1:?parallel jobs}; N=${2:-0}
export TMPDIR=/tmp HOME=/tmp MPLBACKEND=Agg
# one subject per core: every tool single-threaded, so P is the only parallelism
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
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

echo "== host and libraries =="
{ echo "arch	$(uname -m)"
  lscpu 2>/dev/null | awk -F: '/^Model name|^Thread\(s\) per core|^Core\(s\) per socket|^Socket\(s\)/{gsub(/^ +/,"",$2); print $1 "\t" $2}'
  echo "afni	$(afni -ver 2>/dev/null | head -1)"
  python3 -u - <<'PY'
import ctypes, glob, sys
def ver(m):
    try:
        return str(getattr(__import__(m), "__version__", "unknown"))
    except Exception as e:
        return "unavailable (%s)" % type(e).__name__
for m in ("dipy", "nibabel", "numpy", "scipy"):
    print("%s\t%s" % (m, ver(m)))
try:
    lib = sorted(glob.glob(sys.prefix + "/lib/libopenblas*.so*"))[0]
    f = ctypes.CDLL(lib).openblas_get_corename
    f.restype = ctypes.c_char_p
    print("openblas_corename\t%s" % f().decode())
except Exception as e:
    print("openblas_corename\tunavailable (%s)" % type(e).__name__)
PY
} 2>&1 | tee /tmp/host.txt

echo "== dtifit's maps (task 1, this chip) =="
mkdir -p /tmp/fslout /tmp/res /tmp/maps /tmp/work
tar -xf /tmp/dtifit.tar -C /tmp/fslout || { echo "FAIL: extract dtifit.tar"; exit 1; }
if [ "$N" -gt 0 ]; then awk -v n=$N 'NR<=n' subjects.txt > ids.txt; else cp subjects.txt ids.txt; fi
NRUN=$(wc -l < ids.txt)

echo "== $NRUN subjects, $P at a time =="
T0=$(date +%s)
xargs -a ids.txt -P "$P" -n 1 python3 -u /tmp/subject.py 2> /tmp/subjects.err
T1=$(date +%s)
kill $SPID 2>/dev/null; wait $SPID 2>/dev/null
WALL=$((T1-T0))

python3 -u /tmp/subject.py --summary ids.txt $([ "$N" -gt 0 ] && echo 0 || echo 1) > /tmp/checks.txt 2>&1
RC=$?
tar -cf /tmp/dipy-maps.tar -C /tmp/maps .
{ echo "cohort_wall_s	$WALL"
  awk -F'\t' -v wall="$WALL" 'NR==1{cu0=$3} {if($2>mc)mc=$2; if($4>tk)tk=$4; cu=$3}
    END{printf "peak_rss_mib\t%.0f\ntmpfs_peak_mib\t%.0f\n", mc/1048576, tk/1024; if(wall>0) printf "avg_cores\t%.2f\n", (cu-cu0)/(wall*1e6)}' /tmp/samples.tsv
  cat /tmp/host.txt; cat /tmp/checks.txt; } > /tmp/summary.txt
cat /tmp/summary.txt
test "$RC" = 0 || { echo "FAIL: a cohort assertion failed"; exit 1; }
echo DONE
