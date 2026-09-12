set -euo pipefail
cd /tmp
__INSTRUMENT__
capture_topo; RUN_T0=$(date +%s)
echo "== inputs (shape check) =="
test -s GRCh38.fa.gz && test -s GRCh38.gtf.gz && test -s r1.fq.gz && test -s r2.fq.gz
gzip -dc GRCh38.fa.gz > genome.fa
gzip -dc GRCh38.gtf.gz > anno.gtf
echo "genome bytes (uncompressed): $(wc -c < genome.fa)"

echo "== PHASE 1: genomeGenerate (the build) =="
mkdir -p /tmp/idx
mon_start build
STAR --runMode genomeGenerate --genomeDir /tmp/idx \
     --genomeFastaFiles genome.fa --sjdbGTFfile anno.gtf --sjdbOverhang 100 \
     --runThreadN 16 --limitGenomeGenerateRAM 31000000000
mon_stop build
IDX_BYTES=$(du -sb /tmp/idx | cut -f1)
{
  cat /tmp/topo.txt
  echo "== measurement =="
  report build
  echo "index_tmpfs_bytes=$IDX_BYTES"
} > measure.txt

rm -f genome.fa   # our own file; frees ~3.1 GB tmpfs before align (never rm staged inputs)

echo "== PHASE 2: align against the full-genome index =="
mon_start align
STAR --genomeDir /tmp/idx --readFilesIn r1.fq.gz r2.fq.gz --readFilesCommand zcat \
     --runThreadN 16 --outSAMtype BAM Unsorted --outFileNamePrefix /tmp/aln.
mon_stop align
RUN_WALL=$(( $(date +%s) - RUN_T0 ))
{
  report align
  echo "cgroup_memory_peak=$(cat /sys/fs/cgroup/memory.peak 2>/dev/null)"
  echo "run_compute_wall_s=$RUN_WALL run_compute_cost_usd=$(awk -v r=$RATE -v w=$RUN_WALL 'BEGIN{printf "%.4f",r*w/3600}')  # excludes boot/pull"
  echo "# INTERPRETATION (two components, kept separate):"
  echo "#  build anon_max    = STAR's HARD build-RAM floor (a reader always pays this)"
  echo "#  index_tmpfs_bytes = WRITE-PATH ARTIFACT: index lands in /tmp (tmpfs=1/2 RAM) only"
  echo "#                      because the image is non-root; EFS / a mounted volume removes it"
  echo "#  align anon_max    = index resident in RAM to align (align is ALSO ~index-sized for"
  echo "#                      STAR -- inverts the bwa 'align is cheap' intuition)"
} >> measure.txt
cat measure.txt
echo "MEASURE DONE"
