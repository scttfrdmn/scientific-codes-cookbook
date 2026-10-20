#!/usr/bin/env bash
# openfoam-real: NASA TMR's NACA 0012 SA case on the 897x257 grid, rhoSimpleFoam, alpha 10 and 15.
#
#   bash run.sh <ranks> <max_iterations>
#
# Runs in the pinned opencfd/openfoam-default image (arm64 and amd64 entries of one manifest list).
# The image has no python, so the glue is bash + awk + coreutils. Everything lands in /tmp/out/.
set +e
source /usr/lib/openfoam/openfoam2412/etc/bashrc     # before `set -u`: it reads unset variables
set -uo pipefail
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 OMPI_MCA_btl_sm_backing_directory=/tmp
R=$1
N=$2
O=/tmp/out
W=/var/tmp/work
mkdir -p "$O" "$W"
cd /tmp

{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  grep MemTotal /proc/meminfo
  echo "rhoSimpleFoam $(command -v rhoSimpleFoam)"
  echo "rhoSimpleFoam_elf_machine $(od -An -t u2 -j 18 -N 2 "$(command -v rhoSimpleFoam)" | tr -d ' ')"
  echo "mpirun $(mpirun --version 2>&1 | head -n 1)"
  echo "openfoam_build $WM_PROJECT_VERSION $WM_OPTIONS"
} > "$O/host.txt" 2>&1
awk '/^online_cpus/{c=$2} /^physical_cores/{p=$2} END{printf "threads_per_core %d\n", (p>0)?c/p:0}' "$O/host.txt" >> "$O/host.txt"
cat "$O/host.txt"

U0=$(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)
T0=$(date +%s)
bash /tmp/case.sh "$R" "$N" > "$O/case.out" 2>&1
RC=$?
T1=$(date +%s)
U1=$(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)
{
  echo "case_exit $RC"
  echo "case_wall_s $((T1-T0))"
  # cgroup v2 high-water mark of the container's memory, not a periodic sample
  echo "cgroup_memory_peak_gib $(awk '{printf "%.2f", $1/1073741824}' /sys/fs/cgroup/memory.peak 2>/dev/null)"
  awk -v a="$U0" -v b="$U1" -v t=$((T1-T0)) 'BEGIN{if (t>0 && b>a) printf "avg_busy_cores %.2f\n", (b-a)/1e6/t}'
} | tee "$O/times.txt"
tail -n 60 "$O/case.out"
[ "$RC" = 0 ] || { echo "FAIL: case.sh exited $RC"; exit 1; }
grep -q 'OPENFOAM REAL OK' "$O/case.out" || { echo "FAIL: no completion sentinel"; exit 1; }
test -s "$O/check.txt" || { echo "FAIL: check.txt missing or empty"; exit 1; }
echo "OPENFOAM REAL OK"
