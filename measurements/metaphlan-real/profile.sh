#!/usr/bin/env bash
# metaphlan-real, task 1 (MetaPhlAn image): profile whole published Zymo runs against the recipe's
# CHOCOPhlAn index, one after another on one box (as a cohort would), and score each with score.py.
# The recipe's 03-profile, at a whole run's depth.
#
#   bash profile.sh <threads> <accession>:<truth.tsv> [<accession>:<truth.tsv> ...]
#
# Each sample needs /tmp/<acc>_{1,2}.fastq.gz and /tmp/<acc>.reads_ge70 (from prep.sh). Runs on
# aarch.bio's arm64 image and bioconda's x86 one (BusyBox userland: no sha256sum --quiet, no
# date +%N), so the glue is POSIX sh/awk and python3 from the image. Writes /tmp/out/.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
export TMPDIR=/tmp HOME=/tmp
T=$1; shift
IDX=mpa_vJun23_CHOCOPhlAnSGB_202403
O=/tmp/out
mkdir -p "$O"
cd /tmp

echo "== host ==" | tee "$O/host.txt"
{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  echo "mem_total_kib $(awk '/^MemTotal/{print $2}' /proc/meminfo)"
  echo "metaphlan $(command -v metaphlan)"
  echo "bowtie2_version $(bowtie2 --version 2>&1 | head -1)"
} | tee -a "$O/host.txt"
awk '/^online_cpus/{c=$2} /^physical_cores/{p=$2} END{printf "threads_per_core %d\n", (p>0)?c/p:0}' "$O/host.txt" | tee -a "$O/host.txt"

echo "== untar the database (the recipe's step) =="
mkdir -p db
T0=$(date +%s)
tar -xf "$IDX.tar" -C db "$IDX.pkl" || { echo "FAIL: could not extract $IDX.pkl"; exit 1; }
tar -xmf "${IDX}_bt2.tar" -C db --no-same-owner --no-same-permissions || { echo "FAIL: untar of the index"; exit 1; }
T1=$(date +%s)
BT2=$(find db -name '*.bt2l' | wc -l)
echo "untar_s $((T1-T0)) bt2l_files $BT2" | tee "$O/times.txt"
[ "$BT2" -ge 6 ] || { echo "FAIL: expected >=6 .bt2l files, found $BT2"; exit 1; }
df -BG /tmp | awk 'NR==2{print "tmpfs_after_untar " $3 " used of " $2}' | tee -a "$O/times.txt"

FAILS=0
for s in "$@"; do
  ACC=${s%%:*} TRUTH=${s#*:}
  S=$O/$ACC; mkdir -p "$S"
  WANT=$(cat "$ACC.reads_ge70")
  echo "== $ACC: MetaPhlAn, $T threads, truth $TRUTH =="
  # footprint every 5 s: anonymous memory (RSS, not page cache or tmpfs) and cumulative cgroup CPU
  ( while : ; do echo "$(date +%s) $(awk '/^anon /{print $2; exit}' /sys/fs/cgroup/memory.stat 2>/dev/null) $(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)" >> "$S/samples.txt"; sleep 5; done ) &
  SPID=$!
  T2=$(date +%s)
  metaphlan "${ACC}_1.fastq.gz,${ACC}_2.fastq.gz" --input_type fastq \
    --db_dir db -x "$IDX" --offline --nproc "$T" \
    --mapout "$S/mapout.bz2" -o "$S/profile.txt" > "$S/metaphlan.log" 2>&1
  RC=$?
  T3=$(date +%s)
  kill $SPID 2>/dev/null
  echo "$ACC metaphlan_rc $RC metaphlan_wall_s $((T3-T2))" | tee -a "$O/times.txt"
  awk -v a="$ACC" 'NR==1{t0=$1; u0=$3} $2>m{m=$2} {t=$1; u=$3} END{printf "%s peak_anon_gib %.2f avg_busy_cores %.2f\n", a, m/1073741824, (t>t0)?(u-u0)/1e6/(t-t0):0}' "$S/samples.txt" | tee -a "$O/times.txt"
  if [ "$RC" != 0 ] || [ ! -s "$S/profile.txt" ]; then
    tail -30 "$S/metaphlan.log"; echo "FAIL: $ACC metaphlan exit $RC"; FAILS=$((FAILS+1)); continue
  fi

  echo "== $ACC: score -- reads, conservation, the closed species list =="
  python3 -u score.py "$S/profile.txt" "$TRUTH" "$WANT" "$S" || FAILS=$((FAILS+1))

  echo "== $ACC: fingerprints for the cross-chip comparison =="
  # The profile's '#' header echoes the binary path, which differs by image; the body is the
  # result. bowtie2 -p writes reads in thread order, so the mapout is compared as a sorted multiset.
  python3 -u - "$S/profile.txt" "$S/mapout.bz2" <<'PY' | tee "$S/fingerprints.txt"
import bz2, hashlib, sys
body = [l for l in open(sys.argv[1]) if not l.startswith("#")]
print("profile_body_sha256 %s rows %d" % (hashlib.sha256("".join(body).encode()).hexdigest(), len(body)))
rec = [l for l in bz2.open(sys.argv[2], "rt") if not l.startswith("#")]
reads = len(set(l.split("\t")[0] for l in rec))
rec.sort()
print("mapout_sorted_sha256 %s lines %d distinct_reads %d" % (hashlib.sha256("".join(rec).encode()).hexdigest(), len(rec), reads))
PY
done
echo "failures $FAILS" | tee -a "$O/times.txt"
[ "$FAILS" = 0 ] || exit 1
echo "METAPHLAN-REAL OK"
