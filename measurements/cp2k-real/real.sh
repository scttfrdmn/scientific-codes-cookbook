#!/usr/bin/env bash
# cp2k-real: host facts, then real.py under a wall clock, then the footprint.
#
#   bash real.sh <data dir> <jobset>        jobset: probe | full
#
# Runs on the aarch.science `cp2k` image (arm64) and on its linux-64 env twin (x86), so it uses
# only /proc, /sys and coreutils. Everything lands in /tmp/out/, which the spec stages out.
set +e
set -uo pipefail
export TMPDIR=/tmp HOME=/tmp
# Open MPI as root in a container; /dev/shm is 64 MB under Docker, so put the shared-memory
# backing files on /tmp (spawn#1111).
export OMPI_ALLOW_RUN_AS_ROOT=1 OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1
export OMPI_MCA_btl_vader_single_copy_mechanism=none OMPI_MCA_btl_sm_backing_directory=/tmp
P="${CONDA_PREFIX:-/opt/conda}"
# The x86 twin has no Dockerfile to export CP2K_DATA_DIR; the arm64 image does (aarchsci#30).
if [ -z "${CP2K_DATA_DIR:-}" ]; then export CP2K_DATA_DIR="$P/share/cp2k/data"; fi
D=$1 J=$2
O=/tmp/out
mkdir -p "$O" /var/tmp/cp2k-real
cd /tmp

{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  grep MemTotal /proc/meminfo
  echo "CP2K_DATA_DIR $CP2K_DATA_DIR ($(ls "$CP2K_DATA_DIR" | wc -l) files)"
  for f in BASIS_MOLOPT_UZH POTENTIAL_UZH BASIS_ADMM_UZH BASIS_ccGRB_UZH GTH_BASIS_SETS POTENTIAL; do
    test -s "$CP2K_DATA_DIR/$f" && echo "data_file $f present" || echo "data_file $f MISSING"
  done
  echo "cp2k $(command -v cp2k.psmp)"
  echo "mpiexec $(mpiexec --version 2>&1 | head -n 1)"
  df -h /tmp /var/tmp | sed 's/^/df /'
} > "$O/host.txt" 2>&1
awk '/^online_cpus/{c=$2} /^physical_cores/{p=$2} END{printf "threads_per_core %d\n", (p>0)?c/p:0}' "$O/host.txt" >> "$O/host.txt"
cat "$O/host.txt"
grep -q MISSING "$O/host.txt" && { echo "FAIL: a basis/potential file the inputs name is absent"; exit 1; }

U0=$(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)
T0=$(date +%s)
CP2K_REAL_CPUS="$(grep -c '^processor' /proc/cpuinfo)" python3 -u real.py "$D" "$O" "$J" > "$O/real.out" 2>&1
RC=$?
T1=$(date +%s)
U1=$(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)
{
  echo "real_py_exit $RC"
  echo "real_py_wall_s $((T1-T0))"
  # cgroup v2 high-water mark of the container's memory (all ranks together), not a sample
  echo "cgroup_memory_peak_gib $(awk '{printf "%.2f", $1/1073741824}' /sys/fs/cgroup/memory.peak 2>/dev/null)"
  awk -v a="$U0" -v b="$U1" -v t=$((T1-T0)) 'BEGIN{if (t>0 && b>a) printf "avg_busy_cores %.2f\n", (b-a)/1e6/t}'
} | tee "$O/times.txt"
tail -n 30 "$O/real.out"
[ "$RC" = 0 ] || { echo "FAIL: real.py exited $RC"; exit 1; }
grep -q 'CP2K REAL OK' "$O/real.log" || { echo "FAIL: no completion sentinel"; exit 1; }
test -s "$O/runs.tsv" || { echo "FAIL: runs.tsv missing or empty"; exit 1; }
echo "CP2K REAL OK"
