#!/usr/bin/env bash
# specutils-real task body: verify the staged DESI bytes, record the host, fit (real.py), check (check.py).
#   bash run.sh [pix,pix,...]      a subset (a sizing run); default every staged pixel
set +e
set -uo pipefail
export PATH=${CONDA_PREFIX:-/opt/conda}/bin:$PATH TMPDIR=/tmp HOME=/tmp MPLCONFIGDIR=/tmp/mpl LC_ALL=C
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
PIX="${1:-}"
NPROC=$(getconf _NPROCESSORS_ONLN)
mkdir -p /tmp/out /tmp/desi/desiutil
cp /tmp/desispec_emlinefit.py /tmp/desi/desispec_iron_emlinefit.py
cp /tmp/desiutil_dust.py /tmp/desi/desiutil_dust.py
cp /tmp/desiutil_log.py /tmp/desi/desiutil/log.py
: > /tmp/desi/desiutil/__init__.py
cd /work/in
echo '== staged DESI bytes must match the release sha256sum files =='
if [ -n "$PIX" ]; then
  grep -E -- "-($(echo "$PIX" | tr , '|'))\.fits$" /tmp/pins.sha256 > /tmp/want.sha256
else
  cp /tmp/pins.sha256 /tmp/want.sha256
fi
sha256sum -c /tmp/want.sha256 > /tmp/out/staged.sha256 2>&1 || { tail -5 /tmp/out/staged.sha256; echo 'FAIL: a staged input does not match its pin'; exit 1; }
echo "pins ok: $(wc -l < /tmp/want.sha256) files"
( cd /work/sfd && sha256sum -c /tmp/sfd.sha256 ) >> /tmp/out/staged.sha256 2>&1 || { echo 'FAIL: a staged SFD map does not match its pin'; exit 1; }
export SFD_DIR=/work/sfd
{ uname -m; lscpu | grep -iE 'vendor id|model name|^cpu\(s\)|thread\(s\) per core|core\(s\) per socket|numa node\(s\)';
  echo "nproc_used $NPROC";
  python3 -c "
import numpy, scipy, specutils, astropy
print('versions numpy', numpy.__version__, 'scipy', scipy.__version__, 'specutils', specutils.__version__, 'astropy', astropy.__version__)
import ctypes, glob, sys
for p in sorted(glob.glob(sys.prefix + '/lib/libopenblas*.so*'))[:1]:
    try:
        f = ctypes.CDLL(p).openblas_get_corename; f.restype = ctypes.c_char_p
        print('openblas', p.rsplit('/', 1)[-1], 'corename', f().decode())
    except Exception as e: print('openblas', p, 'corename unknown', type(e).__name__)
" 2>&1; } > /tmp/out/host.txt
cat /tmp/out/host.txt
df -h /work /tmp | tail -2
T0=$(date +%s)
python3 -u /tmp/real.py /work/in /tmp/out "$NPROC" "$PIX" > /tmp/out/real.log 2>&1
RC=$?
T1=$(date +%s)
tail -5 /tmp/out/real.log
python3 -u /tmp/check.py /tmp/out > /tmp/out/check.log 2>&1
CRC=$?
T2=$(date +%s)
cat /tmp/out/check.log
CG=/sys/fs/cgroup
{ echo "real.py wall_s $((T1 - T0))"; echo "check.py wall_s $((T2 - T1))"; echo "exit real $RC check $CRC";
  for f in memory.peak memory.max; do [ -r $CG/$f ] && echo "cgroup $f $(cat $CG/$f)"; done;
  df -k /tmp /work | tail -2; } > /tmp/out/footprint.txt
cat /tmp/out/footprint.txt
tar -C /tmp/out -cf /tmp/out/pix.tar pix && rm -rf /tmp/out/pix
[ "$RC" = 0 ] && grep -q '^FITS DONE$' /tmp/out/real.log || { echo 'FAIL: real.py failed'; exit 1; }
[ "$CRC" = 0 ] || { echo 'FAIL: an identity failed'; exit 1; }
grep -q '^SPECUTILS REAL OK$' /tmp/out/check.txt || { echo 'FAIL: no completion sentinel'; exit 1; }
for f in check.txt results.json pix.tar; do [ -s /tmp/out/$f ] || { echo "FAIL: $f empty"; exit 1; }; done
echo DONE
