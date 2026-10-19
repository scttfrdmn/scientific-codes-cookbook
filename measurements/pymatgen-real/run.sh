#!/usr/bin/env bash
# measurements/pymatgen-real: pymatgen over the whole Materials Project 2025-09-25 release --
# phase diagrams, symmetry and structure matching against MP's published values (real.py).
#   run.sh <worker processes>
set +e
set -uo pipefail
NP=${1:?workers}
export PATH=/opt/conda/bin:$PATH HOME=/tmp TMPDIR=/tmp OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
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
    for lib in sorted(glob.glob(os.path.join(sys.prefix, "lib", "libopenblas*.so*"))):
        try:
            f = ctypes.CDLL(lib).openblas_get_corename
            f.restype = ctypes.c_char_p
            print("openblas_corename\t%s (%s)" % (f().decode(), os.path.basename(lib)))
            break
        except Exception as e:
            print("openblas_corename\tunreadable: %s" % e)
    else:
        print("openblas_corename\tnone in %s/lib" % sys.prefix)
except Exception as e:
    print("openblas_corename\terror: %s" % e)
for m in ("pymatgen.core", "spglib", "numpy", "scipy"):
    try:
        print("%s_version\t%s" % (m, str(getattr(__import__(m, fromlist=["x"]), "__version__", "unknown"))))
    except Exception as e:
        print("%s_version\tunimportable: %s" % (m, e))
PY
} > $W/host.txt
cat $W/host.txt

echo "== the staged release must match its pins =="
( cd /tmp/mp && find summary thermo -type f | LC_ALL=C sort | xargs -d '\n' sha256sum ) > $W/staged.sha256
echo "staged files	$(wc -l < $W/staged.sha256)	$(du -sb /tmp/mp | cut -f1) bytes"
if [ -s /tmp/inputs.sha256 ]; then
  cmp -s /tmp/inputs.sha256 $W/staged.sha256 || { echo "FAIL: staged release does not match inputs.sha256"; diff /tmp/inputs.sha256 $W/staged.sha256 | head; exit 1; }
  echo "all $(wc -l < /tmp/inputs.sha256) files match inputs.sha256"
else
  echo "UNPINNED: no inputs.sha256 staged; out/staged.sha256 is the manifest to pin"
fi

T0=$(date +%s)
python3 -u /tmp/real.py /tmp/mp $W "$NP" 2>&1 | tee $W/real.log
CHK=${PIPESTATUS[0]}
T1=$(date +%s)
{
  echo "tool_wall_s	$((T1-T0))"
  CG=/sys/fs/cgroup
  [ -r $CG/memory.peak ] && awk '{printf "cgroup_peak_gib\t%.2f\n", $1/2^30}' $CG/memory.peak
  [ -r $CG/cpu.stat ] && awk -v w=$((T1-T0)) '/^usage_usec/{printf "avg_cores_whole_task\t%.2f\n", $2/(w*1e6)}' $CG/cpu.stat
  df -h /tmp | tail -1
} | tee $W/footprint.txt
tar -czf /tmp/pymatgen-real-out.tar.gz -C /tmp out
[ "$CHK" = 0 ] && echo "ALL CHECKS PASSED" || { echo "FAIL: check rc=$CHK"; exit 1; }
