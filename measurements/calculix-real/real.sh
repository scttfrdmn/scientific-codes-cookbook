#!/usr/bin/env bash
# calculix-real task body: the NAFEMS LE10 ladder (le10.py), plus the host facts that make the
# timings comparable. Everything lands in /tmp/out/, staged out as a directory.
#
#   bash real.sh <threads> "<ladder>" "<iterative rungs>" <serial rung>
set +e
set -uo pipefail
T="$1"; LADDER="$2"; ITER="$3"; SER="$4"
export TMPDIR=/tmp HOME=/tmp
OUT=/tmp/out; mkdir -p "$OUT"
cd /tmp

CCX=$(command -v ccx)
{
  echo "uname_m	$(uname -m)"
  echo "nproc_all	$(nproc --all 2>/dev/null || echo unknown)"
  grep -m1 -E '^(model name|CPU part)' /proc/cpuinfo
  grep MemTotal /proc/meminfo
  command -v lscpu >/dev/null && lscpu
  echo "== ccx links"
  ldd "$CCX" 2>&1 | grep -iE 'blas|lapack|arpack|spooles|pastix|gomp|pthread'
  echo "== openblas"
  python3 -u - <<'PY'
import ctypes, glob, os, sys
libs = sorted(glob.glob(os.path.join(sys.prefix, "lib", "libopenblas*.so*")))
for p in libs[:1]:
    try:
        lib = ctypes.CDLL(p)
        lib.openblas_get_corename.restype = ctypes.c_char_p
        print("openblas_corename\t%s" % lib.openblas_get_corename().decode())
    except Exception as e:
        print("openblas_corename\tunavailable (%s)" % type(e).__name__)
if not libs:
    print("openblas_corename\tno libopenblas in %s" % sys.prefix)
PY
} > "$OUT/host.txt" 2>&1
grep -E 'Thread\(s\) per core|Model name|openblas_corename' "$OUT/host.txt"

t0=$(date +%s)
python3 -u le10.py --threads "$T" --ladder "$LADDER" --iter-rungs "$ITER" --serial-rung "$SER" \
  --out "$OUT" --work /var/tmp/le10 --ccx "$CCX" > "$OUT/le10.log" 2>&1
rc=$?
t1=$(date +%s)
peak=$(cat /sys/fs/cgroup/memory.peak 2>/dev/null || echo unknown)
printf 'le10_exit\t%s\nle10_s\t%s\ncgroup_memory_peak_bytes\t%s\n' "$rc" "$((t1 - t0))" "$peak" > "$OUT/times.tsv"
cat "$OUT/times.tsv"
grep -vE '^(mesh|run) ' "$OUT/le10.log" | tail -60
test "$rc" = 0 || { echo "FAIL: le10.py exit $rc"; exit 1; }
grep -q '^failed_checks	none$' "$OUT/score.tsv" || { echo "FAIL: a check failed"; exit 1; }
echo "DONE"
