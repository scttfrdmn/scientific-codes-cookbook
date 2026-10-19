#!/usr/bin/env bash
# measurements/rnaseq-counts-real task 2 (HTSeq image): htseq-count over the same alignment and
# annotation bytes as task 1 -- paired-end by name, unstranded, union, multimappers not counted,
# secondary/supplementary ignored -- then compare.py sets HTSeq against featureCounts (task 1)
# and STAR's GeneCounts (task 0). Writes everything to /tmp/out.
#   htseq.sh
set +e
set -uo pipefail
export PATH=/usr/local/bin:/opt/conda/bin:$PATH HOME=/tmp TMPDIR=/tmp
O=/tmp/out
mkdir -p $O
fail() { echo "FAIL: $*" | tee -a $O/check.txt; exit 1; }

{
  echo "uname_m	$(uname -m)"
  echo "nproc	$(nproc)"
  echo "threads_per_core	$(awk -F'[,-]' '{print NF}' /sys/devices/system/cpu/cpu0/topology/thread_siblings_list 2>/dev/null)"
  grep -m1 -E '^model name' /proc/cpuinfo | sed 's/^model name[^:]*: */cpu_model	/'
  grep -m1 -E '^CPU part' /proc/cpuinfo | sed 's/^CPU part[^:]*: */cpu_part	/'
  echo "mem_total_kb	$(awk '/MemTotal/{print $2}' /proc/meminfo)"
  echo "htseq	$(htseq-count --version 2>&1 | tail -n1)"
  echo "python_elf_machine	$(od -An -tu2 -j18 -N2 "$(command -v python3)" | tr -d ' ')"
} > $O/host.txt 2>&1
python3 -u - >> $O/host.txt 2>&1 <<'PY'
# Describe, never fail: HTSeq's numpy may link OpenBLAS, though counting calls no BLAS.
import ctypes, glob, sys
libs = sorted(glob.glob(sys.prefix + "/lib/libopenblas*.so*"))
try:
    lib = ctypes.CDLL(libs[0]); lib.openblas_get_corename.restype = ctypes.c_char_p
    print("openblas_corename\t%s" % str(lib.openblas_get_corename().decode()))
except Exception as e:
    print("openblas_corename\tunavailable (%s)" % str(e).split("\n")[0][:80])
PY
cat $O/host.txt

echo "== staged inputs must match their pins =="
( cd /tmp && sha256sum -c inputs.sha256 ) > $O/pins-check.txt 2>&1 \
  || { cat $O/pins-check.txt; fail "a staged input does not match its pin"; }
cat $O/pins-check.txt

echo "== htseq-count, single process =="
T0=$(date +%s)
htseq-count -f bam -r name -s no -t exon -i gene_id -m union --nonunique none \
  --secondary-alignments ignore --supplementary-alignments ignore \
  /tmp/reads.bam /tmp/GRCh38.116.gtf.gz > $O/htseq.txt 2> $O/htseq.log &
PID=$!; HWM=0
# VmHWM is the kernel's own high-water mark, so a late read misses nothing but the last interval
while kill -0 $PID 2>/dev/null; do
  h=$(awk '/^VmHWM/{print $2}' /proc/$PID/status 2>/dev/null); [ -n "$h" ] && HWM=$h
  sleep 2
done
wait $PID; RC=$?
T1=$(date +%s)
echo "htseq_rc	$RC	wall_s	$(( T1 - T0 ))	vmhwm_kb	$HWM" | tee $O/timing.txt
cat /sys/fs/cgroup/memory.peak > $O/memory-peak.txt 2>/dev/null || echo unreadable > $O/memory-peak.txt
tail -n 5 $O/htseq.log
[ "$RC" -eq 0 ] || fail "htseq-count exited $RC"

python3 -u /tmp/compare.py /tmp $O || fail "compare.py"
echo "HTSEQ + COMPARISON CHECKS PASSED"
