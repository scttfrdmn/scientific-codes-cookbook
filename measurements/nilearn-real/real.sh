#!/usr/bin/env bash
# nilearn-real: the host facts, a footprint sampler, then real.py under a wall clock.
#
#   bash real.sh <data dir> <workers> [stride]
#
# The data dir carries abide.sha256 (every staged object's S3-computed sha256); all of it is checked,
# in parallel, before anything reads the data. stride > 1 (sizing only) processes every k-th subject.
#
# Runs on the aarch.science `neuroimaging` image (arm64) and on its linux-64 env twin (x86), so it
# uses only /proc, /sys and coreutils. Everything lands in /tmp/out/, which the spec stages out.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:$PATH TMPDIR=/tmp HOME=/tmp MPLCONFIGDIR=/tmp/mpl MPLBACKEND=Agg
DATA=$1 W=$2 STRIDE=${3:-1}
O=/tmp/out
mkdir -p "$O"
cd /tmp

# every staged byte against its pin, W at a time; a missing file fails too
V0=$(date +%s)
( cd "$DATA" && awk '{print $2}' abide.sha256 | xargs -P "$W" -n 40 sha256sum ) | sort -k2 > "$O/staged.sha256"
sort -k2 "$DATA/abide.sha256" > /tmp/pins.sorted
cmp -s /tmp/pins.sorted "$O/staged.sha256" \
  || { echo "FAIL: staged bytes do not match abide.sha256"; diff /tmp/pins.sorted "$O/staged.sha256" | head; exit 1; }
echo "verify_s $(( $(date +%s) - V0 ))" > "$O/verify.txt"
echo "pins: $(wc -l < "$O/staged.sha256") staged objects match abide.sha256 ($(cat "$O/verify.txt"))"

{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  grep MemTotal /proc/meminfo
  df -k /tmp | awk 'NR==2{printf "tmp_fs_gib %.1f used_gib %.1f\n", $2/1048576, $3/1048576}'
  # conda's OpenBLAS is DYNAMIC_ARCH: the kernel it picks on this host
  python3 -c "
import ctypes, numpy, threadpoolctl
for p in threadpoolctl.threadpool_info():
    if p.get('internal_api') == 'openblas':
        lib = ctypes.CDLL(p['filepath']); lib.openblas_get_corename.restype = ctypes.c_char_p
        print('openblas', p.get('version'), lib.openblas_get_corename().decode())
" 2>&1
} > "$O/host.txt" 2>&1
awk '/^online_cpus/{c=$2} /^physical_cores/{p=$2} END{printf "threads_per_core %d\n", (p>0)?c/p:0}' "$O/host.txt" >> "$O/host.txt"
cat "$O/host.txt"

# footprint every 5 s: anonymous memory (RSS, not page cache or tmpfs), shmem (the staged tmpfs) and
# cumulative cgroup CPU
( while : ; do echo "$(date +%s) $(awk '/^anon /{print $2; exit}' /sys/fs/cgroup/memory.stat 2>/dev/null) $(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)" >> "$O/samples.txt"; sleep 5; done ) &
SPID=$!

# one BLAS/OpenMP thread per worker process: W processes each bringing a pool as wide as the box
# oversubscribe it (recipes/nilearn measured 13 s -> 3,824 s)
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
T0=$(date +%s)
python3 -u real.py "$DATA" "$O" "$W" "$STRIDE" > "$O/real.out" 2>&1
RC=$?
T1=$(date +%s)
kill $SPID 2>/dev/null
{
  echo "real_exit $RC"
  echo "real_wall_s $((T1-T0))"
  awk 'NR==1{t0=$1; u0=$3} $2>m{m=$2} {t=$1; u=$3} END{printf "peak_anon_gib %.2f\navg_busy_cores %.2f\n", m/1073741824, (t>t0)?(u-u0)/1e6/(t-t0):0}' "$O/samples.txt"
} | tee "$O/times.txt"
tail -n 40 "$O/real.out"
[ "$RC" = 0 ] || { echo "FAIL: real.py exited $RC"; exit 1; }
grep -q '^NILEARN REAL OK$' "$O/real.log" || { echo "FAIL: no completion sentinel"; exit 1; }
test -s "$O/score.tsv" && test -s "$O/subjects.tsv" || { echo "FAIL: an output is missing or empty"; exit 1; }
echo "NILEARN REAL OK"
