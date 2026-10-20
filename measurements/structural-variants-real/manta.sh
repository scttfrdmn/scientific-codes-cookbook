#!/usr/bin/env bash
# measurements/structural-variants-real 01-manta: germline Manta on the whole HG002 30x genome, the
# recipe's invocation. Runs in the recipe's manta image on every chip (aarch.bio arm64, bioconda x86:
# BusyBox userland there, so no join/numfmt/perl/`sha256sum --quiet` and no big awk arrays).
#   manta.sh <threads> <memory GB for pyflow>
set +e
set -uo pipefail
T=${1:?threads}; G=${2:?memory GB}
export PATH=/opt/conda/bin:/usr/local/bin:$PATH HOME=/tmp TMPDIR=/var/tmp
O=/tmp/out; R=/var/tmp/mrun
mkdir -p $O
fail() { echo "FAIL: $*" | tee -a $O/check.txt; exit 1; }
finish() {   # the tool's own logs leave the box whatever happens
  cp -f $R/workspace/pyflow.data/logs/pyflow_log.txt $O/pyflow_log.txt 2>/dev/null
  cp -f $R/workflow.error.log.txt $O/ 2>/dev/null
  cp -f $R/results/stats/* $O/ 2>/dev/null
}
trap finish EXIT
cd /tmp
BIN=$(dirname "$(readlink -f "$(command -v configManta.py)")")/../libexec/GenerateSVCandidates
{
  echo "uname_m	$(uname -m)"
  echo "nproc	$(nproc)"
  echo "threads_per_core	$(awk -F'[,-]' '{print NF}' /sys/devices/system/cpu/cpu0/topology/thread_siblings_list 2>/dev/null)"
  grep -m1 -E '^model name' /proc/cpuinfo | sed 's/^model name[^:]*: */cpu_model	/'
  grep -m1 -E '^CPU part' /proc/cpuinfo | sed 's/^CPU part[^:]*: */cpu_part	/'
  echo "mem_total_kb	$(awk '/^MemTotal/{print $2}' /proc/meminfo)   (pyflow sizes from this; -g overrides)"
  echo "manta	$(configManta.py --version 2>&1 | head -n1)"
  echo "manta_elf_machine	$(od -An -tu2 -j18 -N2 "$BIN" | tr -d ' ')"
} > $O/host.txt 2>&1
cat $O/host.txt
M=$(awk -F'\t' '$1=="manta_elf_machine"{print $2}' $O/host.txt)
case "$(uname -m)" in aarch64) WANT=183 ;; x86_64) WANT=62 ;; *) WANT=? ;; esac
[ "$M" = "$WANT" ] || fail "GenerateSVCandidates is ELF machine $M on $(uname -m): emulated or wrong image"

echo "== staged inputs against pins.sha256 =="
grep -E '  (HG002\.hs37d5\.30x\.bam|HG002\.hs37d5\.30x\.bam\.bai|hs37d5\.fa|hs37d5\.fa\.fai)$' pins.sha256 > in.sha256
[ "$(wc -l < in.sha256 | tr -d ' ')" = 4 ] || fail "pins.sha256 lacks an input"
T0=$(date +%s)
sha256sum -c in.sha256 || fail "a staged input does not match its pin"
echo "sha256_s	$(( $(date +%s) - T0 ))" > $O/timing.txt

echo "== configManta.py + runWorkflow.py =="
T0=$(date +%s)
configManta.py --bam HG002.hs37d5.30x.bam --referenceFasta hs37d5.fa --runDir $R > $O/config.log 2>&1 \
  || { cat $O/config.log; fail "configManta.py"; }
T1=$(date +%s)
# python, not a shell timer: RUSAGE_CHILDREN's ru_maxrss is the largest single descendant process.
python -u - "$T" "$G" "$R" <<'PY' > $O/workflow.log 2>&1
import resource, subprocess, sys
t, g, r = sys.argv[1:4]
rc = subprocess.call([r + "/runWorkflow.py", "-m", "local", "-j", t, "-g", g])
print("rc\t%d" % rc)
print("max_child_rss_kb\t%d" % resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
sys.exit(rc)
PY
RC=$?
T2=$(date +%s)
CG=$(cat /sys/fs/cgroup/memory.peak 2>/dev/null || echo NA)
MAXRSS=$(awk -F'\t' '$1=="max_child_rss_kb"{print $2}' $O/workflow.log)
printf 'config_s\t%s\nworkflow_s\t%s\nrc\t%s\nmax_process_rss_kb\t%s\ncgroup_memory_peak\t%s\n' \
  $((T1-T0)) $((T2-T1)) $RC "$MAXRSS" "$CG" >> $O/timing.txt
cat $O/timing.txt; tail -n 5 $O/workflow.log
[ "$RC" = 0 ] || fail "runWorkflow.py exited $RC"
grep -q 'Manta workflow successfully completed' $R/workspace/pyflow.data/logs/pyflow_log.txt \
  || fail "no completion line in pyflow_log.txt"

V=$R/results/variants
for f in diploidSV candidateSV candidateSmallIndels; do
  cp $V/$f.vcf.gz $V/$f.vcf.gz.tbi $O/ || fail "no $f.vcf.gz"
done
[ "$(od -An -tx1 -N4 $O/diploidSV.vcf.gz | tr -d ' ')" = 1f8b0804 ] || fail "diploidSV.vcf.gz is not BGZF"

echo "== checks on every record =="
# The recipe's convention, on every call: manta's END is the last base the event replaces. So every
# explicit-allele record has END == POS+len(REF)-1; a DEL's -SVLEN is its span END-POS, whatever bases
# it also inserts; an INS's SVLEN is its inserted length len(ALT)-1, whatever bases it also deletes.
gzip -dc $O/diploidSV.vcf.gz | awk -F'\t' '
  /^#CHROM/ { sample = $10; next } /^#/ { next }
  { n++; t=""; e=""; l=""; k = split($8, a, ";")
    for (i = 1; i <= k; i++) { split(a[i], kv, "="); if (kv[1]=="SVTYPE") t=kv[2]; if (kv[1]=="END") e=kv[2]
      if (kv[1]=="SVLEN") l=kv[2] }
    c[t]++; if ($7 == "PASS") p[t]++
    x = ($5 !~ /^</ && $5 !~ /[][]/)
    if (x && e != "") { ne++; if (e != $2 + length($4) - 1) bad_end++ }
    if (t == "DEL" && l != "") { nd++; if (-l != e - $2) bad_del++ }
    if (t == "INS" && x && l != "") { ni++; if (l != length($5) - 1) bad_ins++ }
    if (!($1 in seen)) { seen[$1] = 1; nc++ } }
  END {
    printf "sample\t%s\nrecords\t%d\ncontigs_with_calls\t%d\n", sample, n, nc
    for (x in c) printf "calls_%s\t%d\npass_%s\t%d\n", x, c[x], x, p[x] + 0
    printf "explicit\t%d\nexplicit_end_mismatch\t%d\n", ne, bad_end + 0
    printf "del_with_svlen\t%d\ndel_svlen_span_mismatch\t%d\n", nd, bad_del + 0
    printf "ins_explicit\t%d\nins_svlen_mismatch\t%d\n", ni, bad_ins + 0 }' | sort > $O/check.txt
gzip -dc $O/diploidSV.vcf.gz | grep -v '^#' | cut -f1-8 | sha256sum | cut -d' ' -f1 \
  | sed 's/^/records_sha256\t/' >> $O/check.txt
cat $O/check.txt
v() { awk -F'\t' -v k="$1" '$1==k{print $2}' $O/check.txt; }
[ "$(v sample)" = HG002 ] || fail "the VCF sample is $(v sample), not the read group's HG002"
[ "$(v contigs_with_calls)" -ge 23 ] || fail "calls on $(v contigs_with_calls) contigs: not a whole genome"
[ "$(v pass_DEL)" -gt 0 ] && [ "$(v pass_INS)" -gt 0 ] || fail "no PASS deletions or insertions"
[ "$(v explicit_end_mismatch)" = 0 ] && [ "$(v del_svlen_span_mismatch)" = 0 ] && [ "$(v ins_svlen_mismatch)" = 0 ] \
  || fail "a record breaks manta's END/SVLEN convention"
echo "MANTA CHECKS PASSED" | tee -a $O/check.txt
