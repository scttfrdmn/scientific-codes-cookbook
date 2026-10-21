#!/usr/bin/env bash
# r-arrow-real reference task body: verify the staged TLC bytes, compute the four tables with polars.
set +e
set -uo pipefail
# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
export TMPDIR=/tmp HOME=/tmp TZ=UTC PATH="$PATH:/opt/conda/bin"
mkdir -p /tmp/out/ref
cd /tmp/tlc
while read -r h f; do [ -e "$f" ] && echo "$h  $f"; done < /tmp/pins.sha256 > /tmp/present.sha256
sha256sum -c /tmp/present.sha256 > /tmp/out/staged.sha256 2>&1 || { cat /tmp/out/staged.sha256; echo 'FAIL: a staged input does not match its pin'; exit 1; }
test "$(ls yellow_tripdata_*.parquet | wc -l)" = "$(wc -l < /tmp/present.sha256)" || { echo 'FAIL: a staged file has no pin'; exit 1; }
{ uname -m; lscpu | grep -iE 'model name|^cpu\(s\)|thread\(s\) per core'; } > /tmp/out/host.txt
python3 -u /tmp/reference.py /tmp/tlc /tmp/out/ref > /tmp/out/reference.log 2>&1
RC=$?
cat /tmp/out/reference.log
[ "$RC" = 0 ] || { echo 'FAIL: reference.py failed'; exit 1; }
grep -q '^REFERENCE OK' /tmp/out/reference.log || { echo 'FAIL: no completion sentinel'; exit 1; }
(cd /tmp/out/ref && sha256sum files.csv pay.csv hour.csv dist.csv | tee ../ref.sha256)
echo DONE
