#!/usr/bin/env bash
# measurements/transcript-assembly-real task 2 (StringTie image, every chip): assemble the whole
# ERR188026 alignment four ways -- de novo and reference-guided (-G Ensembl 116), each at
# <threads> and at 1 thread -- and check every assembly (check_st.py).
#   stringtie.sh <threads>
# Glue is BusyBox-safe: bioconda's x86 image has no lscpu, perl, join or sha256sum --quiet.
set +e
set -uo pipefail
T=${1:?threads}
export PATH=/usr/local/bin:/opt/conda/bin:$PATH HOME=/tmp TMPDIR=/tmp
O=/tmp/out; W=/var/tmp/st
mkdir -p $O $W
fail() { echo "FAIL: $*" | tee -a $O/check.txt; exit 1; }
{
  echo "uname_m	$(uname -m)"
  echo "nproc	$(nproc)"
  echo "threads_per_core	$(awk -F'[,-]' '{print NF}' /sys/devices/system/cpu/cpu0/topology/thread_siblings_list 2>/dev/null)"
  grep -m1 -E '^model name' /proc/cpuinfo | sed 's/^model name[^:]*: */cpu_model	/'
  grep -m1 -E '^CPU part' /proc/cpuinfo | sed 's/^CPU part[^:]*: */cpu_part	/'
  echo "mem_total_kb	$(awk '/MemTotal/{print $2}' /proc/meminfo)"
  echo "stringtie	$(stringtie --version 2>&1 | head -n 1)"
  echo "stringtie_elf_machine	$(od -An -tu2 -j18 -N2 "$(command -v stringtie)" | tr -d ' ')"
  echo "openblas	not linked (StringTie is a C++ graph/flow assembler; no BLAS)"
} > $O/host.txt 2>&1
cat $O/host.txt

echo "== staged inputs must match their pins =="
( cd /tmp && sha256sum -c inputs.sha256 ) > $O/pins-check.txt 2>&1 \
  || { cat $O/pins-check.txt; fail "a staged input does not match its pin"; }
cat $O/pins-check.txt
gzip -dc /tmp/GRCh38.116.gtf.gz > $W/genes.gtf || fail "gunzip GTF"

BAM=/tmp/ERR188026.hisat2.bam
for t in "$T" 1; do
  python3 -u /tmp/timed.py $O/timing.txt "denovo_p$t" $O/stringtie_denovo_p$t.log -- \
    stringtie -p "$t" -l STRG -o $O/denovo_p$t.gtf $BAM || { tail -n 20 $O/stringtie_denovo_p$t.log; fail "stringtie de novo -p $t"; }
  python3 -u /tmp/timed.py $O/timing.txt "guided_p$t" $O/stringtie_guided_p$t.log -- \
    stringtie -p "$t" -l STRG -G $W/genes.gtf -o $O/guided_p$t.gtf $BAM || { tail -n 20 $O/stringtie_guided_p$t.log; fail "stringtie guided -p $t"; }
done
cat $O/timing.txt
cat /sys/fs/cgroup/memory.peak > $O/memory-peak.txt 2>/dev/null || echo unreadable > $O/memory-peak.txt

python3 -u -I /tmp/check_st.py "$T" /tmp/junctions.tsv /tmp/chrom.sizes $W/genes.gtf $O \
  || fail "check_st.py"
echo "STRINGTIE CHECKS PASSED"
