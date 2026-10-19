#!/usr/bin/env bash
# measurements/nipype-real: a nipype DTI workflow over the Rockland diffusion cohort, four runs.
#   run.sh <n_procs> [first N subjects; default all]
#   A  MultiProc, the cohort            -> the workload, timed
#   B  Linear, 4 subjects, fresh dir    -> plugin invariance
#   C  A again, unchanged, same dir     -> result reuse
#   D  A with stats' `tag` changed      -> exactly the downstream nodes re-run
#   E  hashprobe.py, 1 node, twice      -> why a Function node fed its own log never caches
set +e
set -uo pipefail
P=${1:?n_procs}; N=${2:-0}
export TMPDIR=/var/tmp HOME=/var/tmp MPLBACKEND=Agg
# one node per worker: every tool single-threaded, so n_procs is the only parallelism
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
cd /tmp
CG=/sys/fs/cgroup
cpu() { awk '/^usage_usec/{print $2; exit}' $CG/cpu.stat 2>/dev/null || echo 0; }
peak() { cat $CG/memory.peak 2>/dev/null || echo 0; }

echo "== the staged scans must be the pinned bytes =="
(cd /tmp/dwi && sha256sum -c --quiet /tmp/dwi.sha256) || { echo "FAIL: a DWI file does not match dwi.sha256"; exit 1; }
echo "  $(wc -l < /tmp/dwi.sha256) files match"

# work on the root disk, not the half-RAM /tmp tmpfs (spawn#1179); prove the image's user can write it
mkdir -p /var/tmp/log /var/tmp/ref && touch /var/tmp/log/.w || { echo "FAIL: /var/tmp not writable"; exit 1; }
tar -xf /tmp/ref-maps.tar -C /var/tmp/ref || { echo "FAIL: extract the reference maps"; exit 1; }

echo "== host and libraries =="
{ echo "arch	$(uname -m)"
  echo "nproc	$(OMP_NUM_THREADS= nproc)"
  lscpu 2>/dev/null | awk -F: '/^Model name|^Thread\(s\) per core|^Core\(s\) per socket|^Socket\(s\)/{gsub(/^ +/,"",$2); print $1 "\t" $2}'
  echo "afni	$(afni -ver 2>/dev/null | head -1)"
  python3 -u - <<'PY'
import ctypes, glob, sys
def ver(m):
    try:
        return str(getattr(__import__(m), "__version__", "unknown"))
    except Exception as e:
        return "unavailable (%s)" % type(e).__name__
for m in ("nipype", "networkx", "traits", "dipy", "nibabel", "numpy", "scipy"):
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

if [ "$N" -gt 0 ]; then awk -v n=$N 'NR<=n' subjects.txt > ids.txt; else cp subjects.txt ids.txt; fi
awk 'NR<=4' ids.txt > lin.txt
echo "== $(wc -l < ids.txt) subjects, n_procs $P =="

phase() {  # <name> <base_dir> <plugin> <ids> <tag>
  local c0 t0 t1 c1
  c0=$(cpu); t0=$(date +%s)
  python3 -u /tmp/pipeline.py "$2" "$3" "$P" "$4" "$5" "$1" > /tmp/phase-$1.log 2>&1
  local rc=$?
  t1=$(date +%s); c1=$(cpu)
  printf '%s_rc\t%s\n%s_wall_s\t%s\n%s_avg_cores\t%s\n%s_cgroup_peak_mib\t%s\n' "$1" "$rc" "$1" "$((t1-t0))" \
    "$1" "$(awk -v a="$c0" -v b="$c1" -v w="$((t1-t0))" 'BEGIN{if(w>0) printf "%.2f", (b-a)/(w*1e6); else print 0}')" \
    "$1" "$(( $(peak) / 1048576 ))" >> /tmp/phases.txt
  tail -3 /tmp/phase-$1.log
  return $rc
}
: > /tmp/phases.txt
phase A /var/tmp/wf  MultiProc ids.txt t1; RA=$?
phase B /var/tmp/wfl Linear    lin.txt t1; RB=$?
phase C /var/tmp/wf  MultiProc ids.txt t1; RC_=$?
phase D /var/tmp/wf  MultiProc ids.txt t2; RD=$?
RE=0
for m in file dir file dir; do python3 -u /tmp/hashprobe.py $m >> /tmp/phase-E.log 2>&1 || RE=1; done
tail -2 /tmp/phase-E.log
echo "  rc A=$RA B=$RB C=$RC_ D=$RD E=$RE"

python3 -u /tmp/check.py ids.txt "$P" lin.txt > /tmp/checks.txt 2>&1
RC=$?
cat /tmp/checks.txt
# the evidence: callbacks, side effects, run records, nipype's own log, any crash files
cp /var/tmp/wf/dti/join/cohort.jsonl /tmp/cohort.jsonl 2>/dev/null || : > /tmp/cohort.jsonl
tar -czf /tmp/nipype-logs.tar.gz -C /var/tmp log /tmp/phase-A.log /tmp/phase-B.log /tmp/phase-C.log /tmp/phase-D.log /tmp/phase-E.log 2>/dev/null
test -f /tmp/nipype-logs.tar.gz || tar -czf /tmp/nipype-logs.tar.gz -T /dev/null
{ cat /tmp/phases.txt; cat /tmp/host.txt; cat /tmp/checks.txt; } > /tmp/summary.txt
for r in $RA $RB $RC_ $RD $RE $RC; do test "$r" = 0 || { echo "FAIL: a phase or an assertion failed"; exit 1; }; done
echo DONE
