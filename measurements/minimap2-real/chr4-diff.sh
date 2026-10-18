# Which chr4 records differ across chips? bash chr4-diff.sh   (the genome run's chr4 chunk, 32 threads)
# Writes chr4.nosq.sam.gz -- every alignment record minus SEQ/QUAL -- and, when the other chip's
# copy is staged as other.nosq.sam.gz, the differing record pairs. Also re-hashes the full body,
# so the same chip's genome run says whether the chunk is reproducible run to run.
set -uo pipefail
cd /tmp
fail(){ echo "FAIL: $*"; exit 1; }
uname -m
minimap2 -ax map-hifi -t 32 --MD GRCh38_no_alt_analysis_set.fa wg/chr4.fq.gz > c.sam 2> mm.log || fail minimap2
tail -1 mm.log
echo "body_md5 $(grep -v '^@' c.sam | md5sum | awk '{print $1}')"
grep -v '^@' c.sam | awk -F'\t' 'BEGIN{OFS="\t"} {$10="."; $11="."; print}' | gzip -1 > chr4.nosq.sam.gz
rm -f c.sam
if [ -s other.nosq.sam.gz ]; then
  gzip -dc other.nosq.sam.gz > o.txt
  gzip -dc chr4.nosq.sam.gz > m.txt
  echo "lines here $(wc -l < m.txt) other $(wc -l < o.txt)"
  awk -F'\t' 'NR==FNR{o[FNR]=$0; next} $0!=o[FNR]{n++; if(n<=60){print "OTHER " o[FNR]; print "HERE  " $0}} END{print "differing_records " n+0}' o.txt m.txt > diff.txt
  tail -1 diff.txt
fi
echo DIAG OK
