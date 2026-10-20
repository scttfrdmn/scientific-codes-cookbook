#!/usr/bin/env bash
# The task body for both tasks, kept out of the spec's `command` (EC2 user data is 16 KiB, spawn#1063).
#
#   bash run.sh ncref <threads>     # climate image: read the 9 ESGF netCDF files (ncref.py)
#   bash run.sh zarr  <threads>     # pathology image: the Zarr store, seven rewrites (real.py)
set +e
MODE="${1:?ncref|zarr}"; NT="${2:?threads}"
# aarchsci's climate image keeps its python in /opt/conda/bin, which spawn's task shell drops; the
# x86 twin has already put /var/tmp/env/bin first, so leave that alone
[ -d /var/tmp/env/bin ] || export PATH=/opt/conda/bin:$PATH
export TMPDIR=/tmp HOME=/tmp OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
OUT=/tmp/out
mkdir -p "$OUT"
cd /tmp

{
  echo "arch	$(uname -m)"
  lscpu | grep -E '^(Model name|Thread\(s\) per core|Core\(s\) per|Socket\(s\)|CPU\(s\)):' | sed 's/:  */	/'
  echo "mem_total	$(awk '/MemTotal/ {print $2" kB"}' /proc/meminfo)"
  df -m /tmp /var/tmp | tail -2 | awk '{print "fs\t"$6" "$2" MiB"}'
  python3 -u -c 'import ctypes,glob,sys
libs=glob.glob(sys.prefix+"/lib/libopenblas*.so*")
if libs:
    f=ctypes.CDLL(libs[0]).openblas_get_corename; f.restype=ctypes.c_char_p; print("openblas_corename\t"+f().decode())
else:
    print("openblas_corename\tnone")' 2>&1
} > "$OUT/host.txt"
cat "$OUT/host.txt"

T0=$(date +%s)
if [ "$MODE" = ncref ]; then
  sha256sum -c esgf-nc.sha256 || { echo "FAIL: a netCDF file does not match ESGF's sha256"; exit 1; }
  echo "sha256 of 9 netCDF files in $(( $(date +%s) - T0 ))s"
  T0=$(date +%s)
  python3 -u ncref.py /tmp/nc "$OUT" > "$OUT/ncref.log" 2>&1
  RC=$?; LOG="$OUT/ncref.log"; SENT='^NCREF OK'
else
  ( cd /tmp/tas.zarr && md5sum -c --quiet /tmp/tas.md5 ) || { echo "FAIL: the Zarr store does not match the publisher's MD5s"; exit 1; }
  ( cd /tmp/areacella.zarr && md5sum -c --quiet /tmp/areacella.md5 ) || { echo "FAIL: areacella does not match its MD5s"; exit 1; }
  test "$(find /tmp/tas.zarr -type f | wc -l)" = "$(wc -l < /tmp/tas.md5)" || { echo "FAIL: the staged store has objects the pin does not list"; exit 1; }
  echo "md5 of 123 Zarr objects in $(( $(date +%s) - T0 ))s"
  T0=$(date +%s)
  mkdir -p /tmp/work
  python3 -u real.py "$NT" /tmp/work /tmp/ncref "$OUT" > "$OUT/real.log" 2>&1
  RC=$?; LOG="$OUT/real.log"; SENT='^ZARR-DASK-REAL OK'
fi
T1=$(date +%s)
echo "tool_wall_s	$((T1 - T0))" > "$OUT/times.txt"
echo "rc	$RC" >> "$OUT/times.txt"
for f in /sys/fs/cgroup/memory.peak /sys/fs/cgroup/memory/memory.max_usage_in_bytes; do
  test -r "$f" && echo "cgroup_memory_peak	$(cat "$f") ($f)" >> "$OUT/times.txt"
done
df -m /tmp /var/tmp | tail -2 | awk '{print "fs_end\t"$6" used "$3" MiB"}' >> "$OUT/times.txt"
cat "$OUT/times.txt"
tail -60 "$LOG"
test "$RC" = "0" || { echo "FAIL: $MODE exited $RC"; exit 1; }
grep -q "$SENT" "$LOG" || { echo "FAIL: no completion line"; exit 1; }
echo DONE
