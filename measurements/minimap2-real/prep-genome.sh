# Cut the whole NA12878 HiFi deposit (HG001_GRCh38.haplotag.RTG.trio.bam, GIAB) into 25 FASTQ
# chunks, one per chromosome plus one for everything else, in the recipe's samtools image.
# Chunks exist only so the alignment's SAM fits the staging tmpfs one chunk at a time; minimap2
# maps each read independently, so the chunking does not change any alignment.
set -uo pipefail
cd /tmp
fail(){ echo "FAIL: $*"; exit 1; }
mkdir -p wg
samtools --version > ver.txt; awk 'NR==1' ver.txt
: > chunks.txt
for c in $(seq 1 22) X Y; do echo "chr$c chr$c" >> chunks.txt; done
# chrM, chrEBV, the 42 *_random and 127 chrUn_* contigs, and any unplaced read ('*').
OTHER=$(awk '$1!~/^chr([0-9]+|X|Y)$/ {printf "%s ", $1}' GRCh38_no_alt_analysis_set.fa.fai)
echo "other $OTHER*" >> chunks.txt
test "$(wc -l < chunks.txt)" = 25 || fail "chunk list"
T0=$(date +%s)
xargs -P 8 -L 1 bash prep-chunk.sh < chunks.txt > chunks.log 2>&1
RC=$?
cat chunks.log
echo "extraction ${RC} in $(( $(date +%s) - T0 ))s"
test "$RC" = 0 || fail "a chunk failed"
grep -q FAIL chunks.log && fail "a chunk reported FAIL"
cut -d' ' -f1 chunks.txt > wg/chunks.order
: > wg/counts.tsv
while read -r n; do cat "wg/$n.count" >> wg/counts.tsv || fail "no count for $n"; done < wg/chunks.order
awk -F'\t' '{r+=$2; b+=$3} END{printf "total reads %.0f bases %.0f\n", r, b}' wg/counts.tsv
TOT=$(awk -F'\t' '{r+=$2} END{printf "%.0f", r}' wg/counts.tsv)
DIST=$(cat wg/*.pbmm2.tsv | cut -f1 | sort -u | wc -l)
test "$DIST" = "$TOT" || fail "$TOT reads but $DIST distinct names across chunks"
echo "distinct read names across all chunks: $DIST"
# The chr20 chunk is the recipe's FASTQ, rebuilt the same way: same records, same image, same gzip -1.
cut -f1 wg/chr20.pbmm2.tsv | sort > a.names; cut -f1 pbmm2.chr20.tsv | sort > b.names
cmp -s a.names b.names && echo "chr20 chunk has the recipe's 217281 reads" || fail "chr20 chunk read set differs from the recipe's"
sha256sum wg/*.fq.gz wg/*.pbmm2.tsv wg/counts.tsv wg/chunks.order > wg-pins.sha256
# the recipe's own FASTQ, for comparison with wg/chr20.fq.gz (same bytes expected, not required)
sha256sum hifi.chr20.fq.gz | tee -a chunks.log
cat wg-pins.sha256
echo "PREP GENOME OK"
