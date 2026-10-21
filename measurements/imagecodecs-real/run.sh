#!/usr/bin/env bash
# The task body: host facts, then real.py, then the box's own peak memory. Kept out of the spec's
# `command` so the spec stays small (EC2 user data is 16 KiB, spawn#1063).
#
#   bash run.sh <workers>            # SLIDES=a,b,c in the environment runs a subset
set +e
WORKERS="${1:?workers}"
export TMPDIR=/tmp HOME=/tmp OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
OUT=/tmp/out
mkdir -p "$OUT"
cd /tmp

{
  echo "arch	$(uname -m)"
  lscpu | grep -E '^(Model name|Thread\(s\) per core|Core\(s\) per|Socket\(s\)|CPU\(s\)):' | sed 's/:  */	/'
  echo "mem_total	$(awk '/MemTotal/ {print $2" kB"}' /proc/meminfo)"
  python3 -u -c 'import ctypes,glob,sys
libs=glob.glob(sys.prefix+"/lib/libopenblas*.so*")
if libs:
    f=ctypes.CDLL(libs[0]).openblas_get_corename; f.restype=ctypes.c_char_p; print("openblas_corename\t"+f().decode())
else:
    print("openblas_corename\tnone")' 2>&1
  python3 -u -c 'import imagecodecs as ic, tifffile, numpy, PIL, PIL.features
def v(f):
    try:
        return str(f())
    except Exception as e:
        return "unknown (%s)" % type(e).__name__
print("versions\timagecodecs %s, tifffile %s, numpy %s, pillow %s" % (v(lambda: ic.__version__), v(lambda: tifffile.__version__), v(lambda: numpy.__version__), v(lambda: PIL.__version__)))
print("libs\tlibjxl %s, libjpeg-turbo %s, pillow libtiff %s, pillow jpeg %s" % (v(ic.jpegxl_version), v(ic.jpeg8_version), v(lambda: PIL.features.version("libtiff")), v(lambda: PIL.features.version("jpg"))))' 2>&1
} > "$OUT/host.txt"
cat "$OUT/host.txt"

T0=$(date +%s)
python3 -u real.py "$WORKERS" slides.tsv "$OUT" > "$OUT/real.log" 2>&1
RC=$?
T1=$(date +%s)
echo "tool_wall_s	$((T1 - T0))" > "$OUT/times.txt"
echo "rc	$RC" >> "$OUT/times.txt"
for f in /sys/fs/cgroup/memory.peak /sys/fs/cgroup/memory/memory.max_usage_in_bytes; do
  test -r "$f" && echo "cgroup_memory_peak	$(cat "$f") ($f)" >> "$OUT/times.txt"
done
cat "$OUT/times.txt"
tail -40 "$OUT/real.log"
test "$RC" = "0" || { echo "FAIL: real.py exited $RC"; exit 1; }
grep -q '^IMAGECODECS-REAL OK' "$OUT/real.log" || { echo "FAIL: no completion line"; exit 1; }
test -s "$OUT/slides.jsonl" || { echo "FAIL: no per-slide results"; exit 1; }
echo DONE
