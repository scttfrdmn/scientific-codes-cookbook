# The read-to-draft overlaps racon consumes: minimap2, once, on Graviton4, so every chip's racon
# reads the same PAF bytes.   bash overlap.sh <threads>
T=${1:?threads}
. /tmp/common.sh
O=/var/tmp/out; mkdir -p $O
host_info $O/host.txt
minimap2 --version > $O/minimap2-version.txt
T0=$(date +%s)
minimap2 -x map-ont -t "$T" /tmp/draft.fa /tmp/ERR12813217.fastq.gz > $O/ov.paf 2> $O/minimap2.log
RC=$?; T1=$(date +%s)
NPAF=$(awk 'END{print NR+0}' $O/ov.paf)
NQ=$(cut -f1 $O/ov.paf | sort -u | awk 'END{print NR+0}')
{
  echo "minimap2_rc $RC"
  echo "minimap2_wall_s $(( T1 - T0 ))"
  grep 'Peak RSS' $O/minimap2.log | tail -1 | sed 's/^/minimap2_self_report /'
  echo "paf_lines $NPAF"
  echo "reads_with_an_overlap $NQ of 397033"
} | tee $O/checks.txt
[ "$RC" = 0 ] && [ "$NQ" -ge 300000 ] || { echo "FAIL: minimap2 failed, or most reads did not align to the draft"; exit 1; }
echo "OVERLAP OK"
