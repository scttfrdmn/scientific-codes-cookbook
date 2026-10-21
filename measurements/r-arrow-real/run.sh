#!/usr/bin/env bash
# r-arrow-real task body: verify the staged TLC bytes and the polars reference, record the host,
# run analysis.R, record the footprint.
set +e
set -uo pipefail
# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
export TMPDIR=/tmp HOME=/tmp TZ=UTC PATH="$PATH:/opt/conda/bin"
NT="${NT:-16}"
mkdir -p /tmp/out
cd /tmp/tlc
echo '== staged TLC bytes must match their pins (every staged file is checked) =='
while read -r h f; do [ -e "$f" ] && echo "$h  $f"; done < /tmp/pins.sha256 > /tmp/present.sha256
sha256sum -c /tmp/present.sha256 > /tmp/out/staged.sha256 2>&1 || { cat /tmp/out/staged.sha256; echo 'FAIL: a staged input does not match its pin'; exit 1; }
test "$(ls yellow_tripdata_*.parquet | wc -l)" = "$(wc -l < /tmp/present.sha256)" || { echo 'FAIL: a staged file has no pin'; exit 1; }
echo "  $(wc -l < /tmp/present.sha256) files match"
REF=""
if [ -s /tmp/ref.sha256 ]; then
  (cd /tmp/ref && sha256sum -c /tmp/ref.sha256) || { echo 'FAIL: the polars reference does not match its pin'; exit 1; }
  REF=/tmp/ref
fi
{ uname -m; lscpu | grep -iE 'vendor id|model name|^cpu\(s\)|thread\(s\) per core|core\(s\) per socket|numa node\(s\)'; nproc; grep MemTotal /proc/meminfo; } > /tmp/out/host.txt
cat /tmp/out/host.txt
df -h /tmp | tail -1
T0=$(date +%s)
REF_DIR="$REF" RW_DIR=/tmp/rw Rscript /tmp/analysis.R /tmp/tlc /tmp/out "$NT" > /tmp/out/analysis.log 2>&1
RC=$?
T1=$(date +%s)
cat /tmp/out/analysis.log
CG=/sys/fs/cgroup
{ echo "analysis.R wall_s $((T1 - T0))"; echo "exit $RC";
  for f in memory.peak memory.max; do [ -r $CG/$f ] && echo "cgroup $f $(cat $CG/$f)"; done;
  du -sk /tmp/rw 2>/dev/null; df -k /tmp | tail -1; } > /tmp/out/footprint.txt
cat /tmp/out/footprint.txt
[ "$RC" = 0 ] || { echo 'FAIL: analysis.R failed'; exit 1; }
grep -q '^R-ARROW REAL OK' /tmp/out/check.txt || { echo 'FAIL: no completion sentinel'; exit 1; }
for f in check.txt results.tsv files.csv pay.csv hour.csv dist.csv; do [ -s /tmp/out/$f ] || { echo "FAIL: $f empty"; exit 1; }; done
echo DONE
