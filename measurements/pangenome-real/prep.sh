#!/usr/bin/env bash
# Task 00 (samtools image, once): turn HG002's chr20 Illumina 2x250 reads into the paired FASTQ
# Giraffe reads, and write novoalign's own placement of every read for the linear cross-check.
#   reads   ilmn.chr20.bam from measurements/phasing-real (NIST novoalign GRCh38 BAM, chr20 slice)
#   graph   HPRC v1.1 minigraph-cactus GRCh38 chr20.d9.vg, a server-side copy of the RODA object
# Everything written here is sha256-pinned in pins.sha256 for the giraffe task.
set +e
set -uo pipefail
cd /tmp
. ./lib.sh
T=${1:-8}
machine
rec samtools_version "$(samtools --version | head -1)"
chk "ilmn.chr20.bam sha256 == phasing-real's pin" "$(sha ilmn.chr20.bam)" 0a18e575f7bf30d9263dd90b457cb9a0705ea4ece9cc76e54f7a5ebea336e50a
rec graph_sha256 "$(sha chr20.d9.vg)"
rec graph_bytes "$(wc -c < chr20.d9.vg | tr -d ' ')"

# Primary records only (-F 0x900). A pair is kept when both mates are in the slice; a mate that
# novoalign placed off chr20 leaves its partner a singleton, which is counted and set aside.
tic
samtools collate -@ "$T" -O -u ilmn.chr20.bam "$W/coll" \
  | samtools fastq -@ "$T" -F 0x900 -n -1 "$O/r1.fq.gz" -2 "$O/r2.fq.gz" -s "$W/single.fq.gz" -0 "$W/other.fq.gz" - \
  2> "$O/fastq.log"
chk "collate | fastq exit" "$?" 0
toc fastq
cat "$O/fastq.log"
P1=$(gzip -dc "$O/r1.fq.gz" | awk 'END{print NR/4}')
P2=$(gzip -dc "$O/r2.fq.gz" | awk 'END{print NR/4}')
S=$(gzip -dc "$W/single.fq.gz" | awk 'END{print NR/4}')
X=$(gzip -dc "$W/other.fq.gz" | awk 'END{print NR/4}')
rec pairs "$P1"; rec singletons "$S"; rec other_reads "$X"
chk "r1 and r2 hold the same number of reads" "$P1" "$P2"
# conservation: every primary record in the BAM went to exactly one of the four outputs
NPRIM=$(samtools view -@ "$T" -c -F 0x900 ilmn.chr20.bam)
rec bam_primary_records "$NPRIM"
chk "2*pairs + singletons + other == primary records in the BAM" "$((2*P1 + S + X))" "$NPRIM"
# mates pair up by name, record for record
chk "r1 and r2 names agree record for record" \
  "$(paste <(gzip -dc "$O/r1.fq.gz" | awk 'NR%4==1') <(gzip -dc "$O/r2.fq.gz" | awk 'NR%4==1') | awk '$1!=$2' | wc -l | tr -d ' ')" 0

# novoalign's placement of every primary record, keyed by name and mate (1/2), sorted for a merge:
#   name  mate  contig  pos  mapq  ustart      (unmapped: contig "*", pos 0)
# ustart is POS less the leading clip: where the read's first base sits whatever either aligner clipped.
tic
samtools view -@ "$T" -F 0x900 ilmn.chr20.bam \
  | awk -v OFS='\t' '{m=(int($2/64)%2)?1:2; u=int($2/4)%2; c=0
      if (!u && match($6, /^[0-9]+[SH]/)) c=substr($6, 1, RLENGTH-1)+0
      print $1, m, (u?"*":$3), (u?0:$4), $5, (u?0:$4-c)}' \
  | sort -S 50% -T "$W" -k1,1 -k2,2n | gzip -1 > "$O/linear.tsv.gz"
toc linear_table
rec linear_records "$(gzip -dc "$O/linear.tsv.gz" | wc -l | tr -d ' ')"
chk "linear table holds every primary record" "$(gzip -dc "$O/linear.tsv.gz" | wc -l | tr -d ' ')" "$NPRIM"
chk "linear table has no duplicate name+mate" "$(gzip -dc "$O/linear.tsv.gz" | cut -f1,2 | uniq -d | wc -l | tr -d ' ')" 0
rec mean_read_length "$(gzip -dc "$O/r1.fq.gz" | awk 'NR%4==2{n++; s+=length($0)} END{printf "%.1f", s/n}')"

( cd "$O" && sha256sum r1.fq.gz r2.fq.gz linear.tsv.gz ) > "$W/prep.sha256"
echo "$(sha chr20.d9.vg)  chr20.d9.vg" >> "$W/prep.sha256"
mv "$W/prep.sha256" "$O/prep.sha256"
cat "$O/prep.sha256"
finish "PREP"
