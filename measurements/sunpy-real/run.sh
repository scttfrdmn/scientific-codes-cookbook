#!/usr/bin/env bash
# sunpy-real task body: verify the staged AIA bytes, record the host, run real.py, record the footprint.
#   bash run.sh [file ...]      a subset (a sizing run); default every staged image
set +e
set -uo pipefail
export PATH=${CONDA_PREFIX:-/opt/conda}/bin:$PATH TMPDIR=/tmp HOME=/tmp MPLCONFIGDIR=/tmp/mpl LC_ALL=C
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
NPROC=$(getconf _NPROCESSORS_ONLN)
mkdir -p /tmp/out /tmp/.cache /tmp/mpl
cd /work/in
echo '== staged AIA bytes must match their pins =='
if [ "$#" -gt 0 ]; then
  : > /tmp/want.sha256
  for f in "$@"; do grep "  $f\$" /tmp/pins.sha256 >> /tmp/want.sha256; done
else
  cp /tmp/pins.sha256 /tmp/want.sha256
fi
sha256sum -c /tmp/want.sha256 > /tmp/out/staged.sha256 2>&1 || { tail -5 /tmp/out/staged.sha256; echo 'FAIL: a staged input does not match its pin'; exit 1; }
echo "pins ok: $(wc -l < /tmp/want.sha256) files"
{ uname -m; lscpu | grep -iE 'vendor id|model name|^cpu\(s\)|thread\(s\) per core|core\(s\) per socket|numa node\(s\)';
  echo "nproc_used $NPROC";
  python3 -c "import numpy; c=numpy.show_config(mode='dicts'); print('blas', c['Build Dependencies']['blas'].get('name'))" 2>/dev/null; } > /tmp/out/host.txt
cat /tmp/out/host.txt
df -h /work /tmp | tail -2
T0=$(date +%s)
python3 -u /tmp/real.py /work/in /tmp/out "$NPROC" "$@" > /tmp/out/real.log 2>&1
RC=$?
T1=$(date +%s)
tail -40 /tmp/out/real.log
CG=/sys/fs/cgroup
{ echo "real.py wall_s $((T1 - T0))"; echo "exit $RC";
  for f in memory.peak memory.max; do [ -r $CG/$f ] && echo "cgroup $f $(cat $CG/$f)"; done;
  df -k /tmp /work | tail -2; } > /tmp/out/footprint.txt
cat /tmp/out/footprint.txt
[ "$RC" = 0 ] || { echo 'FAIL: real.py failed'; exit 1; }
grep -q '^SUNPY REAL OK$' /tmp/out/check.txt || { echo 'FAIL: no completion sentinel'; exit 1; }
for f in check.txt results.json; do [ -s /tmp/out/$f ] || { echo "FAIL: $f empty"; exit 1; }; done
echo DONE
