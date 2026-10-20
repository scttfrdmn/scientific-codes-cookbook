#!/usr/bin/env bash
# rebound-real task body: verify the staged DE440 bytes, then run, on separate cores,
#   core 1: long.py, the nominal long WHFast run;
#   core 2: real.py (the DE440-span checks), then long.py again with Mercury moved 1 m (the shadow);
# then longcheck.py on the pair. Records the host and the footprint.
#   bash run.sh <span in Myr>
set +e
set -uo pipefail
export TMPDIR=/tmp HOME=/tmp MPLCONFIGDIR=/tmp/mpl OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
SPAN="${1:?span in Myr}"
O=/tmp/out
mkdir -p $O
cd /tmp/de440
echo '== staged DE440 bytes must match their pins =='
sha256sum -c /tmp/pins.sha256 > $O/staged.sha256 2>&1 || { cat $O/staged.sha256; echo 'FAIL: a staged input does not match its pin'; exit 1; }
cat $O/staged.sha256
{ uname -m; lscpu | grep -iE 'vendor id|model name|^cpu\(s\)|thread\(s\) per core|core\(s\) per socket|numa node\(s\)'; nproc;
  python3 - <<'PYEOF' 2>&1
import ctypes, glob, sys
libs = sorted(glob.glob(sys.prefix + "/lib/libopenblas*.so*"))
try:
    f = ctypes.CDLL(libs[0]).openblas_get_corename
    f.restype = ctypes.c_char_p
    print("openblas_corename", f().decode(), "(numpy's BLAS; REBOUND's integrators are plain C and call no BLAS)")
except Exception as e:
    print("openblas_corename unavailable:", type(e).__name__, libs[:1])
PYEOF
} > $O/host.txt
cat $O/host.txt
C=/tmp
T0=$(date +%s)
python3 -u $C/long.py /tmp/de440 $O/long-nominal.npz "$SPAN" 0 > $O/long-nominal.log 2>&1 &
PID=$!
python3 -u $C/real.py /tmp/de440 $O > $O/real.log 2>&1
RC_REAL=$?
T1=$(date +%s)
python3 -u $C/long.py /tmp/de440 $O/long-shadow.npz "$SPAN" 1.0 > $O/long-shadow.log 2>&1
RC_SHADOW=$?
T2=$(date +%s)
wait $PID
RC_NOM=$?
T3=$(date +%s)
python3 -u $C/longcheck.py $O/long-nominal.npz $O/long-shadow.npz "$SPAN" $O/long.json $O/check.txt >> $O/real.log 2>&1
RC_CHECK=$?
T4=$(date +%s)
cat $O/real.log; tail -3 $O/long-nominal.log $O/long-shadow.log
CG=/sys/fs/cgroup
{ echo "real.py wall_s $((T1 - T0)) exit $RC_REAL"
  echo "long-shadow wall_s $((T2 - T1)) exit $RC_SHADOW"
  echo "long-nominal wall_s $((T3 - T0)) exit $RC_NOM (ends when the slower of the two cores' work ends)"
  echo "longcheck wall_s $((T4 - T3)) exit $RC_CHECK"
  echo "total wall_s $((T4 - T0))"
  for f in memory.peak memory.max; do [ -r $CG/$f ] && echo "cgroup $f $(cat $CG/$f)"; done
  grep -h '^maxrss_kib' $O/real.log $O/long-nominal.log $O/long-shadow.log
  df -k /tmp | tail -1; } > $O/footprint.txt
cat $O/footprint.txt
[ "$RC_REAL$RC_SHADOW$RC_NOM$RC_CHECK" = 0000 ] || { echo 'FAIL: a part failed'; exit 1; }
grep -q '^FAIL' $O/check.txt && { echo 'FAIL: a check failed'; exit 1; }
for f in check.txt results.json long.json ephem.npz long-nominal.npz long-shadow.npz; do [ -s $O/$f ] || { echo "FAIL: $f empty"; exit 1; }; done
echo 'REBOUND REAL OK' >> $O/check.txt
echo DONE
