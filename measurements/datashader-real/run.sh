#!/usr/bin/env bash
# datashader-real task body: verify the staged TLC bytes, record the host, run real.py, record the footprint.
set +e
set -uo pipefail
export TMPDIR=/tmp HOME=/tmp NUMBA_CACHE_DIR=/tmp/numba MPLCONFIGDIR=/tmp/mpl
export OPENBLAS_NUM_THREADS=1
NT="${NT:-8}"
mkdir -p /tmp/out
cd /tmp/tlc
echo '== staged TLC bytes must match their pins (every staged file is checked) =='
while read -r h f; do [ -e "$f" ] && echo "$h  $f"; done < /tmp/pins.sha256 > /tmp/present.sha256
sha256sum -c /tmp/present.sha256 > /tmp/out/staged.sha256 2>&1 || { cat /tmp/out/staged.sha256; echo 'FAIL: a staged input does not match its pin'; exit 1; }
cat /tmp/out/staged.sha256
test "$(ls yellow_tripdata_*.parquet | wc -l)" = "$(wc -l < /tmp/present.sha256)" || { echo 'FAIL: a staged file has no pin'; exit 1; }
{ uname -m; lscpu | grep -iE 'vendor id|model name|^cpu\(s\)|thread\(s\) per core|core\(s\) per socket|numa node\(s\)'; nproc; grep MemTotal /proc/meminfo; } > /tmp/out/host.txt
cat /tmp/out/host.txt
df -h /tmp | tail -1
T0=$(date +%s)
python3 -u /tmp/real.py /tmp/tlc /tmp/out "$NT" > /tmp/out/real.log 2>&1
RC=$?
T1=$(date +%s)
cat /tmp/out/real.log
CG=/sys/fs/cgroup
{ echo "real.py wall_s $((T1 - T0))"; echo "exit $RC";
  for f in memory.peak memory.max; do [ -r $CG/$f ] && echo "cgroup $f $(cat $CG/$f)"; done;
  df -k /tmp | tail -1; } > /tmp/out/footprint.txt
cat /tmp/out/footprint.txt
[ "$RC" = 0 ] || { echo 'FAIL: real.py failed'; exit 1; }
grep -q '^DATASHADER REAL OK' /tmp/out/check.txt || { echo 'FAIL: no completion sentinel'; exit 1; }
for f in check.txt results.json rasters.npz; do [ -s /tmp/out/$f ] || { echo "FAIL: $f empty"; exit 1; }; done
echo DONE
