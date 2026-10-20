#!/usr/bin/env bash
# measurements/psi4-real: SAPT0 + CP DF-MP2 on DESRES's S66, checked against the published columns.
#   run.sh real  <workers> <threads> <mem MB per worker>
#   run.sh probe-import                            (one water dimer: does this image import and reproduce?)
#   run.sh probe                                   (sizing and tolerance probe: three dimers, several settings)
set +e
set -uo pipefail
MODE=${1:?real or probe}
# The env's root: /opt/conda in the aarchsci image, and in the psi4/psi4 image's micromamba base env.
P="${CONDA_PREFIX:-/opt/conda}"
export PATH=$P/bin:$PATH TMPDIR=/tmp HOME=/tmp
W=/tmp/out
mkdir -p $W /var/tmp/psi4scr
cd /tmp

echo "== the staged DESS66 zip must match the sha256 recorded when it was fetched =="
( cd /tmp/in && grep 'DESS66.zip' /tmp/in/dess66.sha256 | sha256sum -c - ) > $W/sources.check 2>&1
rc=$?
cat $W/sources.check
[ "$rc" = 0 ] || { echo "FAIL: DESS66.zip does not match dess66.sha256" | tee $W/check.txt; exit 1; }

{
  echo "uname_m	$(uname -m)"
  echo "nproc_all	$(nproc --all)"
  lscpu 2>/dev/null | grep -E '^(Model name|Thread\(s\) per core|Core\(s\) per socket|Socket\(s\)|CPU\(s\)):' | sed 's/:  */	/'
  grep MemTotal /proc/meminfo
  python3 -u - <<'PY' 2>&1
# Describes the run only; nothing here may fail it.
import ctypes, glob, json, os, sys
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
    else:
        print("openblas_corename\tnone in %s/lib; mkl: %s" % (sys.prefix, [os.path.basename(x) for x in glob.glob(os.path.join(sys.prefix, "lib", "libmkl_core*"))]))
except Exception as e:
    print("openblas_corename\terror: %s" % e)
try:
    for m in sorted(glob.glob(os.path.join(sys.prefix, "conda-meta", "*.json"))):
        b = os.path.basename(m)
        if b.startswith(("psi4-", "einsums-", "libblas-", "libxc-", "python-3", "numpy-", "mkl-", "libint-")):
            d = json.load(open(m))
            print("conda_pkg\t%s %s %s %s" % (d.get("name"), d.get("version"), d.get("build"), d.get("channel", d.get("url", ""))))
except Exception as e:
    print("conda_pkg\terror: %s" % e)
try:
    import psi4
    print("psi4_version\t%s" % str(getattr(psi4, "__version__", "unknown")))
    for fn in ("version", "git_version"):
        try:
            print("psi4_core_%s\t%s" % (fn, str(getattr(psi4.core, fn)())))
        except Exception as e:
            print("psi4_core_%s\tunavailable: %s" % (fn, e))
    print("psi4_file\t%s" % str(psi4.__file__))
except Exception as e:
    print("psi4_import\tFAILED: %s: %s" % (type(e).__name__, e))
PY
  ls -l $P/lib/libblas.so* $P/lib/liblapack.so* 2>/dev/null | sed 's/.* \([^ ]* -> .*\)/\1/'
} > $W/host.txt
cat $W/host.txt
grep -q '^psi4_version' $W/host.txt || { echo "FAIL: psi4 does not import" | tee $W/check.txt; exit 1; }

S=/tmp/psi4_s66.py
T0=$(date +%s)
if [ "$MODE" = real ]; then
  NW=${2:?workers}; TH=${3:?threads}; MEM=${4:?MB per worker}
  python3 -u $S run /tmp/in/DESS66.zip DESS66.csv $W "$NW" "$TH" "$MEM" all tight 2>&1 | tee $W/run.log
  CHK=${PIPESTATUS[0]}
elif [ "$MODE" = probe-import ]; then
  python3 -u $S run /tmp/in/DESS66.zip DESS66.csv $W "1" "2" "2500" 0 tight 2>&1 | tee $W/run.log
  CHK=${PIPESTATUS[0]}
else
  CHK=0
  for cfg in "p16 1 16 48000 pick tight" "p4 1 4 48000 pick tight" "p1 2 1 24000 pick2 tight" "loose 1 16 48000 pick loose"; do
    set -- $cfg
    echo "== probe $1: workers $2 threads $3 mem $4 select $5 conv $6 ==" | tee -a $W/run.log
    t=$(date +%s)
    python3 -u $S run /tmp/in/DESS66.zip DESS66.csv $W/$1 "$2" "$3" "$4" "$5" "$6" 2>&1 | tee -a $W/run.log
    echo "probe $1 wall $(( $(date +%s) - t )) s rc ${PIPESTATUS[0]}" | tee -a $W/run.log
  done
fi
T1=$(date +%s)
{
  echo "tool_wall_s	$((T1-T0))"
  CG=/sys/fs/cgroup
  [ -r $CG/memory.peak ] && awk '{printf "cgroup_peak_gib\t%.2f\n", $1/2^30}' $CG/memory.peak
  [ -r $CG/cpu.stat ] && awk -v w=$((T1-T0)) '/^usage_usec/{printf "avg_cores_whole_task\t%.2f\n", $2/(w*1e6)}' $CG/cpu.stat
  df -h /tmp /var/tmp | tail -2
} | tee $W/footprint.txt
[ "$CHK" = 0 ] && echo "ALL CHECKS PASSED" || { echo "FAIL: check rc=$CHK"; exit 1; }
