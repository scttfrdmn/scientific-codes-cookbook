#!/usr/bin/env bash
# bismark-real, staging task P1 (Bismark image, arm64, once): check the two staged inputs against
# their published identities, then build Bismark's CT + GA bowtie2 indices of GRCh38.
#
#   bash prep-bismark.sh <threads per indexer>
#
# Inputs in /tmp: GRCh38_no_alt_analysis_set.fa(.fai) (minimap2-real's, sha256 in pins.sha256) and
# ENCFF000LYO.fastq.gz (ENCODE, md5 11c5c6b0... from the ENCODE portal). The index is built once
# and shared by every chip, as a cohort would share it. Writes /tmp/out/.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
export TMPDIR=/tmp HOME=/tmp
T=$1
O=/tmp/out
mkdir -p "$O"
cd /tmp
FAILS=0
row() { printf '%-44s %-30s %s\n' "$1" "$2" "$3" | tee -a "$O/prep-checks.txt"; }
check() { if [ "$1" = 1 ]; then row "$2" "$3" "ok   ($4)"; else row "$2" "$3" "FAIL ($4)"; FAILS=$((FAILS+1)); fi; }

bismark --version 2>&1 | grep -m1 . | tee "$O/prep-versions.txt"
bowtie2 --version 2>&1 | grep -m1 version | tee -a "$O/prep-versions.txt"

echo "== the reads: ENCODE's md5, the archive's read count, and what the library looks like =="
MD5=$(md5sum ENCFF000LYO.fastq.gz | cut -d' ' -f1)
check "$([ "$MD5" = 11c5c6b097a490a4020cef6e16713bfe ] && echo 1)" "FASTQ md5 == ENCODE portal md5sum" "11c5c6b0..." "$MD5"
sha256sum ENCFF000LYO.fastq.gz | tee "$O/fastq.sha256"
# reads, length histogram, quality alphabet, and reads carrying the Illumina adapter (13 bp,
# Trim Galore's) -- that last one is what says whether this library needs adapter trimming
gzip -dc ENCFF000LYO.fastq.gz | awk '
  NR%4==2 { n++; L[length($0)]++; if (index($0, "AGATCGGAAGAGC")) ad++; if (index($0, "N")) nn++ }
  NR%4==0 && NR<=4000000 { for (i=1; i<=length($0); i++) q[substr($0,i,1)]=1 }
  END { printf "reads %d\nreads_with_adapter %d\nreads_with_N %d\n", n, ad, nn
        for (l in L) printf "length %d %d\n", l, L[l]
        s=""; for (c in q) s=s c; printf "qual_symbols_first1M %s\n", s }' > "$O/fastq-stats.txt"
cat "$O/fastq-stats.txt"
NREADS=$(awk '$1=="reads"{print $2}' "$O/fastq-stats.txt")
check "$([ "$NREADS" = 42372813 ] && echo 1)" "reads == ENCODE read_count" "want 42372813" "$NREADS"

echo "== GRCh38: Bismark CT + GA indices =="
mkdir -p genome
# a link, not a move: the staged file belongs to the instance user, and /tmp is sticky
ln -s /tmp/GRCh38_no_alt_analysis_set.fa genome/GRCh38_no_alt_analysis_set.fa
( while : ; do echo "$(date +%s) $(awk '/^anon /{print $2; exit}' /sys/fs/cgroup/memory.stat 2>/dev/null) $(df -k /tmp | awk 'NR==2{print $3}')" >> "$O/prep-samples.txt"; sleep 10; done ) &
SPID=$!
T0=$(date +%s)
bismark prepare --bowtie2 --parallel "$T" genome > "$O/prepare.log" 2>&1
RC=$?
T1=$(date +%s)
kill $SPID 2>/dev/null
echo "prepare_rc $RC prepare_wall_s $((T1-T0))" | tee "$O/prep-times.txt"
awk '$2>m{m=$2} $3>d{d=$3} END{printf "peak_anon_gib %.2f peak_tmpfs_gib %.2f\n", m/1073741824, d/1048576}' "$O/prep-samples.txt" | tee -a "$O/prep-times.txt"
check "$([ "$RC" = 0 ] && echo 1)" "bismark prepare exit" "want 0" "$RC"
[ "$RC" = 0 ] || tail -40 "$O/prepare.log"
NBT2=$(find genome/Bisulfite_Genome -name '*.bt2' | wc -l)
check "$([ "$NBT2" = 12 ] && echo 1)" "bowtie2 index files" "want 12 (6 CT + 6 GA)" "$NBT2"
# the converted FASTAs are only the indexer's input; the aligner reads the .bt2 files
find genome/Bisulfite_Genome -name '*.fa' -exec rm -f {} +
# staged out in place (the spec's output is /tmp/genome/Bisulfite_Genome/), not copied: a copy
# would hold the index twice in the tmpfs
( cd genome && find Bisulfite_Genome -type f | sort | xargs sha256sum ) | tee "$O/index.sha256"
du -sh genome/Bisulfite_Genome | tee -a "$O/prep-times.txt"
echo "prep_failures $FAILS" | tee -a "$O/prep-checks.txt"
[ "$FAILS" = 0 ] || exit 1
echo "PREP OK"
