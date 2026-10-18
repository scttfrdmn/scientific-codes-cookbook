# Build the two derived inputs of measurements/minimap2-real, in the recipe's samtools image.
#  1. GRCh38_no_alt_analysis_set.fa -- the 195 sequences of the 1000 Genomes GRCh38 analysis set
#     that are not *_alt, HLA-* or *_decoy: the primary assembly, chrM, chrEBV and the
#     unlocalised/unplaced contigs. That is the reference GIAB aligned this HiFi deposit to.
#  2. pbmm2.chr20.tsv -- where the depositors' pbmm2 placed each of the reads (name, chrom, start,
#     end, MAPQ), from the same -F 0x900 records the recipe's FASTQ was extracted from.
set -uo pipefail
cd /tmp
fail(){ echo "FAIL: $*"; exit 1; }
FULL=GRCh38_full_analysis_set_plus_decoy_hla.fa
NOALT=GRCh38_no_alt_analysis_set.fa
URL=https://s3.amazonaws.com/giab/data/NA12878/PacBio_SequelII_CCS_11kb/HG001_GRCh38/HG001_GRCh38.haplotag.RTG.trio.bam
samtools --version > ver.txt; awk 'NR==1' ver.txt

echo "== no-alt reference =="
awk '$1!~/_alt$/ && $1!~/^HLA-/ && $1!~/_decoy$/ {print $1}' $FULL.fai > noalt.names
N=$(wc -l < noalt.names)
test "$N" = 195 || fail "expected the 195 no-alt sequences, got $N"
samtools faidx "$FULL" -r noalt.names -o "$NOALT" || fail "faidx extract"
samtools faidx "$NOALT" || fail "faidx index"
NSEQ=$(wc -l < "$NOALT.fai")
LEN=$(awk '{s+=$2} END{printf "%.0f", s}' "$NOALT.fai")
echo "sequences $NSEQ  bases $LEN"
test "$NSEQ" = 195 || fail "no-alt fai has $NSEQ sequences"
# 26 primary (chr1-22,X,Y,M,EBV) + 42 *_random + 127 chrUn_*: 3,088,458,224 + 6,978,808 + 4,485,509
test "$LEN" = 3099922541 || fail "no-alt length $LEN"

echo "== pbmm2's placements of the chr20 reads =="
samtools view -F 0x900 "$URL" chr20 > pb.sam || fail "remote view"
awk -F'\t' '{
  rl=0; n=""; c=$6
  for (i=1;i<=length(c);i++) { ch=substr(c,i,1)
    if (ch ~ /[0-9]/) n=n ch; else { if (ch ~ /[MD=XN]/) rl+=n; n="" } }
  printf "%s\t%s\t%s\t%.0f\t%s\n", $1, $3, $4, $4+rl-1, $5 }' pb.sam > pbmm2.chr20.tsv
rm -f pb.sam
NR_=$(wc -l < pbmm2.chr20.tsv)
cut -f1 pbmm2.chr20.tsv | sort -u > names.u
NU=$(wc -l < names.u)
echo "pbmm2 records $NR_  distinct names $NU"
test "$NR_" = 217281 || fail "pbmm2 table has $NR_ records, the FASTQ has 217281 reads"
test "$NU" = 217281 || fail "duplicate read names in the pbmm2 table"

sha256sum "$NOALT" "$NOALT.fai" pbmm2.chr20.tsv hifi.chr20.fq.gz "$FULL" > prep-pins.sha256
cat prep-pins.sha256
echo "PREP OK"
