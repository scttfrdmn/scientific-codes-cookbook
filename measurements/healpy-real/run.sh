#!/usr/bin/env bash
# healpy-real task body: verify the staged Planck bytes, run real.py, record the host and footprint.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:$PATH TMPDIR=/tmp HOME=/tmp MPLCONFIGDIR=/tmp/mpl
export OMP_NUM_THREADS=8 OPENBLAS_NUM_THREADS=1
mkdir -p /tmp/out
cd /tmp/pr3
echo '== staged Planck bytes must match their pins =='
sha256sum -c /tmp/pins.sha256 > /tmp/out/staged.sha256 2>&1 || { cat /tmp/out/staged.sha256; echo 'FAIL: a staged input does not match its pin'; exit 1; }
cat /tmp/out/staged.sha256
{ uname -m; lscpu | grep -iE 'vendor id|model name|^cpu\(s\)|thread\(s\) per core|core\(s\) per socket|numa node\(s\)'; nproc; } > /tmp/out/host.txt
cat /tmp/out/host.txt
df -h /tmp | tail -1
T0=$(date +%s)
python3 -u /tmp/real.py /tmp/pr3 /tmp/out > /tmp/out/real.log 2>&1
RC=$?
T1=$(date +%s)
cat /tmp/out/real.log
CG=/sys/fs/cgroup
{ echo "real.py wall_s $((T1 - T0))"; echo "exit $RC";
  for f in memory.peak memory.max; do [ -r $CG/$f ] && echo "cgroup $f $(cat $CG/$f)"; done;
  df -k /tmp | tail -1; } > /tmp/out/footprint.txt
cat /tmp/out/footprint.txt
[ "$RC" = 0 ] || { echo 'FAIL: real.py failed'; exit 1; }
grep -q '^HEALPY REAL OK$' /tmp/out/check.txt || { echo 'FAIL: no completion sentinel'; exit 1; }
for f in check.txt results.json spectra.npz; do [ -s /tmp/out/$f ] || { echo "FAIL: $f empty"; exit 1; }; done
echo DONE
