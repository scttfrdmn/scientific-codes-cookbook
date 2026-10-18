#!/usr/bin/env bash
# metaphlan-real, task 0 (sra-tools image): one RODA .sra -> paired FASTQ.gz.
#
#   bash prep.sh <run accession> <ENA read_count (spots)> <ENA base_count> <threads>
#
# The .sra comes from the AWS Open Data SRA mirror (s3://sra-pub-run-odp), copied server-side into
# the bucket by stage-inputs.sh. The dump is tied back to the archive by ENA's own counts: pairs ==
# read_count (spots) and the summed read length == base_count, so a truncated dump cannot pass.
# Also counts reads >= 70 bp, MetaPhlAn's default --read_min_len, for the profile's read check.
# Writes /tmp/out/<accession>/.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
ACC=$1 WANT_PAIRS=$2 WANT_BASES=$3 T=$4
O=/tmp/out/$ACC
mkdir -p "$O"
cd /tmp
FAILS=0
row() { printf '%-40s %-28s %s\n' "$1" "$2" "$3" | tee -a "$O/prep-checks.txt"; }
check() { if [ "$1" = 1 ]; then row "$2" "$3" "ok   ($4)"; else row "$2" "$3" "FAIL ($4)"; FAILS=$((FAILS+1)); fi; }

sha256sum "$ACC.sra" | tee "$O/sra.sha256"
fasterq-dump --version 2>&1 | grep -m1 . | tee "$O/prep-versions.txt"

# The raw FASTQ and fasterq-dump scratch go on the container disk, not the /tmp tmpfs.
W=/var/tmp/prep-$ACC; mkdir -p "$W/fq"
T0=$(date +%s)
fasterq-dump --split-files --threads "$T" --temp "$W/tmp" -O "$W/fq" "./$ACC.sra" > "$O/fasterq-dump.log" 2>&1
RC=$?
T1=$(date +%s)
echo "fasterq_dump_rc $RC wall_s $((T1-T0))" | tee -a "$O/prep-times.txt"
check "$([ "$RC" = 0 ] && echo 1)" "fasterq-dump exit" "want 0" "$RC"
ls -l "$W/fq" | tee -a "$O/prep-times.txt"

# reads, bases, reads >= 70 bp and the quality alphabet of each mate, one pass each
for m in 1 2; do
  awk -v m="$m" 'NR%4==2{b+=length($0); if (length($0)>=70) g++} NR%4==0 && NR<=400000{for(i=1;i<=length($0);i++) q[substr($0,i,1)]=1}
                 END{n=0; for(k in q) n++; printf "mate%s reads %.0f bases %.0f ge70 %.0f qual_symbols_first100k %d\n", m, NR/4, b, g, n}' \
      "$W/fq/${ACC}_$m.fastq" | tee -a "$O/prep-counts.txt" &
done
wait
read -r _ _ R1 _ B1 _ G1 _ Q1 <<<"$(grep '^mate1' "$O/prep-counts.txt")"
read -r _ _ R2 _ B2 _ G2 _ Q2 <<<"$(grep '^mate2' "$O/prep-counts.txt")"
check "$([ "$R1" = "$R2" ] && echo 1)" "mates pair up" "R1 == R2" "$R1 / $R2"
check "$([ "$R1" = "$WANT_PAIRS" ] && echo 1)" "pairs == ENA read_count (spots)" "want $WANT_PAIRS" "$R1"
check "$([ $((B1+B2)) = "$WANT_BASES" ] && echo 1)" "bases == ENA base_count" "want $WANT_BASES" "$((B1+B2))"
check "$([ "$Q1" -gt 1 ] && [ "$Q2" -gt 1 ] && echo 1)" "qualities are real, not SRA-lite" "want >1 symbol" "$Q1 / $Q2"
echo "$((G1+G2))" > "$O/reads_ge70.txt"
echo "reads_ge70 $((G1+G2)) of $((R1+R2))" | tee -a "$O/prep-counts.txt"

T2=$(date +%s)
for m in 1 2; do gzip -c "$W/fq/${ACC}_$m.fastq" > "$O/${ACC}_$m.fastq.gz" & done
wait
T3=$(date +%s)
echo "gzip_wall_s $((T3-T2))" | tee -a "$O/prep-times.txt"
rm -rf "$W"
(cd "$O" && sha256sum "${ACC}_1.fastq.gz" "${ACC}_2.fastq.gz") | tee "$O/fastq.sha256"
for m in 1 2; do
  gzip -t "$O/${ACC}_$m.fastq.gz"; check "$([ $? = 0 ] && echo 1)" "mate $m gzip intact" "gzip -t" "$m"
done
echo "prep_failures $FAILS" | tee -a "$O/prep-checks.txt"
[ "$FAILS" = 0 ] || exit 1
echo "PREP OK $ACC"
