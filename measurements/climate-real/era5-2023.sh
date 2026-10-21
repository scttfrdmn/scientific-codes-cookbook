# Outputs travel as one directory (/tmp/out/ in the spec): copied on any exit, so a failed run
# still leaves its log behind.
trap 'mkdir -p /tmp/out && cp -f /tmp/era5.log /tmp/era5-results.tsv /tmp/annual_mean_2023.npy /tmp/global_hourly_2023.npy /tmp/cpu.txt /tmp/out/ 2>/dev/null; true' EXIT
set +e
set -uo pipefail
# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
export PATH=/opt/conda/bin:$PATH TMPDIR=/tmp HOME=/tmp
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 ERA5_THREADS=8 ERA5_DIR=/tmp/era5
cd /tmp
echo "== staged bytes must match their pins =="
T0=$(date +%s)
sha256sum -c pins.sha256 || { echo "FAIL: a staged input does not match its pin"; exit 1; }
echo "sha256 of 13.35 GB in $(( $(date +%s) - T0 ))s"
lscpu | grep -iE 'model name|^cpu\(s\)|thread\(s\) per core|numa node\(s\)' > cpu.txt
python3 -u -c "import ctypes,glob,os,sys
try:
 l=ctypes.CDLL(sorted(glob.glob(os.path.join(sys.prefix,'lib','libopenblas*.so*')))[0]); l.openblas_get_corename.restype=ctypes.c_char_p; print('openblas_corename\t'+l.openblas_get_corename().decode())
except Exception as e:
 print('openblas_corename\tunknown (%s)' % e)" >> cpu.txt 2>&1
cat cpu.txt
df -h /tmp | tee -a cpu.txt
T0=$(date +%s)
python3 -u era5.py > era5.log 2>&1
RC=$?
echo "era5.py exit $RC in $(( $(date +%s) - T0 ))s"
cat era5.log
df -h /tmp
test "$RC" = 0 || { echo "FAIL: the workload failed"; exit 1; }
grep -q 'ERA5 REAL OK' era5.log || { echo "FAIL: no completion sentinel"; exit 1; }
uname -m | awk '{print "arch\t"$1}'
echo DONE
