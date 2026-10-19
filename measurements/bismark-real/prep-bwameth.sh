#!/usr/bin/env bash
# bismark-real, staging task P2 (bwa-meth image, arm64, once): bwa-meth's C->T converted GRCh38
# and its bwa index. bwa index is single-threaded, so this runs on a small box, beside P1.
#
#   bash prep-bwameth.sh
#
# Input in /tmp: GRCh38_no_alt_analysis_set.fa (minimap2-real's, sha256 in pins.sha256). The
# conversion doubles the reference (an f- and an r- copy of every contig, 6.2 Gbp), and the build
# runs on the container disk (/var/tmp); only the five index files the aligner reads come back to
# /tmp/out/bwameth/ for stage-out. Writes /tmp/out/.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
export TMPDIR=/var/tmp HOME=/tmp
O=/tmp/out
mkdir -p "$O/bwameth"
cd /tmp
FAILS=0
row() { printf '%-44s %-30s %s\n' "$1" "$2" "$3" | tee -a "$O/prep-bwameth-checks.txt"; }
check() { if [ "$1" = 1 ]; then row "$2" "$3" "ok   ($4)"; else row "$2" "$3" "FAIL ($4)"; FAILS=$((FAILS+1)); fi; }

bwameth.py --version 2>&1 | grep -v Warning | grep -m1 . | tee "$O/prep-bwameth-versions.txt"
bwa 2>&1 | grep -m1 Version | tee -a "$O/prep-bwameth-versions.txt"

W=/var/tmp/bwm; mkdir -p "$W"
cp GRCh38_no_alt_analysis_set.fa "$W/ref.fa"
( while : ; do echo "$(date +%s) $(awk '/^anon /{print $2; exit}' /sys/fs/cgroup/memory.stat 2>/dev/null)" >> "$O/prep-bwameth-samples.txt"; sleep 10; done ) &
SPID=$!
T0=$(date +%s)
bwameth.py index "$W/ref.fa" > "$O/bwameth-index.log" 2>&1
RC=$?
T1=$(date +%s)
kill $SPID 2>/dev/null
echo "index_rc $RC index_wall_s $((T1-T0))" | tee "$O/prep-bwameth-times.txt"
awk '$2>m{m=$2} END{printf "peak_anon_gib %.2f\n", m/1073741824}' "$O/prep-bwameth-samples.txt" | tee -a "$O/prep-bwameth-times.txt"
check "$([ "$RC" = 0 ] && echo 1)" "bwameth.py index exit" "want 0" "$RC"
[ "$RC" = 0 ] || tail -40 "$O/bwameth-index.log"
# the converted reference holds an f- and an r- copy of each of the 195 contigs
NC=$(grep -c '^>' "$W/ref.fa.bwameth.c2t")
check "$([ "$NC" = 390 ] && echo 1)" "c2t contigs" "want 390 (2 x 195)" "$NC"
for x in amb ann bwt pac sa; do
  mv "$W/ref.fa.bwameth.c2t.$x" "$O/bwameth/" || { echo "FAIL: no .$x"; FAILS=$((FAILS+1)); }
done
( cd "$O/bwameth" && sha256sum ref.fa.bwameth.c2t.* ) | tee "$O/bwameth-index.sha256"
du -sh "$O/bwameth" | tee -a "$O/prep-bwameth-times.txt"
rm -rf "$W"
echo "prep_failures $FAILS" | tee -a "$O/prep-bwameth-checks.txt"
[ "$FAILS" = 0 ] || exit 1
echo "PREP OK"
