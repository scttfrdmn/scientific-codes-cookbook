#!/usr/bin/env bash
# nf-spawn-real, the DIRECT run: one process of recipes/nf-spawn/main.nf run as a plain spawn task,
# with no Nextflow, on the same image, instance size and staged bytes as the engine's process.
# <step>.sh is that process's script, rendered from main.nf by render.py (params filled in, the
# way Nextflow renders .command.sh), and it is run the way nf-spawn runs .command.sh:
# `bash .command.sh 1>.command.out 2>.command.err`. This wrapper adds what main.nf cannot carry
# unchanged: tool wall, cgroup memory.peak, cores busy and the host.
#
#   bash direct.sh <step> <step>.sh <input> [<input> ...]    (inputs: already named as main.nf stages them)
#
# Writes /tmp/out/<step>/. Runs on aarch.bio's images and on bioconda's BusyBox x86 ones: POSIX glue.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
STEP=$1 SCRIPT=$2
shift 2
O=/tmp/out/$STEP
W=/tmp/work-$STEP
mkdir -p "$O" "$W"
for f in "$@"; do cp "/tmp/$f" "$W/$f"; done
cp "/tmp/$SCRIPT" "$W/.command.sh"
cd "$W"

{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  echo "mem_total_kib $(awk '/^MemTotal/{print $2}' /proc/meminfo)"
  for t in mafft muscle iqtree3; do
    b=$(command -v $t) || continue
    echo "$t $b"
    echo "$t links: $( (ldd "$b" 2>&1 || echo none) | awk '{printf "%s ", $1}')"
  done
} > "$O/host.txt"
awk '/^online_cpus/{c=$2} /^physical_cores/{p=$2} END{printf "threads_per_core %d\n", (p>0)?c/p:0}' "$O/host.txt" >> "$O/host.txt"

ls -A > before.lst
cpu_usec() { awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null; }
S=$(date +%s); C0=$(cpu_usec)
bash .command.sh 1>.command.out 2>.command.err
RC=$?
E=$(date +%s); C1=$(cpu_usec)
PK=$(cat /sys/fs/cgroup/memory.peak 2>/dev/null || echo 0)
{
  echo "rc $RC"
  echo "tool_wall_s $((E-S))"
  echo "memory_peak_kib $((PK/1024))"
  awk -v a="${C0:-0}" -v b="${C1:-0}" -v w=$((E-S)) 'BEGIN{ if (w>0) printf "avg_cores_busy %.2f\n", (b-a)/1e6/w }'
  grep -h -m1 -E '^Kernel:' ./*.log 2>/dev/null
} > "$O/metrics.txt"
# everything the script wrote, plus its streams and the script itself
ls -A | grep -v -x -F -f before.lst | grep -v -x -e before.lst > new.lst
while read -r f; do cp -r "$f" "$O/"; done < new.lst
cp .command.sh .command.out .command.err "$O/"
(cd "$O" && sha256sum $(cat "$W/new.lst") .command.sh) > "$O/sha256.txt" 2>/dev/null
echo "exitcode $RC" > "$O/.exitcode.txt"
cat "$O/host.txt" "$O/metrics.txt" "$O/.command.out" "$O/sha256.txt"
exit "$RC"
