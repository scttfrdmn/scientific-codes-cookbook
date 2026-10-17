# seurat-real task body, sourced by the spec once every staged file matches its pin.
# r1 = the result (Scanpy leg, then Seurat's workflow with FindAllMarkers); r2 = both legs again in
# fresh processes, for the within-chip determinism check. Wall times are r1's.
export OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 MKL_NUM_THREADS=4 NUMBA_NUM_THREADS=4
tar -xzf pbmc10k.tar.gz
now() { date +%s.%N; }
uname -m | awk '{print "arch\t"$1}'
nproc | awk '{print "nproc\t"$1}'
python3 -c "import numpy, scipy.linalg; from threadpoolctl import threadpool_info as t; [print('blas\t%s %s %s' % (i.get('internal_api'), i.get('version'), i.get('architecture'))) for i in t()]" 2>/dev/null || true

T0=$(now)
python3 -u scanpy_leg.py r1 2>&1 | tee scanpy.log
test "${PIPESTATUS[0]}" = "0" || { echo "FAIL: scanpy leg failed"; exit 1; }
T1=$(now)
Rscript seurat_leg.R r1 r1 1 > seurat.log 2>&1; RRC=$?
cat seurat.log
test "$RRC" = "0" && grep -q "SEURAT OK" seurat.log || { echo "FAIL: Seurat leg exited $RRC"; exit 1; }
T2=$(now)
awk -v a="$T0" -v b="$T1" -v c="$T2" 'BEGIN{printf "wall_r1\tscanpy %.1f s, seurat %.1f s, total %.1f s\n", b-a, c-b, c-a}'

python3 -u scanpy_leg.py r2 > scanpy-r2.log 2>&1 || { cat scanpy-r2.log; echo "FAIL: scanpy r2"; exit 1; }
Rscript seurat_leg.R r1 r2 0 > seurat-r2.log 2>&1 || { cat seurat-r2.log; echo "FAIL: Seurat r2"; exit 1; }
grep -E "clusters|wall" scanpy-r2.log seurat-r2.log

python3 -u compare.py r1 r2 2>&1 | tee compare.log
test "${PIPESTATUS[0]}" = "0" || { echo "FAIL: comparison failed"; exit 1; }
cp r1/markers_all.tsv markers_all.tsv
for f in /sys/fs/cgroup/memory.peak /sys/fs/cgroup/memory/memory.max_usage_in_bytes; do
  [ -r "$f" ] && awk -v f="$f" '{printf "cgroup_peak\t%.2f GiB (%s)\n", $1/2^30, f}' "$f"
done
echo "DONE"
