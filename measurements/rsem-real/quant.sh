#!/usr/bin/env bash
# measurements/rsem-real task 2 (RSEM image, every chip): RSEM's EM over every bowtie2 alignment of
# the complete ERR188026 run, then check.R at scale: read bookkeeping, conservation, aggregation,
# and rank agreement with salmon-real's quant of the same reads against the same FASTA.
#   quant.sh <threads>
# Glue is BusyBox-safe: bioconda's x86 image has no lscpu, join, numfmt or sha256sum --quiet.
set +e
set -uo pipefail
T=${1:?threads}
export PATH=/usr/local/bin:/opt/conda/bin:$PATH HOME=/tmp TMPDIR=/tmp
O=/tmp/out; W=/var/tmp/rsem
mkdir -p $O $W
fail() { echo "FAIL: $*" | tee -a $O/check.txt; exit 1; }
{
  echo "uname_m	$(uname -m)"
  echo "nproc	$(nproc)"
  echo "threads_per_core	$(awk -F'[,-]' '{print NF}' /sys/devices/system/cpu/cpu0/topology/thread_siblings_list 2>/dev/null)"
  grep -m1 -E '^model name' /proc/cpuinfo | sed 's/^model name[^:]*: */cpu_model	/'
  grep -m1 -E '^CPU part' /proc/cpuinfo | sed 's/^CPU part[^:]*: */cpu_part	/'
  echo "mem_total_kb	$(awk '/MemTotal/{print $2}' /proc/meminfo)"
  echo "rsem	$(rsem-calculate-expression --version 2>&1 | head -n 1)"
  echo "rsem_run_em_elf_machine	$(od -An -tu2 -j18 -N2 "$(command -v rsem-run-em)" | tr -d ' ')"
  echo "openblas	not linked (RSEM's EM is plain C++; no BLAS)"
  echo "R	$(Rscript --version 2>&1 | head -n 1)"
} > $O/host.txt 2>&1
cat $O/host.txt

echo "== staged inputs must match their pins =="
( cd /tmp && sha256sum -c inputs.sha256 ) > $O/pins-check.txt 2>&1 \
  || { cat $O/pins-check.txt; fail "a staged input does not match its pin"; }
cat $O/pins-check.txt

# Peak RSS: the kernel's own high-water mark (VmHWM) of each RSEM binary, read every second while
# it runs. VmHWM is monotonic, so polling can only miss growth in a process's last second.
hwm() { : > $O/hwm.txt
  while :; do
    for p in /proc/[0-9]*; do
      c=$(cat $p/comm 2>/dev/null) || continue
      case "$c" in rsem-*) k=$(awk '/^VmHWM/{print $2}' $p/status 2>/dev/null)
        [ -n "$k" ] && echo "$c	$k" >> $O/hwm.txt ;; esac
    done
    sleep 1
  done; }
hwm & HP=$!

echo "== rsem-calculate-expression: paired-end, $T threads, seed pinned =="
T0=$(date +%s)
rsem-calculate-expression --alignments --paired-end --no-bam-output -p "$T" --seed 12345 --time \
  --temporary-folder $W/tmp /tmp/aln.sam.gz /tmp/ens116 $O/ERR188026 > $O/rsem.log 2>&1
RC=$?
T1=$(date +%s)
kill $HP 2>/dev/null
echo "rsem	rc	$RC	wall_s	$(( T1 - T0 ))" | tee $O/timing.txt
awk -F'\t' '{if ($2+0 > m[$1]) m[$1] = $2+0} END{for (c in m) printf "maxrss_kb\t%s\t%d\n", c, m[c]}' $O/hwm.txt \
  | sort | tee -a $O/timing.txt
cat /sys/fs/cgroup/memory.peak > $O/memory-peak.txt 2>/dev/null || echo unreadable > $O/memory-peak.txt
rm -f $O/hwm.txt
[ "$RC" = 0 ] || { tail -n 40 $O/rsem.log; fail "rsem-calculate-expression rc $RC"; }
cat $O/ERR188026.time 2>/dev/null
cp $O/ERR188026.stat/ERR188026.cnt $O/ERR188026.stat/ERR188026.theta $O/ 2>/dev/null
( cd $O && sha256sum ERR188026.isoforms.results ERR188026.genes.results ERR188026.cnt ERR188026.theta ) > $O/results.sha256
cat $O/results.sha256

Rscript /tmp/check.R $O /tmp/gene_map.tsv /tmp/salmon.quant.sf || fail "check.R"
echo "RSEM CHECKS PASSED"
