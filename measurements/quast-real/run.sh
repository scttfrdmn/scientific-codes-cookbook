#!/usr/bin/env bash
# quast-real, task 1 (QUAST image): one QUAST run over fourteen whole-genome E. coli K-12 MG1655
# assemblies against the closed reference U00096.3 and its NCBI annotation -- nine published
# GenBank genomes, spades-real's contigs and scaffolds, and three planted-truth controls built from
# the reference -- then check.py asserts the claims.
#
#   bash run.sh <threads> <assemblies.tsv>
#
# Runs on aarch.bio's arm64 image and bioconda's x86 one (BusyBox userland), so the glue is POSIX
# sh/awk and the measuring is python3 (QUAST's own interpreter). Peak RSS is ru_maxrss of QUAST's
# largest descendant; the cgroup's memory.peak is recorded beside it. Writes /tmp/out/.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
export TMPDIR=/tmp HOME=/tmp
T=$1 LIST=$2
O=/tmp/out
mkdir -p "$O"
cd /tmp
CG=/sys/fs/cgroup
REF=GCF_000005845.2.fna.gz

{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  echo "mem_total_kib $(awk '/^MemTotal/{print $2}' /proc/meminfo)"
  echo "quast_version $(quast.py --version 2>&1 | tail -1)"
  echo "minimap2_version $(ls /opt/conda/lib/python*/site-packages/quast_libs/minimap2/ 2>/dev/null | head -1) $(minimap2 --version 2>/dev/null)"
  echo "python $(python3 --version 2>&1)"
} > "$O/host.txt"
awk '/^online_cpus/{c=$2} /^physical_cores/{p=$2} END{printf "threads_per_core %d\n", (p>0)?c/p:0}' "$O/host.txt" >> "$O/host.txt"
cat "$O/host.txt"

python3 -I -u mkcontrols.py "$REF" controls > "$O/mkcontrols.log" 2>&1 || { echo "FAIL: mkcontrols"; cat "$O/mkcontrols.log"; exit 1; }
cp controls/truth.json "$O/"
gzip -dc GCF_000005845.2.gff.gz > ref.gff

FILES="controls/self.fasta controls/planted.fasta controls/chimera.fasta spades_contigs.fasta spades_scaffolds.fasta"
LABELS="self,planted,chimera,spades_contigs,spades_scaffolds"
grep -v '^#' "$LIST" | cut -f1 > labels.txt
while read -r l; do FILES="$FILES $l.fna.gz"; LABELS="$LABELS,$l"; done < labels.txt
(sha256sum $FILES) > "$O/inputs.sha256"

echo "== quast.py: $(echo $FILES | wc -w) assemblies vs U00096.3, -t $T =="
U0=$(awk '/^usage_usec/{print $2}' $CG/cpu.stat 2>/dev/null)
# shellcheck disable=SC2086
python3 -I -u -c '
import resource, subprocess, sys, time
t0 = time.time()
rc = subprocess.call(sys.argv[1:], stdout=open("/tmp/out/quast.stdout", "w"), stderr=subprocess.STDOUT)
w = time.time() - t0
print("quast_rc %d\nquast_wall_s %.1f\nquast_peak_rss_kib %d" % (rc, w, resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss))
sys.exit(rc)' quast.py $FILES -r "$REF" -g ref.gff -t "$T" -o q --labels "$LABELS" | tee "$O/times.txt"
RC=${PIPESTATUS[0]}
U1=$(awk '/^usage_usec/{print $2}' $CG/cpu.stat 2>/dev/null)
W=$(awk '/^quast_wall_s/{print $2}' "$O/times.txt")
awk -v du="$(( ${U1:-0} - ${U0:-0} ))" -v w="$W" 'BEGIN{printf "avg_busy_cpus %.2f\n", (w>0)? du/(w*1e6) : 0}' | tee -a "$O/times.txt"
echo "cgroup_memory_peak_bytes $(cat $CG/memory.peak 2>/dev/null)" | tee -a "$O/times.txt"
echo "tmpfs_used_kib $(df -k /tmp | awk 'NR==2{print $3}')" | tee -a "$O/times.txt"

cp q/quast.log "$O/" 2>/dev/null
for f in report.tsv report.txt contigs_reports/misassemblies_report.tsv contigs_reports/unaligned_report.tsv \
         genome_stats/features_report.tsv genome_stats/genome_info.txt; do
  [ -e "q/$f" ] && cp "q/$f" "$O/"
done
tar -czf "$O/quast-output.tgz" q

FAILS=0
[ "$RC" = 0 ] || { echo "FAIL: quast.py exit $RC"; FAILS=1; }
grep -q 'Thank you for using QUAST' q/quast.log 2>/dev/null || { echo "FAIL: no completion line in quast.log"; FAILS=1; }
ARGS="self=controls/self.fasta planted=controls/planted.fasta chimera=controls/chimera.fasta"
ARGS="$ARGS spades_contigs=spades_contigs.fasta spades_scaffolds=spades_scaffolds.fasta"
while read -r l; do ARGS="$ARGS $l=$l.fna.gz"; done < labels.txt
# shellcheck disable=SC2086
python3 -I -u check.py q "$REF" GCF_000005845.2.gff.gz controls/truth.json spades-real.report.tsv $ARGS | tee "$O/checks.txt"
[ "${PIPESTATUS[0]}" = 0 ] || FAILS=1
# the deterministic outputs, for the cross-chip comparison (logs and plots carry times and paths)
(cd q && find . -name '*.tsv' -o -name '*.txt' | grep -v -e log -e '/icarus' | sort | xargs sha256sum) > "$O/fingerprints.txt"
echo "run_failures $FAILS" | tee -a "$O/checks.txt"
[ "$FAILS" = 0 ] || exit 1
echo "QUAST-REAL OK"
