#!/usr/bin/env bash
# measurements/rnaseq-counts-real task 0 (STAR image, Graviton4, run once): build the input the
# counters read. The complete ERR188026 run aligned against the published GRCh38 + Ensembl 116
# STAR index, unsorted (mates adjacent, every alignment of a read together -- what HTSeq's
# `-r name` needs), unmapped pairs kept in the BAM so every input pair is in it, and STAR's own
# per-gene counts (--quantMode GeneCounts) as the third counter on the same alignments.
#   align.sh <threads>
set +e
set -uo pipefail
T=${1:?threads}
export PATH=/opt/conda/bin:$PATH HOME=/tmp TMPDIR=/tmp
O=/tmp/out; W=/var/tmp/star
mkdir -p $O $W
fail() { echo "FAIL: $*" | tee -a $O/pins.txt; exit 1; }

echo "== staged inputs against their publishers' checksums =="
# ENA's fastq_md5 for ERR188026, and Ensembl release-116's CHECKSUMS (BSD `sum`) for the GTF.
M1=$(md5sum /tmp/ERR188026_1.fastq.gz | cut -d' ' -f1)
M2=$(md5sum /tmp/ERR188026_2.fastq.gz | cut -d' ' -f1)
GS=$(sum /tmp/GRCh38.116.gtf.gz | awk '{print $1, $2}')
{
  echo "ERR188026_1.fastq.gz md5 $M1 want 5fd42c5e9889ac6f30259c04848c8ffb (ENA)"
  echo "ERR188026_2.fastq.gz md5 $M2 want 2f03cfb4d272170d0d6451523e37569c (ENA)"
  echo "GRCh38.116.gtf.gz sum $GS want 49151 137815 (Ensembl release-116 CHECKSUMS)"
} > $O/pins.txt
cat $O/pins.txt
[ "$M1" = 5fd42c5e9889ac6f30259c04848c8ffb ] || fail "mate 1 md5"
[ "$M2" = 2f03cfb4d272170d0d6451523e37569c ] || fail "mate 2 md5"
[ "$GS" = "49151 137815" ] || fail "GTF sum"
( cd /tmp/idx && sha256sum Genome SA SAindex sjdbList.out.tab geneInfo.tab exonGeTrInfo.tab ) > $O/index.sha256

echo "== STAR: the complete run, $T threads =="
T0=$(date +%s)
STAR --runThreadN "$T" --genomeDir /tmp/idx \
     --readFilesIn /tmp/ERR188026_1.fastq.gz /tmp/ERR188026_2.fastq.gz --readFilesCommand zcat \
     --outSAMtype BAM Unsorted --outSAMunmapped Within --quantMode GeneCounts \
     --outTmpDir $W/tmp --outFileNamePrefix $O/ > $O/star.stdout 2>&1
RC=$?
T1=$(date +%s)
echo "star_rc $RC wall_s $(( T1 - T0 ))" | tee $O/timing.txt
STAR --version >> $O/timing.txt
[ "$RC" -eq 0 ] || { tail -n 30 $O/star.stdout $O/Log.out; fail "STAR exited $RC"; }
cat $O/Log.final.out
( cd $O && sha256sum Aligned.out.bam ReadsPerGene.out.tab Log.final.out ) > $O/outputs.sha256
( cd /tmp && sha256sum GRCh38.116.gtf.gz ) >> $O/outputs.sha256
cat $O/outputs.sha256
IN=$(awk -F'|' '/Number of input reads/{gsub(/[ \t]/,"",$2); print $2}' $O/Log.final.out)
[ "$IN" = 15800127 ] || fail "STAR read $IN pairs, ENA lists 15800127"
# STAR's own accounting, recorded here and asserted by the counting tasks
awk '{for(i=2;i<=4;i++) s[i]+=$i} END{for(i=2;i<=4;i++) print "readspergene_col" i "_sum", s[i]}' \
  $O/ReadsPerGene.out.tab | tee -a $O/pins.txt
echo "ALIGN OK"
