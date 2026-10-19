#!/usr/bin/env bash
# spades-real, task 0 (sra-tools image): the RODA .sra -> paired FASTQ.gz, plus the closed reference.
#
#   bash prep.sh <run accession> <ENA read_count (spots)> <ENA base_count> <threads>
#
# The .sra comes from the AWS Open Data SRA mirror (s3://sra-pub-run-odp), copied server-side into
# the bucket by stage-inputs.sh. The dump is tied back to the archive by ENA's own counts: pairs ==
# read_count and summed read length == base_count, so a truncated dump cannot pass. The reference is
# NCBI RefSeq GCF_000005845.2 (E. coli K-12 MG1655, U00096.3), checked against NCBI's own
# md5checksums.txt. Writes /tmp/out/.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
ACC=$1 WANT_PAIRS=$2 WANT_BASES=$3 T=$4
O=/tmp/out
mkdir -p "$O"
cd /tmp
FAILS=0
row() { printf '%-40s %-28s %s\n' "$1" "$2" "$3" | tee -a "$O/prep-checks.txt"; }
check() { if [ "$1" = 1 ]; then row "$2" "$3" "ok   ($4)"; else row "$2" "$3" "FAIL ($4)"; FAILS=$((FAILS+1)); fi; }

echo "== reference: NCBI GCF_000005845.2 =="
R=https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/000/005/845/GCF_000005845.2_ASM584v2
curl -fsS "$R/GCF_000005845.2_ASM584v2_genomic.fna.gz" -o "$O/GCF_000005845.2.fna.gz"
curl -fsS "$R/md5checksums.txt" -o "$O/ncbi-md5checksums.txt"
WANT_MD5=$(awk '$2=="./GCF_000005845.2_ASM584v2_genomic.fna.gz"{print $1}' "$O/ncbi-md5checksums.txt")
GOT_MD5=$(md5sum "$O/GCF_000005845.2.fna.gz" | awk '{print $1}')
check "$([ -n "$WANT_MD5" ] && [ "$GOT_MD5" = "$WANT_MD5" ] && echo 1)" "reference md5 == NCBI md5checksums" "want $WANT_MD5" "$GOT_MD5"
REFLEN=$(gzip -dc "$O/GCF_000005845.2.fna.gz" | awk '/^>/{n++; next}{b+=length($0)}END{print n, b}')
check "$([ "$REFLEN" = "1 4641652" ] && echo 1)" "reference is one 4,641,652 bp sequence" "want 1 4641652" "$REFLEN"

sha256sum "$ACC.sra" | tee "$O/sra.sha256"
fasterq-dump --version 2>&1 | grep -m1 . | tee "$O/prep-versions.txt"
W=/var/tmp/prep-$ACC; mkdir -p "$W/fq"
T0=$(date +%s)
fasterq-dump --split-files --threads "$T" --temp "$W/tmp" -O "$W/fq" "./$ACC.sra" > "$O/fasterq-dump.log" 2>&1
RC=$?
T1=$(date +%s)
echo "fasterq_dump_rc $RC wall_s $((T1-T0))" | tee -a "$O/prep-times.txt"
check "$([ "$RC" = 0 ] && echo 1)" "fasterq-dump exit" "want 0" "$RC"
ls -l "$W/fq" | tee -a "$O/prep-times.txt"

for m in 1 2; do
  awk -v m="$m" 'NR%4==2{b+=length($0); if (length($0)>mx) mx=length($0)} NR%4==0 && NR<=400000{for(i=1;i<=length($0);i++) q[substr($0,i,1)]=1}
                 END{n=0; for(k in q) n++; printf "mate%s reads %.0f bases %.0f maxlen %d qual_symbols_first100k %d\n", m, NR/4, b, mx, n}' \
      "$W/fq/${ACC}_$m.fastq" | tee -a "$O/prep-counts.txt" &
done
wait
read -r _ _ R1 _ B1 _ L1 _ Q1 <<<"$(grep '^mate1' "$O/prep-counts.txt")"
read -r _ _ R2 _ B2 _ L2 _ Q2 <<<"$(grep '^mate2' "$O/prep-counts.txt")"
check "$([ "$R1" = "$R2" ] && echo 1)" "mates pair up" "R1 == R2" "$R1 / $R2"
check "$([ "$R1" = "$WANT_PAIRS" ] && echo 1)" "pairs == ENA read_count (spots)" "want $WANT_PAIRS" "$R1"
check "$([ $((B1+B2)) = "$WANT_BASES" ] && echo 1)" "bases == ENA base_count" "want $WANT_BASES" "$((B1+B2))"
check "$([ "$Q1" -gt 1 ] && [ "$Q2" -gt 1 ] && echo 1)" "qualities are real, not SRA-lite" "want >1 symbol" "$Q1 / $Q2"
echo "$R1" > "$O/pairs.txt"

for m in 1 2; do gzip -n -c "$W/fq/${ACC}_$m.fastq" > "$O/${ACC}_$m.fastq.gz" & done
wait
rm -rf "$W"
(cd "$O" && sha256sum "${ACC}_1.fastq.gz" "${ACC}_2.fastq.gz" GCF_000005845.2.fna.gz pairs.txt) | tee "$O/staged.sha256"
for m in 1 2; do
  gzip -t "$O/${ACC}_$m.fastq.gz"; check "$([ $? = 0 ] && echo 1)" "mate $m gzip intact" "gzip -t" "$m"
done
echo "prep_failures $FAILS" | tee -a "$O/prep-checks.txt"
[ "$FAILS" = 0 ] || exit 1
echo "PREP OK $ACC"
