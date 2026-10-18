# scanpy-real task body, sourced by the spec once every staged file matches its pin.
# r1 = the cell-count ladder (20,220 / 40,441 / 80,882 / 161,764 nested cells), timed per step,
# with every check on the full rung. r2 = the full rung again in a fresh process, for determinism.
export OMP_NUM_THREADS=8 OPENBLAS_NUM_THREADS=8 MKL_NUM_THREADS=8 NUMBA_NUM_THREADS=8
uname -m | awk '{print "arch\t"$1}'
nproc | awk '{print "nproc\t"$1}'
lscpu | grep -iE 'model name|^cpu\(s\)|numa node\(s\)' || true
python3 -c "import numpy; from threadpoolctl import threadpool_info as t; [print('blas\t%s %s %s' % (str(i.get('internal_api')), str(i.get('version')), str(i.get('architecture')))) for i in t()]" || true

T0=$(date +%s)
python3 -u pipeline.py r1 20220 40441 80882 161764 > pipeline-r1.log 2>&1; RC=$?
T1=$(date +%s)
cat pipeline-r1.log
echo "wall_r1_s	$((T1 - T0))"
test "$RC" = "0" && grep -q "PIPELINE OK" pipeline-r1.log || { echo "FAIL: r1 exited $RC"; exit 1; }

python3 -u pipeline.py r2 161764 > pipeline-r2.log 2>&1; RC=$?
T2=$(date +%s)
grep -vE '^version|^blas' pipeline-r2.log
echo "wall_r2_s	$((T2 - T1))"
test "$RC" = "0" && grep -q "PIPELINE OK" pipeline-r2.log || { echo "FAIL: r2 exited $RC"; exit 1; }

python3 -u compare.py r1 r2 > compare.log 2>&1; RC=$?
cat compare.log
test "$RC" = "0" || { echo "FAIL: determinism"; exit 1; }
cp r1/steps.csv steps.csv
for f in /sys/fs/cgroup/memory.peak /sys/fs/cgroup/memory/memory.max_usage_in_bytes; do
  [ -r "$f" ] && awk -v f="$f" '{printf "cgroup_peak\t%.2f GiB (%s)\n", $1/2^30, f}' "$f"
done
echo "DONE"
