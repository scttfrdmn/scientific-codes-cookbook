set +e
set -uo pipefail
export PATH=/opt/conda/bin:$PATH
export TMPDIR=/tmp HOME=/tmp
cd /tmp

# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
#
# deepTools reads a BAM but cannot create or index one, so this task supplies the .bai --
# the same two-task split the shipped recipe uses, at full scale. It also records the total
# aligned bases, which is what makes the NEXT task's check a conservation identity rather
# than a band: bamCoverage with --normalizeUsing None must redistribute exactly those bases
# across its bins.

echo "== the bytes must match ENCODE's own published md5 =="
# Only the treatment BAM is staged here, and encode.md5 lists both, so select the line
# first -- a bare `md5sum -c` would fail on the file this task deliberately does not have.
grep ENCFF933NSJ encode.md5 > /tmp/this.md5
md5sum -c /tmp/this.md5 || { echo "FAIL: BAM does not match ENCODE's published md5"; exit 1; }

samtools --version | head -2
TPC=$(awk -F, '{n=0; for(i=1;i<=NF;i++){k=split($i,r,"-"); n+=(k==2?r[2]-r[1]+1:1)} print n}' /sys/devices/system/cpu/cpu0/topology/thread_siblings_list 2>/dev/null || echo unknown)
CPUM=$(awk -F': *' '/^model name/{print $2; exit} /^CPU part/{print "CPU part "$2; exit}' /proc/cpuinfo 2>/dev/null || echo unknown)
printf 'threads_per_core\t%s\ncpu_model\t%s\n' "$TPC" "$CPUM"

echo "== index (ENCODE alignments are already coordinate-sorted) =="
T0=$(date +%s)
samtools index -@ "$(nproc)" ENCFF933NSJ.bam > /tmp/index.log 2>&1
IRC=$?
T1=$(date +%s)
echo "index_rc	$IRC"
echo "index_wall_s	$((T1-T0))"
test "$IRC" = "0" || { cat /tmp/index.log; echo "FAIL: samtools index exited $IRC"; exit 1; }
ls -l ENCFF933NSJ.bam.bai | awk '{print "bai_bytes\t"$5}'

# A sorted BAM is a precondition for indexing at all, so a successful index IS the
# sortedness check -- no separate assertion needed.
echo "== samtools stats: the denominator for the next task's identity =="
samtools stats -@ "$(nproc)" ENCFF933NSJ.bam > /tmp/samstats.txt 2>&1
SRC=$?
test "$SRC" = "0" || { echo "FAIL: samtools stats exited $SRC"; exit 1; }

awk -F'\t' '/^SN/{gsub(/:$/,"",$2); print "sn\t"$2"\t"$3}' /tmp/samstats.txt \
  | grep -E 'raw total sequences|reads mapped|bases mapped( \(cigar\))?|average length' \
  | tee /tmp/denominator.tsv

# 'bases mapped (cigar)' is the M+I bases actually placed on the reference -- the quantity
# bamCoverage redistributes. Pull it out explicitly for the next task to assert against.
BM=$(awk -F'\t' '/^SN\tbases mapped \(cigar\)/{print $3}' /tmp/samstats.txt)
MR=$(awk -F'\t' '/^SN\treads mapped:/{print $3}' /tmp/samstats.txt)
echo "bases_mapped_cigar	$BM"
echo "reads_mapped	$MR"
test -n "$BM" && test "$BM" -gt 0 || { echo "FAIL: no mapped bases reported"; exit 1; }
printf 'bases_mapped_cigar\t%s\nreads_mapped\t%s\n' "$BM" "$MR" > /tmp/basis.tsv

df -k /tmp | awk 'NR==2{printf "tmpfs_used_mib\t%.0f\n", $3/1024}'
echo "INDEX AND STATS OK"
