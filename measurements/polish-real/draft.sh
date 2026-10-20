# The draft every polisher starts from: Flye on the whole run, once, on Graviton4.
#   bash draft.sh <threads>
# --asm-coverage 50 is what ONT's own wf-bacterial-genomes passes (flye_asm_coverage 50): at ~490x
# Flye builds its disjointigs from the longest 50x and then uses every read. -g is required with it.
T=${1:?threads}
. /tmp/common.sh
O=/var/tmp/out; mkdir -p $O
host_info $O/host.txt
flye --version > $O/flye-version.txt 2>&1
python3 -u /tmp/run.py $O/flye-time.txt flye --nano-hq /tmp/ERR12813217.fastq.gz -g 5.2m \
  --asm-coverage 50 -t "$T" -o /var/tmp/flye > $O/flye.stdout 2>&1
cp /var/tmp/flye/flye.log /var/tmp/flye/assembly_info.txt $O/ 2>/dev/null
cp /var/tmp/flye/assembly.fasta $O/draft.fa 2>/dev/null
cat $O/flye-time.txt
# Completion sentinel: "Final assembly:" is the last line of Flye's JobFinalize (recipes/flye).
DONE=$(grep -c 'INFO: Final assembly:' $O/flye.log)
N=$(grep -c '^>' $O/draft.fa)
{
  echo "flye_done $DONE"
  echo "contigs $N"
  awk 'NR>1{print "contig " $1 " len " $2 " cov " $3 " circular " $4}' $O/assembly_info.txt
  echo "draft_sha256 $(sha256sum $O/draft.fa | awk '{print $1}')"
} | tee $O/checks.txt
[ "$DONE" -ge 1 ] && [ "$N" -ge 1 ] || { echo "FAIL: Flye did not finish an assembly"; exit 1; }
echo "DRAFT OK"
