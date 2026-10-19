#!/usr/bin/env bash
# measurements/yt-real: yt over every output of the Enzo_64 cosmology run (real.py).
#   run.sh <worker processes> [output names...]
set +e
set -uo pipefail
NP=${1:?workers}; shift
export HOME=/tmp TMPDIR=/tmp MPLCONFIGDIR=/tmp/mpl YT_CONFIG_DIR=/tmp/yt
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
W=/tmp/out
mkdir -p $W
cd /tmp
{
  echo "uname_m	$(uname -m)"
  echo "nproc_all	$(nproc --all)"
  lscpu 2>/dev/null | grep -E '^(Model name|Thread\(s\) per core|Core\(s\) per socket|Socket\(s\)|CPU\(s\)):' | sed 's/:  */	/'
  python3 -u - <<'PY' 2>&1
import ctypes, glob, os, sys
# Describes the run only; nothing here may fail it.
try:
    libs = sorted(glob.glob(os.path.join(sys.prefix, "lib", "libopenblas*.so*")))
    for lib in libs:
        try:
            f = ctypes.CDLL(lib).openblas_get_corename
            f.restype = ctypes.c_char_p
            print("openblas_corename\t%s (%s)" % (f().decode(), os.path.basename(lib)))
            break
        except Exception as e:
            print("openblas_corename\tunreadable: %s" % e)
    if not libs:
        print("openblas_corename\tnone in %s/lib" % sys.prefix)
except Exception as e:
    print("openblas_corename\terror: %s" % e)
for m in ("yt", "numpy", "h5py", "unyt"):
    try:
        print("%s_version\t%s" % (m, str(getattr(__import__(m), "__version__", "unknown"))))
    except Exception as e:
        print("%s_version\tunimportable: %s" % (m, e))
PY
} > $W/host.txt
cat $W/host.txt

echo "== the staged run must match its pins =="
( cd /tmp/data && find Enzo_64 -type f | LC_ALL=C sort | xargs -d '\n' sha256sum ) > $W/staged.sha256
echo "staged files	$(wc -l < $W/staged.sha256)	$(du -sb /tmp/data | cut -f1) bytes"
cmp -s /tmp/files.sha256 $W/staged.sha256 || { echo "FAIL: staged Enzo_64 does not match files.sha256"; diff /tmp/files.sha256 $W/staged.sha256 | head; exit 1; }
echo "all $(wc -l < /tmp/files.sha256) files match files.sha256"

T0=$(date +%s)
python3 -u /tmp/real.py /tmp/data/Enzo_64 $W "$NP" "$@" 2>&1 | tee $W/real.log
CHK=${PIPESTATUS[0]}
T1=$(date +%s)
{
  echo "tool_wall_s	$((T1-T0))"
  CG=/sys/fs/cgroup
  [ -r $CG/memory.peak ] && awk '{printf "cgroup_peak_gib\t%.2f\n", $1/2^30}' $CG/memory.peak
  [ -r $CG/cpu.stat ] && awk -v w=$((T1-T0)) '/^usage_usec/{printf "avg_cores\t%.2f\n", $2/(w*1e6)}' $CG/cpu.stat
  df -h /tmp | tail -1
} | tee $W/footprint.txt
[ "$CHK" = 0 ] && echo "ALL CHECKS PASSED" || { echo "FAIL: check rc=$CHK"; exit 1; }
