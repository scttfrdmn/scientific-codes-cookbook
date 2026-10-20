#!/usr/bin/env bash
# measurements/transcript-assembly-real task 1 (samtools image, Graviton4, run once): sort and
# index hisat2's alignment, and read off the two things the StringTie checks need -- every
# junction any mapped read spans (the CIGAR N operations), and the chromosome lengths.
#   sort.sh <threads>
set +e
set -uo pipefail
T=${1:?threads}
export PATH=/opt/conda/bin:$PATH HOME=/tmp TMPDIR=/tmp LC_ALL=C
O=/tmp/out; W=/var/tmp/srt
mkdir -p $O $W
fail() { echo "FAIL: $*" | tee -a $O/check.txt; exit 1; }
echo "samtools	$(samtools --version | head -n 1)" > $O/host.txt
( cd /tmp && sha256sum -c sam.sha256 ) || fail "staged SAM does not match align's sha256"

t0=$(date +%s)
samtools sort -@ "$T" -m 1500M -T $W/s -o $O/ERR188026.hisat2.bam /tmp/aln.sam > $O/sort.log 2>&1 \
  || { cat $O/sort.log; fail "samtools sort"; }
samtools index -@ "$T" $O/ERR188026.hisat2.bam || fail "samtools index"
t1=$(date +%s)
echo "sort_index_wall_s	$(( t1 - t0 ))" > $O/timing.txt
samtools flagstat -@ "$T" $O/ERR188026.hisat2.bam > $O/flagstat.txt
cat $O/flagstat.txt
samtools view -H $O/ERR188026.hisat2.bam | awk -F'\t' '$1=="@SQ"{sub("SN:","",$2); sub("LN:","",$3); print $2"\t"$3}' > $O/chrom.sizes

# every distinct intron a mapped alignment (primary or secondary) spans; StringTie sees all of them
samtools view -F 4 $O/ERR188026.hisat2.bam | awk -F'\t' '$6 ~ /N/ { g=$4; c=$6
  while (match(c,/^[0-9]+[MIDNSHP=X]/)) { L=substr(c,1,RLENGTH-1)+0; op=substr(c,RLENGTH,1)
    if (op=="N") print $3"\t"g"\t"(g+L-1)
    if (op=="M"||op=="D"||op=="N"||op=="="||op=="X") g+=L
    c=substr(c,RLENGTH+1) } }' | sort -S 4G -T $W -u > $O/junctions.tsv
t2=$(date +%s)
echo "junctions_wall_s	$(( t2 - t1 ))" >> $O/timing.txt

PRIM=$(awk '/primary$/{print $1}' $O/flagstat.txt)
NJ=$(wc -l < $O/junctions.tsv | tr -d ' ')
NSQ=$(wc -l < $O/chrom.sizes | tr -d ' ')
{
  echo "primary_records $PRIM   want == 2 x 15800127 = 31600254 (EXACT: every read once, as hisat2 was given it)"
  echo "distinct_junctions $NJ"
  echo "sequences $NSQ"
} | tee $O/check.txt
[ "$PRIM" = 31600254 ] || fail "primary records $PRIM"
[ "$NJ" -gt 100000 ] || fail "only $NJ junctions"
( cd $O && sha256sum ERR188026.hisat2.bam ERR188026.hisat2.bam.bai junctions.tsv chrom.sizes ) > $O/inputs.sha256
cat $O/inputs.sha256
echo "SORT OK"
