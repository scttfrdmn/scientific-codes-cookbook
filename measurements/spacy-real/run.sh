#!/usr/bin/env bash
# measurements/spacy-real: en_core_web_sm on UD English-EWT r2.18 (ewt.py), then over a block of PMC
# Open Access full text (real.py).   run.sh <worker processes> [first N shards]
set +e
set -uo pipefail
NP=${1:?workers}
NS=${2:-}
export PATH=/opt/conda/bin:$PATH TMPDIR=/tmp HOME=/tmp
# one thread per worker process: thinc's blis and numpy's OpenBLAS must not start a thread per core each
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 BLIS_NUM_THREADS=1
W=/tmp/out
mkdir -p $W
cd /tmp
{
  echo "uname_m	$(uname -m)"
  echo "nproc_all	$(nproc --all)"
  lscpu 2>/dev/null | grep -E '^(Model name|Thread\(s\) per core|Core\(s\) per socket|Socket\(s\)|CPU\(s\)):' | sed 's/:  */	/'
  grep MemTotal /proc/meminfo
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
for m in ("spacy", "thinc", "numpy", "blis", "en_core_web_sm"):
    try:
        print("%s_version\t%s" % (m, str(getattr(__import__(m), "__version__", "unknown"))))
    except Exception as e:
        print("%s_version\tunimportable: %s" % (m, e))
PY
} > $W/host.txt
cat $W/host.txt

echo "== the staged inputs must match their pins =="
( cd /tmp/pmc && sha256sum -c /tmp/shards.sha256 > /dev/null ) || { echo "FAIL: a PMC shard does not match shards.sha256"; exit 1; }
( cd /tmp/ewt && sha256sum -c /tmp/ewt.sha256 ) || { echo "FAIL: an EWT file does not match ewt.sha256"; exit 1; }
echo "all $(wc -l < /tmp/shards.sha256) shards and $(wc -l < /tmp/ewt.sha256) EWT files match their pins"

T0=$(date +%s)
python3 -u /tmp/ewt.py /tmp/ewt $W 2>&1 | tee $W/ewt.log
EWT=${PIPESTATUS[0]}
T1=$(date +%s)
python3 -u /tmp/real.py /tmp/pmc $W "$NP" $NS 2>&1 | tee $W/real.log
CHK=${PIPESTATUS[0]}
T2=$(date +%s)
[ -f $W/articles.jsonl ] && gzip -9 -n $W/articles.jsonl
gzip -9 -n $W/*.conllu
{
  echo "ewt_wall_s	$((T1-T0))"
  echo "pmc_wall_s	$((T2-T1))"
  echo "tool_wall_s	$((T2-T0))"
  CG=/sys/fs/cgroup
  [ -r $CG/memory.peak ] && awk '{printf "cgroup_peak_gib\t%.2f\n", $1/2^30}' $CG/memory.peak
  [ -r $CG/cpu.stat ] && awk -v w=$((T2-T0)) '/^usage_usec/{printf "avg_cores_whole_task\t%.2f\n", $2/(w*1e6)}' $CG/cpu.stat
  df -h /tmp /var/tmp | tail -2
} | tee $W/footprint.txt
[ "$EWT" = 0 ] || echo "FAIL: EWT rc=$EWT"
[ "$CHK" = 0 ] || echo "FAIL: PMC rc=$CHK"
[ "$EWT" = 0 ] && [ "$CHK" = 0 ] && echo "ALL CHECKS PASSED" || exit 1
