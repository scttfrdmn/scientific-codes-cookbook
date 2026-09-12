set -euo pipefail
cd /tmp
__INSTRUMENT__
capture_topo; RUN_T0=$(date +%s)
echo "== inputs (shape check) =="
test -s ecoli_R1.fq.gz && test -s ecoli_R2.fq.gz
echo "read pairs (r1): $(( $(zcat ecoli_R1.fq.gz | wc -l) / 4 ))"

echo "== megahit, 8 threads (output dir must not pre-exist) =="
mon_start megahit
megahit -1 ecoli_R1.fq.gz -2 ecoli_R2.fq.gz -o out -t 8
mon_stop megahit

CONTIGS=$(grep -c '^>' out/final.contigs.fa || echo 0)
LARGEST=$(awk '/^>/{if(l>m)m=l; l=0; next}{l+=length($0)}END{if(l>m)m=l; print m+0}' out/final.contigs.fa)
RUN_WALL=$(( $(date +%s) - RUN_T0 ))
{
  cat /tmp/topo.txt
  echo "== measurement =="
  report megahit
  echo "contigs=$CONTIGS largest_contig_bp=$LARGEST"
  echo "cgroup_memory_peak=$(cat /sys/fs/cgroup/memory.peak 2>/dev/null)"
  echo "run_compute_wall_s=$RUN_WALL run_compute_cost_usd=$(awk -v r=$RATE -v w=$RUN_WALL 'BEGIN{printf "%.4f",r*w/3600}')  # excludes boot/pull"
  echo "# compare anon_max to spades on the SAME reads: the succinct-de-Bruijn ratio."
} > measure.txt
cat measure.txt
cp samples-megahit.tsv samples.tsv 2>/dev/null || true
echo "MEASURE DONE"
