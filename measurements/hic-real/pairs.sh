#!/usr/bin/env bash
# measurements/hic-real task 1 (pairtools image): 4DN's two GM12878 replicate contact lists,
# merged and mapq-filtered exactly as the 4DN pipeline did, must reproduce 4DN's own combined list;
# and pairtools dedup is checked on real data both ways -- it must find nothing in lists pairtools
# already deduplicated, and exactly the planted copies in a list merged with itself.
#   pairs.sh <threads>
# Glue is BusyBox-safe: bioconda's x86 image has no lscpu, perl, join or sha256sum --quiet.
set +e
set -uo pipefail
T=${1:?threads}
export PATH=/usr/local/bin:/opt/conda/bin:$PATH HOME=/tmp TMPDIR=/var/tmp/hic
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1   # several python processes run at once
O=/tmp/out; W=/var/tmp/hic          # big intermediates on the root disk, not the half-RAM tmpfs
mkdir -p $O $W
cd /tmp
fail() { echo "FAIL: $*" | tee -a $O/check.txt; exit 1; }
rec() { printf '%s\t%s\n' "$1" "$2" | tee -a $O/check.txt; }
R1=4DNFILGIU5G7.pairs.gz; R2=4DNFI4EZMKAO.pairs.gz; C=4DNFIHMMV629.pairs.gz

{
  echo "uname_m	$(uname -m)"
  echo "nproc	$(grep -c ^processor /proc/cpuinfo)"
  echo "threads_per_core	$(awk -F'[,-]' '{print NF}' /sys/devices/system/cpu/cpu0/topology/thread_siblings_list 2>/dev/null)"
  grep -m1 -E '^model name' /proc/cpuinfo | sed 's/^model name[^:]*: */cpu_model	/'
  grep -m1 -E '^CPU part' /proc/cpuinfo | sed 's/^CPU part[^:]*: */cpu_part	/'
  echo "mem_total_kb	$(awk '/MemTotal/{print $2}' /proc/meminfo)"
  echo "pairtools	$(pairtools --version 2>&1 | head -n1)"
  echo "python_elf_machine	$(od -An -tu2 -j18 -N2 "$(readlink -f "$(command -v python3)")" | tr -d ' ')"
  echo "sort	$(sort --version 2>&1 | head -n1)"
  echo "bgzip	$(bgzip --version 2>&1 | head -n1)"
  echo "openblas	not used (pairtools dedup is a KD-tree search over integer positions)"
} > $O/host.txt 2>&1
cat $O/host.txt

echo "== staged bytes must match the sha256 pins and 4DN's published md5 =="
pins() {   # pins <file>...: 4DN's md5 must match; our sha256 must match where inputs.sha256 has one
  for f in "$@"; do
    m=$(awk -v f="$f" '$2==f{print $1}' inputs.md5); s=$(awk -v f="$f" '$2==f{print $1}' inputs.sha256)
    [ -n "$m" ] || fail "$f has no published md5 in inputs.md5"
    gm=$(md5sum "$f" | cut -d' ' -f1); gs=$(sha256sum "$f" | cut -d' ' -f1)
    echo "$gs  $f" >> $O/inputs-observed.sha256
    [ "$gm" = "$m" ] || fail "$f md5 $gm, 4DN publishes $m"
    [ -z "$s" ] || [ "$gs" = "$s" ] || fail "$f sha256 $gs, pinned $s"
    echo "ok  $f  md5 $gm  sha256 $gs${s:+ (pinned)}" | tee -a $O/pins-check.txt
  done
}
pins $R1 $R2 $C 4DNFI823LSII.chrom.sizes

# Each timed step: wall, exit code, and the peak RSS of any process under it (RUSAGE_CHILDREN).
cat > $W/tm.py <<'PY'
import resource, subprocess, sys, time
name, cmd = sys.argv[1], sys.argv[2]
t0 = time.time()
rc = subprocess.call(["bash", "-c", "set -o pipefail\n" + cmd])
w = time.time() - t0
ru = resource.getrusage(resource.RUSAGE_CHILDREN)
with open("/tmp/out/timing.tsv", "a") as fh:
    fh.write("%s\t%.1f\t%d\t%d\t%.1f\n" % (name, w, rc, ru.ru_maxrss, ru.ru_utime + ru.ru_stime))
sys.exit(rc)
PY
printf 'step\twall_s\trc\tmaxrss_kb\tcpu_s\n' > $O/timing.tsv
tm() { python3 -u $W/tm.py "$@"; }
# body digest and record count of a pairs file in one pass (the header carries run-specific @PG lines)
# (a fifo, not >(...): the shell does not wait for a process substitution, so its count could be read early)
body() { echo "rm -f $2.f; mkfifo $2.f; wc -l < $2.f > $2.n & bgzip -dc -@ 2 $1 | grep -v '^#' | tee $2.f | md5sum | cut -d' ' -f1 > $2.md5; wait"; }
DEDUP="pairtools dedup --max-mismatch 3 --method max"   # pairtools' defaults, as 4DN ran them

echo "== in parallel: the 4DN merge+filter, three dedup checks, and the reference digests =="
T0=$(date +%s)
# (a) merge the two replicates and keep mapq1>30 and mapq2>30 -- the exact @PG chain 4DN records
#     in 4DNFIHMMV629's header -- then digest the body.
tm merge_select "pairtools merge --nproc $T --memory 8G --tmpdir $W $R1 $R2 \
  | pairtools select 'mapq1>30 and mapq2>30' -o $O/combined.pairs.gz" > $O/merge.log 2>&1 &
P1=$!
# (b) idempotence: dedup's own output, deduplicated again, holds no duplicates. Each replicate was
#     already deduplicated by 4DN (pairtools 1.1.0, the same defaults); what this version still
#     finds in it is recorded, with the pairs, and is not an identity.
tm dedup_rep1 "$DEDUP --output-stats $O/dedup-rep1.stats --output-dups $O/dedup-rep1.dups.pairs -o - $R1 \
  | $DEDUP --output-stats $O/dedup-again.stats -o /dev/null" > $O/dedup-rep1.log 2>&1 &
P2=$!
tm dedup_rep2 "$DEDUP --output-stats $O/dedup-rep2.stats --output-dups $O/dedup-rep2.dups.pairs -o /dev/null $R2" \
  > $O/dedup-rep2.log 2>&1 &
P3=$!
# diagnostic: the same dedup of rep1 with a 100x chunk and 1000x carryover (the streaming window)
tm dedup_rep1_window "$DEDUP --chunksize 1000000 --carryover 100000 --output-stats $O/dedup-window.stats \
  --output-dups $O/dedup-window.dups.pairs -o /dev/null $R1" > $O/dedup-window.log 2>&1 &
P8=$!
# (c) planted duplicates: replicate 2 merged with itself is every pair twice, so dedup must mark
#     exactly one copy of each, and what it keeps must be replicate 2 again.
tm dedup_planted "rm -f $W/planted.f; mkfifo $W/planted.f; wc -l < $W/planted.f > $W/planted.n & \
  pairtools merge --nproc 2 --memory 4G --tmpdir $W $R2 $R2 \
  | $DEDUP --output-stats $O/dedup-planted.stats -o - | grep -v '^#' \
  | tee $W/planted.f | md5sum | cut -d' ' -f1 > $W/planted.md5; wait" > $O/dedup-planted.log 2>&1 &
P4=$!
tm digest_4dn_combined "$(body $C $W/c4dn)" > $O/digest.log 2>&1 &
P5=$!
tm digest_rep1 "$(body $R1 $W/r1)" >> $O/digest.log 2>&1 &
P6=$!
tm digest_rep2 "$(body $R2 $W/r2)" >> $O/digest.log 2>&1 &
P7=$!
RC=0
for p in $P1 $P2 $P3 $P4 $P5 $P6 $P7 $P8; do wait $p || RC=$((RC+1)); done
rec parallel_wall_s $(( $(date +%s) - T0 ))
cat $O/timing.tsv
[ $RC = 0 ] || { tail -20 $O/*.log; fail "$RC of the parallel steps exited non-zero"; }
tm digest_ours "$(body $O/combined.pairs.gz $W/ours)" >> $O/digest.log 2>&1 || fail "digest of our combined list"

N1=$(tr -d ' ' < $W/r1.n); N2=$(tr -d ' ' < $W/r2.n); NC=$(tr -d ' ' < $W/c4dn.n); NO=$(tr -d ' ' < $W/ours.n)
rec rep1_records "$N1"; rec rep2_records "$N2"; rec combined_4dn_records "$NC"; rec combined_ours_records "$NO"
rec combined_4dn_body_md5 "$(cat $W/c4dn.md5)"; rec combined_ours_body_md5 "$(cat $W/ours.md5)"
rec rep2_body_md5 "$(cat $W/r2.md5)"
[ "$NC" -gt 100000000 ] || fail "4DN's combined list has only $NC records"

echo "== identity 1: our merge+filter reproduces 4DN's combined list =="
[ "$NO" = "$NC" ] || fail "our combined list has $NO records, 4DN's has $NC"
if [ "$(cat $W/ours.md5)" = "$(cat $W/c4dn.md5)" ]; then
  rec identity_combined "body byte-identical to 4DN's 4DNFIHMMV629 ($NC records, same order)"
else
  # Different bytes: compare as multisets (sorted), and the sort key (chrom1 pos1 chrom2 pos2) in
  # file order -- equal keys in order means the two lists differ only in how ties are ordered.
  for f in c4dn ours; do
    case $f in c4dn) src=$C ;; *) src=$O/combined.pairs.gz ;; esac
    tm sorted_$f "bgzip -dc -@ 2 $src | grep -v '^#' | LC_ALL=C sort -S 5G --parallel=6 -T $W \
      | md5sum | cut -d' ' -f1 > $W/$f.sorted.md5" >> $O/digest.log 2>&1 &
    tm keys_$f "bgzip -dc -@ 2 $src | grep -v '^#' | cut -f2-5 | md5sum | cut -d' ' -f1 > $W/$f.keys.md5" \
      >> $O/digest.log 2>&1 &
  done
  wait
  rec sorted_body_md5 "4DN $(cat $W/c4dn.sorted.md5)  ours $(cat $W/ours.sorted.md5)"
  rec key_order_md5 "4DN $(cat $W/c4dn.keys.md5)  ours $(cat $W/ours.keys.md5)"
  [ "$(cat $W/c4dn.sorted.md5)" = "$(cat $W/ours.sorted.md5)" ] || fail "our combined list holds different records from 4DN's"
  [ "$(cat $W/c4dn.keys.md5)" = "$(cat $W/ours.keys.md5)" ] || fail "our combined list is in a different sort-key order from 4DN's"
  rec identity_combined "same $NC records as 4DN's 4DNFIHMMV629, same key order; differs only in order among tied keys"
fi
# Observation, not an identity: how many merged pairs the mapq filter removed.
rec mapq_filter_removed "$(( N1 + N2 - NO ))"

st() { awk -v k="$2" '$1==k{print $2; exit}' "$1"; }
for s in rep1 rep2 planted; do
  f=$O/dedup-$s.stats
  t=$(st $f total); d=$(st $f total_dups); n=$(st $f total_nodups); m=$(st $f total_mapped)
  rec dedup_${s}_total "$t"; rec dedup_${s}_dups "$d"; rec dedup_${s}_nodups "$n"
  [ -n "$t" ] && [ -n "$d" ] && [ -n "$n" ] || fail "could not read $f"
  [ "$m" = "$t" ] || fail "$s: $m of $t pairs mapped -- these lists hold mapped pairs only"
  [ $((n + d)) = "$t" ] || fail "$s: nodups $n + dups $d != total $t"
done
rec identity_bookkeeping "total == nodups + dups on all three dedup runs"

echo "== identity 2: dedup is idempotent -- its own output holds no duplicates =="
[ "$(st $O/dedup-rep1.stats total)" = "$N1" ] || fail "dedup read $(st $O/dedup-rep1.stats total) of rep1's $N1"
[ "$(st $O/dedup-rep2.stats total)" = "$N2" ] || fail "dedup read $(st $O/dedup-rep2.stats total) of rep2's $N2"
A=$(st $O/dedup-again.stats total); AD=$(st $O/dedup-again.stats total_dups)
rec dedup_again_total "$A"; rec dedup_again_dups "$AD"
[ "$A" = "$(st $O/dedup-rep1.stats total_nodups)" ] || fail "second pass read $A, first pass kept $(st $O/dedup-rep1.stats total_nodups)"
[ "$AD" = 0 ] || fail "dedup found $AD duplicates in its own output"
rec identity_idempotent "dedup(dedup(rep1)) finds 0 duplicates in $A pairs"
# Observation: what this pairtools still finds in 4DN's already-deduplicated replicates.
rec redup_4dn_rep1 "$(st $O/dedup-rep1.stats total_dups)"
rec redup_4dn_rep2 "$(st $O/dedup-rep2.stats total_dups)"
rec redup_4dn_rep1_window "$(st $O/dedup-window.stats total_dups)"
grep -v '^#' $O/dedup-rep1.dups.pairs | head -20 | cut -f2-8 | sed 's/^/  /'

echo "== identity 3: dedup removes exactly the planted copies, and keeps the original =="
[ "$(st $O/dedup-planted.stats total)" = $((2 * N2)) ] || fail "planted: total is not 2 x $N2"
[ "$(st $O/dedup-planted.stats total_dups)" = "$N2" ] || fail "planted: dups $(st $O/dedup-planted.stats total_dups), planted $N2"
[ "$(tr -d ' ' < $W/planted.n)" = "$N2" ] || fail "planted: kept $(cat $W/planted.n) pairs, not $N2"
rec planted_kept_body_md5 "$(cat $W/planted.md5)"
if [ "$(cat $W/planted.md5)" = "$(cat $W/r2.md5)" ]; then
  rec identity_planted "$N2 planted duplicates found of $N2; kept list byte-identical to rep2"
else
  rec identity_planted "$N2 planted duplicates found of $N2; kept list same count, different bytes"
fi

for k in /sys/fs/cgroup/memory.peak /sys/fs/cgroup/memory/memory.max_usage_in_bytes; do
  [ -r $k ] && rec cgroup_memory_peak_bytes "$(cat $k)" && break
done
echo "PAIRS OK" | tee -a $O/check.txt
