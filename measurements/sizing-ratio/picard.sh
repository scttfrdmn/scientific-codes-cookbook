set -euo pipefail
cd /tmp
__INSTRUMENT__
capture_topo; RUN_T0=$(date +%s)
echo "== input (shape check) =="
test -s chr1.bam
echo "bam bytes: $(wc -c < chr1.bam)"

echo "== MarkDuplicates, -Xmx12g generous, GC logging on to read the live-heap high-water =="
mon_start dedup
# GC logging goes via JAVA_TOOL_OPTIONS: the picard wrapper only forwards a subset of
# JVM args and misreads -Xlog as a tool name; the JVM honors this env var directly.
export JAVA_TOOL_OPTIONS="-Xlog:gc*:file=/tmp/gc.log:tags,level"
picard -Xmx12g MarkDuplicates -I chr1.bam -O /tmp/marked.bam -M /tmp/dup_metrics.txt
unset JAVA_TOOL_OPTIONS
mon_stop dedup
RUN_WALL=$(( $(date +%s) - RUN_T0 ))

# post-GC live-heap high-water: the "after" value of each GC pause (->NNN[KMG]), max of them.
LIVE=$(grep -oE '>[0-9]+[KMG]' /tmp/gc.log 2>/dev/null | tr -d '>' | awk '
  {u=substr($0,length($0),1); v=substr($0,1,length($0)-1);
   if(u=="G")b=v*1073741824; else if(u=="M")b=v*1048576; else b=v*1024;
   if(b>m)m=b} END{printf "%.0f", m+0}')
{
  cat /tmp/topo.txt
  echo "== measurement =="
  report dedup
  echo "post_gc_live_heap_bytes=$LIVE   # what MarkDuplicates NEEDS (its working set)"
  echo "cgroup_memory_peak=$(cat /sys/fs/cgroup/memory.peak 2>/dev/null)   # what the JVM HELD (-Xmx12g committed + off-heap)"
  echo "run_compute_wall_s=$RUN_WALL run_compute_cost_usd=$(awk -v r=$RATE -v w=$RUN_WALL 'BEGIN{printf "%.4f",r*w/3600}')  # excludes boot/pull"
  echo "# Pick the box from live-heap NEED, not the JVM's committed footprint. avg_cores ~1 => memory-family."
  grep -E 'PERCENT_DUPLICATION|READ_PAIRS_EXAMINED' /tmp/dup_metrics.txt 2>/dev/null | head -2 || true
} > measure.txt
cat measure.txt
cp samples-dedup.tsv samples.tsv 2>/dev/null || true
echo "MEASURE DONE"
