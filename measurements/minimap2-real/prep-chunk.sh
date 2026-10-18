# One chunk of the whole-genome read set: bash prep-chunk.sh NAME REGION...
# The deposit's primary records (-F 0x900: no secondary, no supplementary, so each read once) in
# REGION(s), as FASTQ for minimap2 plus pbmm2's placement of each read (name chrom start end MAPQ).
set -uo pipefail
cd /tmp
URL=https://s3.amazonaws.com/giab/data/NA12878/PacBio_SequelII_CCS_11kb/HG001_GRCh38/HG001_GRCh38.haplotag.RTG.trio.bam
N=$1; shift
fail(){ echo "FAIL $N: $*"; exit 1; }
samtools view -b -F 0x900 -@ 2 -o "part.$N.bam" "$URL" "$@" || fail "remote view"
samtools fastq -n -@ 2 "part.$N.bam" 2> "wg/$N.fastq.err" | gzip -1 > "wg/$N.fq.gz" || fail "fastq"
samtools view "part.$N.bam" | awk -F'\t' '{
  rl=0; n=""; c=$6
  for (i=1;i<=length(c);i++) { ch=substr(c,i,1)
    if (ch ~ /[0-9]/) n=n ch; else { if (ch ~ /[MD=XN]/) rl+=n; n="" } }
  printf "%s\t%s\t%s\t%.0f\t%s\n", $1, $3, $4, $4+rl-1, $5 }' > "wg/$N.pbmm2.tsv" || fail "table"
NB=$(samtools view -c "part.$N.bam")
rm -f "part.$N.bam"
NQ=$(( $(gzip -dc "wg/$N.fq.gz" | wc -l) / 4 ))
NT=$(wc -l < "wg/$N.pbmm2.tsv")
BASES=$(gzip -dc "wg/$N.fq.gz" | awk 'NR%4==2{b+=length($0)} END{printf "%.0f", b}')
test "$NB" = "$NQ" && test "$NB" = "$NT" || fail "counts differ: bam $NB fastq $NQ table $NT"
printf '%s\t%s\t%s\n' "$N" "$NB" "$BASES" > "wg/$N.count"
echo "chunk $N reads $NB bases $BASES"
