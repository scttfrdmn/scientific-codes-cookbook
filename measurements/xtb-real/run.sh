#!/usr/bin/env bash
# measurements/xtb-real: GFN2-xTB over all of GMTKN55.   run.sh <workers> [limit]
set +e
set -uo pipefail
NW=${1:?workers}; LIMIT=${2:-0}
# The env's root: /opt/conda in the aarchsci image, $CONDA_PREFIX in the x86 twin.
P="${CONDA_PREFIX:-/opt/conda}"
export PATH=$P/bin:$PATH TMPDIR=/tmp HOME=/tmp LC_ALL=C
# one thread per xtb process: a thread per core under NW workers is oversubscription
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 OMP_STACKSIZE=1G
ulimit -s unlimited 2>/dev/null
W=/tmp/out
mkdir -p $W /tmp/g
cd /tmp

echo "== the staged GMTKN55 archive must be the pinned commit's bytes =="
( cd /tmp/in && sha256sum -c gmtkn55-v1.sha256 ) > $W/sources.check 2>&1
rc=$?
cat $W/sources.check
[ "$rc" = 0 ] || { echo "FAIL: the staged GMTKN55 archive does not match its sha256" | tee $W/check.txt; exit 1; }
tar -xzf /tmp/in/gmtkn55-v1.tar.gz -C /tmp/g --strip-components=1 || { echo "FAIL: untar" | tee $W/check.txt; exit 1; }

{
  echo "uname_m	$(uname -m)"
  echo "nproc_all	$(nproc --all)"
  lscpu 2>/dev/null | grep -E '^(Model name|Thread\(s\) per core|Core\(s\) per socket|Socket\(s\)|CPU\(s\)):' | sed 's/:  */	/'
  grep MemTotal /proc/meminfo
  xtb --version 2>&1 | grep -E 'version' | head -1 | sed 's/^ */xtb	/'
  tblite --version 2>&1 | head -1 | sed 's/^/tblite	/'
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
try:
    print("numpy_version\t%s" % str(getattr(__import__("numpy"), "__version__", "unknown")))
except Exception as e:
    print("numpy_version\tunimportable: %s" % e)
PY
  ls -l $P/lib/libblas.so* $P/lib/liblapack.so* 2>/dev/null | sed 's/.* \([^ ]* -> .*\)/\1/'
  echo "xtb_binary_arch	$(od -An -t u2 -j 18 -N 2 "$(command -v xtb)" | tr -d ' ') (e_machine; 183 = aarch64, 62 = x86-64)"
} > $W/host.txt
cat $W/host.txt

# tblite (the env's independent GFN2 implementation) as a cross-code check: one probe, reported only --
# its 0.6.0 CLI segfaults in this env on every structure tried, on both architectures (see README)
mkdir -p /tmp/tbp && cp /tmp/g/S22/02a/coord /tmp/tbp/ \
  && ( cd /tmp/tbp && tblite run --method gfn2 --acc 0.0001 --grad tblite.txt coord > out.txt 2>&1; \
       echo "tblite_run_rc	$?"; grep -iE 'total energy|segmentation' out.txt | head -2 ) > $W/tblite-probe.txt 2>&1
cat $W/tblite-probe.txt

T0=$(date +%s)
python3 -u /tmp/xtb_run.py /tmp/g $W "$NW" "$LIMIT" 2>&1 | tee $W/run.log
CHK=${PIPESTATUS[0]}
T1=$(date +%s)
{
  echo "tool_wall_s	$((T1-T0))"
  CG=/sys/fs/cgroup
  [ -r $CG/memory.peak ] && awk '{printf "cgroup_peak_gib\t%.2f\n", $1/2^30}' $CG/memory.peak
  [ -r $CG/cpu.stat ] && awk -v w=$((T1-T0)) '/^usage_usec/{printf "avg_cores_whole_task\t%.2f\n", $2/(w*1e6)}' $CG/cpu.stat
  df -h /tmp /var/tmp | tail -2
} | tee $W/footprint.txt
gzip -kf $W/xtb.jsonl
[ "$CHK" = 0 ] && echo "ALL CHECKS PASSED" || { echo "FAIL: check rc=$CHK"; exit 1; }
