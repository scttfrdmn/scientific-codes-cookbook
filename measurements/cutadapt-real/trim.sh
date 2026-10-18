#!/usr/bin/env bash
# cutadapt-real, the one task (cutadapt image): the recipe's paired-end TruSeq invocation on whole
# published runs, one after another on one box, then check.py over every read.
#
#   bash trim.sh <threads> <accession>:<ENA pairs>:<ENA bases>[:full] ...
#
# Per sample: the recipe's command (`main`, timed: this is the workload), the same command without
# its two filters (`raw`, so the trimming and the filtering can be checked apart), and for a sample
# marked `full` also the recipe's command at -j 1, the recipe's -q 15,20 on each mate, and a
# wrong-kit control (Nextera's adapter). Outputs that are only checked, never kept, go on the
# container disk (/var/tmp); /tmp is the tmpfs that holds the staged reads. Runs on aarch.bio's
# arm64 image and bioconda's x86 one (BusyBox userland), so the glue is POSIX sh/awk + python3.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH TMPDIR=/tmp HOME=/tmp
T=$1; shift
AD1=AGATCGGAAGAGCACACGTCTGAACTCCAGTCAC
AD2=AGATCGGAAGAGCGTCGTGTAGGGAAAGAGTGT
NX=CTGTCTCTTATACACATCT
O=/tmp/out
W=/var/tmp/cutadapt-real
mkdir -p "$O" "$W"
cd /tmp

{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  echo "mem_total_kib $(awk '/^MemTotal/{print $2}' /proc/meminfo)"
  echo "cutadapt $(command -v cutadapt) $(cutadapt --version 2>&1)"
  python3 -c 'import sys, platform, dnaio; print("python", platform.python_version(), "dnaio", getattr(dnaio, "__version__", "unknown"))' 2>&1
  df -h /tmp /var/tmp 2>&1
} > "$O/host.txt"
awk '/^online_cpus/{c=$2} /^physical_cores/{p=$2} END{printf "threads_per_core %d\n", (p>0)?c/p:0}' "$O/host.txt" >> "$O/host.txt"
cat "$O/host.txt"

# run <label> <dir> <cmd...>: wall seconds, peak anonymous memory and mean busy cores of the
# container cgroup over the command, sampled every 2 s
run() {
  local label=$1 dir=$2; shift 2
  ( while : ; do echo "$(date +%s) $(awk '/^anon /{print $2; exit}' /sys/fs/cgroup/memory.stat 2>/dev/null) $(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)" >> "$dir/$label.samples"; sleep 2; done ) &
  local spid=$! t0 t1 rc
  t0=$(date +%s)
  "$@" > "$dir/cutadapt_$label.log" 2>&1
  rc=$?
  t1=$(date +%s)
  kill $spid 2>/dev/null; wait $spid 2>/dev/null
  awk -v l="$label" -v rc="$rc" -v w="$((t1-t0))" 'NR==1{t0=$1; u0=$3} $2>m{m=$2} {t=$1; u=$3}
      END{printf "%s rc %d wall_s %d peak_anon_gib %.2f avg_busy_cores %.2f\n", l, rc, w, m/1073741824, (t>t0)?(u-u0)/1e6/(t-t0):0}' \
      "$dir/$label.samples" | tee -a "$dir/times.txt"
  return $rc
}

FAILS=0
for s in "$@"; do
  IFS=: read -r ACC PAIRS BASES FULL <<<"$s"
  S=$O/$ACC; D=$W/$ACC; mkdir -p "$S" "$D"
  IN1=${ACC}_1.fastq.gz IN2=${ACC}_2.fastq.gz
  echo "== $ACC: the recipe's invocation, -j $T =="
  run main "$S" cutadapt -a "$AD1" -A "$AD2" -e 0.1 -O 3 -m 10 --max-n 0 -j "$T" --json="$S/main.json" \
      -o "$D/main_R1.fq.gz" -p "$D/main_R2.fq.gz" "$IN1" "$IN2" || { tail -20 "$S/cutadapt_main.log"; FAILS=$((FAILS+1)); continue; }
  echo "== $ACC: the same trimming, no filters =="
  run raw "$S" cutadapt -a "$AD1" -A "$AD2" -e 0.1 -O 3 -j "$T" --json="$S/raw.json" \
      -o "$D/raw_R1.fq" -p "$D/raw_R2.fq" "$IN1" "$IN2" || { tail -20 "$S/cutadapt_raw.log"; FAILS=$((FAILS+1)); continue; }
  if [ "${FULL:-}" = full ]; then
    echo "== $ACC: -j 1, -q 15,20 per mate, and the wrong kit's adapter =="
    run j1 "$S" cutadapt -a "$AD1" -A "$AD2" -e 0.1 -O 3 -m 10 --max-n 0 -j 1 --json="$S/j1.json" \
        -o "$D/j1_R1.fq.gz" -p "$D/j1_R2.fq.gz" "$IN1" "$IN2" || FAILS=$((FAILS+1))
    for m in 1 2; do
      run "qual_R$m" "$S" cutadapt -q 15,20 -j "$T" --json="$S/qual_R$m.json" -o "$D/qual_R$m.fq" "${ACC}_$m.fastq.gz" || FAILS=$((FAILS+1))
    done
    run nextera "$S" cutadapt -a "$NX" -A "$NX" -e 0.1 -O 3 -m 10 --max-n 0 -j "$T" --json="$S/nextera.json" \
        -o /dev/null -p /dev/null "$IN1" "$IN2" || FAILS=$((FAILS+1))
  fi
  df -h /var/tmp | awk 'NR==2{print "container_disk_used " $3 " of " $2}' | tee -a "$S/times.txt"
done
# The checks start after every timed cutadapt run, so they cannot slow one; one process per sample.
for s in "$@"; do
  IFS=: read -r ACC PAIRS BASES FULL <<<"$s"
  EXTRA=""; [ "${FULL:-}" = full ] && EXTRA="--full"
  echo "== $ACC: check every read =="
  python3 -u check.py "$ACC" "$PAIRS" "$BASES" "$AD1" "$AD2" "$W/$ACC" "$O/$ACC" $EXTRA > "$O/$ACC/check.log" 2>&1 &
done
wait
for s in "$@"; do
  ACC=${s%%:*}
  cat "$O/$ACC/check.log"
  grep -q "^CHECK OK $ACC" "$O/$ACC/check.log" || FAILS=$((FAILS+1))
done
echo "failures $FAILS" | tee -a "$O/times.txt"
[ "$FAILS" = 0 ] || { echo "CUTADAPT-REAL FAILED"; exit 1; }
echo "CUTADAPT-REAL OK"
