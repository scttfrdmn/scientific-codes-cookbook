#!/usr/bin/env bash
# bismark-real, staging task P0 (cutadapt image, arm64, once): the archive's 2011 GAIIx reads in a
# form today's aligners take, trimmed the way Trim Galore trims them.
#
#   bash prep-trim.sh <cores>
#
# ENCFF000LYO is Illumina 1.5 encoding: Phred+64 qualities, '.' for a no-call, 'B' (Q2) tails.
# Re-encoded to Phred+33 with 'N' for '.', base for base, then cutadapt with Trim Galore's
# defaults: Q20 3' quality trim, the Illumina adapter, reads under 20 bp dropped. (Trim Galore's
# --rrbs 2 bp rule applies only to adapter-trimmed reads, which 36 bp reads of >=40 bp MspI
# fragments seldom are; the count is in the report.) Both aligners read the one output file.
# Input /tmp/ENCFF000LYO.fastq.gz; writes /tmp/out/.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
export TMPDIR=/var/tmp HOME=/tmp
J=$1
O=/tmp/out
mkdir -p "$O"
cd /tmp
FAILS=0
row() { printf '%-44s %-30s %s\n' "$1" "$2" "$3" | tee -a "$O/trim-checks.txt"; }
check() { if [ "$1" = 1 ]; then row "$2" "$3" "ok   ($4)"; else row "$2" "$3" "FAIL ($4)"; FAILS=$((FAILS+1)); fi; }
echo "cutadapt $(cutadapt --version)" | tee "$O/trim-versions.txt"
MD5=$(md5sum ENCFF000LYO.fastq.gz | cut -d' ' -f1)
check "$([ "$MD5" = 11c5c6b097a490a4020cef6e16713bfe ] && echo 1)" "FASTQ md5 == ENCODE portal md5sum" "11c5c6b0..." "$MD5"

T0=$(date +%s)
gzip -dc ENCFF000LYO.fastq.gz | python3 -u -c '
import sys
q = bytes.maketrans(bytes(range(64, 127)), bytes(range(33, 96)))
n = bad = 0
inp, out = sys.stdin.buffer, sys.stdout.buffer
while True:
    h = inp.readline()
    if not h:
        break
    s, p, ql = inp.readline(), inp.readline(), inp.readline()
    if min(ql.rstrip(b"\n")) < 64:
        bad += 1
    out.write(h + s.replace(b".", b"N") + b"+\n" + ql.translate(q))
    n += 1
sys.stderr.write("reencoded_reads %d below_phred64_floor %d\n" % (n, bad))
' 2> "$O/reencode.txt" | cutadapt -j "$J" -q 20 -a AGATCGGAAGAGC -m 20 -o "$O/ENCFF000LYO.trimmed.fastq.gz" - > "$O/cutadapt.log" 2>&1
RC="${PIPESTATUS[0]}${PIPESTATUS[1]}${PIPESTATUS[2]}"
T1=$(date +%s)
echo "trim_rc $RC trim_wall_s $((T1-T0))" | tee "$O/trim-times.txt"
cat "$O/reencode.txt"
check "$([ "$RC" = 000 ] && echo 1)" "decode | re-encode | cutadapt exit" "000" "$RC"
read -r _ NRE _ BAD < "$O/reencode.txt"
check "$([ "$NRE" = 42372813 ] && echo 1)" "re-encoded reads == ENCODE read_count" "42372813" "$NRE"
check "$([ "$BAD" = 0 ] && echo 1)" "no quality below '@' (so it IS Phred+64)" "0" "$BAD"
# cutadapt's own accounting: processed == written + too short
num() { awk -F': *' -v k="$1" '$1==k{gsub(/,/,"",$2); split($2,a," "); print a[1]; exit}' "$O/cutadapt.log"; }
PROC=$(num "Total reads processed"); WR=$(num "Reads written (passing filters)"); SH=$(num "Reads that were too short")
ADP=$(num "Reads with adapters")
check "$([ "$PROC" = 42372813 ] && [ $((WR+SH)) = "$PROC" ] && echo 1)" "cutadapt: written + too short == processed" "ACCOUNTING" "$WR + $SH = $PROC"
echo "reads_processed $PROC written $WR too_short $SH with_adapter $ADP" | tee -a "$O/trim-times.txt"
gzip -dc "$O/ENCFF000LYO.trimmed.fastq.gz" | awk 'NR%4==2{n++; b+=length($0); if (index($0,".")) dot++} END{printf "out_reads %.0f out_bases %.0f reads_with_dot %d\n", n, b, dot}' | tee -a "$O/trim-times.txt"
OUTN=$(awk '$1=="out_reads"{print $2}' "$O/trim-times.txt")
check "$([ "$OUTN" = "$WR" ] && echo 1)" "reads in the output == cutadapt's written" "$WR" "$OUTN"
( cd "$O" && sha256sum ENCFF000LYO.trimmed.fastq.gz ) | tee "$O/trimmed.sha256"
echo "prep_failures $FAILS" | tee -a "$O/trim-checks.txt"
[ "$FAILS" = 0 ] || exit 1
echo "TRIM OK"
