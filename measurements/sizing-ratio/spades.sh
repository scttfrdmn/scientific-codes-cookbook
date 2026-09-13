set -eo pipefail   # no -u: biocontainers' conda-activate env script trips nounset
cd /tmp
__INSTRUMENT__
capture_topo; RUN_T0=$(date +%s)
echo "== inputs (shape check) =="
test -s ecoli_R1.fq.gz && test -s ecoli_R2.fq.gz
echo "read pairs (r1): $(( $(zcat ecoli_R1.fq.gz | wc -l) / 4 ))"

echo "== spades --isolate, 8 threads, 30 GiB cap =="
mon_start spades
spades.py --isolate -1 ecoli_R1.fq.gz -2 ecoli_R2.fq.gz -o out -t 8 -m 30
mon_stop spades

CONTIGS=$(grep -c '^>' out/contigs.fasta || echo 0)
LARGEST=$(awk '/^>/{if(l>m)m=l; l=0; next}{l+=length($0)}END{if(l>m)m=l; print m+0}' out/contigs.fasta)
RUN_WALL=$(( $(date +%s) - RUN_T0 ))
{
  cat /tmp/topo.txt
  echo "== measurement =="
  report spades
  echo "contigs=$CONTIGS largest_contig_bp=$LARGEST"
  echo "cgroup_memory_peak=$(cat /sys/fs/cgroup/memory.peak 2>/dev/null)"
  echo "run_compute_wall_s=$RUN_WALL run_compute_cost_usd=$(awk -v r=$RATE -v w=$RUN_WALL 'BEGIN{printf "%.4f",r*w/3600}')  # excludes boot/pull"
  echo "# anon_max = hard memory floor (k-mer graph); peak_rss includes cache."
  echo "# cost_usd on report line = rate * compute wall (not billed instance time)."
} > measure.txt
cat measure.txt
cp samples-spades.tsv samples.tsv 2>/dev/null || true
echo "MEASURE DONE"
