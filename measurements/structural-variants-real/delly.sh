#!/usr/bin/env bash
# measurements/structural-variants-real 02-delly: `delly sr` on the same HG002 30x BAM, with the
# telomere/centromere exclude list delly ships (excludeTemplates/human.hg19.excl.tsv at v2.6.0).
# The delly image has no bcftools, so the calls themselves are read in 03-score.
#   delly.sh <threads>
set +e
set -uo pipefail
T=${1:?threads}
export PATH=/opt/conda/bin:/usr/local/bin:$PATH HOME=/tmp TMPDIR=/var/tmp
O=/tmp/out
mkdir -p $O
fail() { echo "FAIL: $*" | tee -a $O/check.txt; exit 1; }
cd /tmp
{
  echo "uname_m	$(uname -m)"
  echo "nproc	$(nproc)"
  echo "threads_per_core	$(awk -F'[,-]' '{print NF}' /sys/devices/system/cpu/cpu0/topology/thread_siblings_list 2>/dev/null)"
  grep -m1 -E '^model name' /proc/cpuinfo | sed 's/^model name[^:]*: */cpu_model	/'
  grep -m1 -E '^CPU part' /proc/cpuinfo | sed 's/^CPU part[^:]*: */cpu_part	/'
  echo "mem_total_kb	$(awk '/^MemTotal/{print $2}' /proc/meminfo)"
  echo "delly	$(delly --version 2>&1 | grep -m1 -i version)"
  echo "delly_elf_machine	$(od -An -tu2 -j18 -N2 "$(command -v delly)" | tr -d ' ')"
} > $O/host.txt 2>&1
cat $O/host.txt
M=$(awk -F'\t' '$1=="delly_elf_machine"{print $2}' $O/host.txt)
case "$(uname -m)" in aarch64) WANT=183 ;; x86_64) WANT=62 ;; *) WANT=? ;; esac
[ "$M" = "$WANT" ] || fail "delly is ELF machine $M on $(uname -m): emulated or wrong image"

echo "== staged inputs against pins.sha256 =="
grep -E '  (HG002\.hs37d5\.30x\.bam|HG002\.hs37d5\.30x\.bam\.bai|hs37d5\.fa|hs37d5\.fa\.fai|human\.hg19\.excl\.tsv)$' \
  pins.sha256 > in.sha256
[ "$(wc -l < in.sha256 | tr -d ' ')" = 5 ] || fail "pins.sha256 lacks an input"
T0=$(date +%s)
sha256sum -c in.sha256 || fail "a staged input does not match its pin"
echo "sha256_s	$(( $(date +%s) - T0 ))" > $O/timing.txt

echo "== delly sr =="
peak() {  # peak() <pid>: the process's VmHWM (kB); a high-water mark, so a late read is the peak
  local h=0 x
  while kill -0 "$1" 2>/dev/null; do
    x=$(awk '/^VmHWM/{print $2}' /proc/$1/status 2>/dev/null); [ -n "$x" ] && h=$x
    sleep 1
  done
  echo $h
}
T0=$(date +%s)
delly sr -h "$T" -x human.hg19.excl.tsv -g hs37d5.fa -o $O/delly.bcf HG002.hs37d5.30x.bam > $O/delly.log 2>&1 &
DP=$!
HWM=$(peak $DP); wait $DP; RC=$?
T1=$(date +%s)
CG=$(cat /sys/fs/cgroup/memory.peak 2>/dev/null || echo NA)
printf 'delly_s\t%s\nrc\t%s\nvmhwm_kb\t%s\ncgroup_memory_peak\t%s\n' $((T1-T0)) $RC "$HWM" "$CG" >> $O/timing.txt
cat $O/timing.txt; tail -n 8 $O/delly.log
[ "$RC" = 0 ] || fail "delly sr exited $RC"
grep -q 'Done' $O/delly.log || fail "no 'Done' line in delly.log"

echo "== checks =="
grep 'Sample:' $O/delly.log > $O/delly-stats.txt
MAD=$(sed -n 's/.*MAD=\([0-9]*\).*/\1/p' $O/delly-stats.txt | head -n1)
UDP=$(sed -n 's/.*UniqueDiscordantPairs=\([0-9]*\).*/\1/p' $O/delly-stats.txt | head -n1)
MAGIC=$(od -An -tx1 -N4 $O/delly.bcf | tr -d ' ')
BCF=$(gzip -dc $O/delly.bcf 2>/dev/null | od -An -c -N3 | tr -d ' ')
{
  printf 'insert_mad\t%s\nunique_discordant_pairs\t%s\nbgzf_magic\t%s\nbcf_magic\t%s\nbcf_bytes\t%s\n' \
    "$MAD" "$UDP" "$MAGIC" "$BCF" "$(wc -c < $O/delly.bcf | tr -d ' ')"
} > $O/check.txt
cat $O/delly-stats.txt $O/check.txt
[ "$MAGIC" = 1f8b0804 ] && [ "$BCF" = BCF ] || fail "delly.bcf is not a BGZF-compressed BCF"
[ -s $O/delly.bcf.csi ] || fail "no delly.bcf.csi"
[ "${MAD:-0}" -gt 0 ] && [ "${UDP:-0}" -gt 0 ] || fail "delly saw no insert-size spread or no discordant pairs"
echo "DELLY CHECKS PASSED" | tee -a $O/check.txt
