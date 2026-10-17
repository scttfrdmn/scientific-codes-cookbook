# The seurat recipe's three legs, sourced by 01-crosscheck.task.json once every staged file has
# matched its sha256 pin. Staged rather than inline because spawn carries the task script in
# EC2 user data (16 KiB, spawn#1063).
tar -xzf pbmc3k.tar.gz
MTX=filtered_gene_bc_matrices/hg19
ls -l "$MTX" | awk 'NR>1{print "mtxfile\t"$9"\t"$5}'

# ------------------------------------------------------------------ 1. Scanpy
python3 -u scanpy_leg.py 2>&1 | tee scanpy.log
test "${PIPESTATUS[0]}" = "0" || { echo "FAIL: scanpy leg failed"; exit 1; }

# ------------------------------------------------------------------ 2. Seurat
Rscript seurat_leg.R > seurat.log 2>&1
RRC=$?
cat seurat.log
test "$RRC" = "0" || { echo "FAIL: Seurat leg exited $RRC"; exit 1; }
grep -q "SEURAT OK" seurat.log || { echo "FAIL: Seurat did not reach completion"; exit 1; }

# ------------------------------------------------------------------ 3. compare
python3 -u compare.py 2>&1 | tee compare.log
test "${PIPESTATUS[0]}" = "0" || { echo "FAIL: comparison failed"; exit 1; }
cp score.tsv seurat-diag.txt
uname -m | awk '{print "arch\t"$1}'
echo "DONE"
