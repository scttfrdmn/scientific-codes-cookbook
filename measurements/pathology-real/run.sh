#!/usr/bin/env bash
# The task body: host facts, then real.py, then the box's own peak memory. Kept out of the spec's
# `command` so the spec stays small (EC2 user data is 16 KiB, spawn#1063).
#
#   bash run.sh <workers> <budget_gb>
set +e
WORKERS="${1:?workers}"; BUDGET="${2:?budget_gb}"
export TMPDIR=/tmp HOME=/tmp OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
OUT=/tmp/out
mkdir -p "$OUT"
cd /tmp

{
  echo "arch	$(uname -m)"
  lscpu | grep -E '^(Model name|Thread\(s\) per core|Core\(s\) per|Socket\(s\)|CPU\(s\)):' | sed 's/:  */	/'
  echo "mem_total	$(awk '/MemTotal/ {print $2" kB"}' /proc/meminfo)"
  df -m /tmp | tail -1 | awk '{print "tmp_fs\t"$2" MiB"}'
  python3 -u -c 'import ctypes,glob,sys
libs=glob.glob(sys.prefix+"/lib/libopenblas*.so*")
if libs:
    f=ctypes.CDLL(libs[0]).openblas_get_corename; f.restype=ctypes.c_char_p; print("openblas_corename\t"+f().decode())
else:
    print("openblas_corename\tnone")' 2>&1
  python3 -u -c 'import openslide,tifffile,zarr,numpy,skimage
print("versions\topenslide %s / openslide-python %s, tifffile %s, zarr %s, numpy %s, scikit-image %s" % (openslide.__library_version__, getattr(openslide,"__version__","?"), tifffile.__version__, zarr.__version__, numpy.__version__, skimage.__version__))' 2>&1
} > "$OUT/host.txt"
cat "$OUT/host.txt"

T0=$(date +%s)
python3 -u real.py "$WORKERS" "$BUDGET" slides.tsv "$OUT" > "$OUT/real.log" 2>&1
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
grep -q '^PATHOLOGY-REAL OK' "$OUT/real.log" || { echo "FAIL: no completion line"; exit 1; }
test -s "$OUT/slides.jsonl" || { echo "FAIL: no per-slide results"; exit 1; }
echo DONE
