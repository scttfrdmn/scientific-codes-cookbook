#!/usr/bin/env bash
# measurements/laspy-real: laspy over the 128 source LAZ tiles of 3DEP CA_LassenNP_2020 (real.py).
#
#   bash real.sh <workers>
#
# Runs on the aarch.science pointcloud image (arm64) and on its linux-64 env twin (x86), so it uses
# only /proc, /sys, coreutils and python. The tiles are staged to /tmp/laz (tmpfs); the LAZ laspy
# writes goes to the root disk (/var/tmp/w), and only the evidence in /tmp/out is staged out.
set +e
set -uo pipefail
W=$1
O=/tmp/out
mkdir -p "$O" /var/tmp/w
cd /tmp
export HOME=/tmp TMPDIR=/var/tmp LC_ALL=C
# the tile workers are the parallelism: one BLAS / OpenMP thread each
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
{
  echo "uname_m	$(uname -m)"
  echo "online_cpus	$(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores	$(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1"\t"$2}' /proc/cpuinfo | sort | uniq -c
  (lscpu 2>/dev/null | grep -E '^(Model name|Thread\(s\) per core|Core\(s\) per socket|Socket\(s\)|NUMA node\(s\)):' | sed 's/:  */	/') || echo "lscpu	absent"
  grep MemTotal /proc/meminfo
  echo "pdal_version	$(pdal --version 2>&1 | tr -s '\n -' ' ' | head -c 200)"
  python3 -u -c '
import importlib
for m in ("laspy", "lazrs", "numpy", "pyproj"):
    try:
        mod = importlib.import_module(m)
        print(m + "\t" + str(getattr(mod, "__version__", "unknown")))
    except Exception as e:
        print(m + "\tunavailable " + str(type(e).__name__))
' 2>&1
  df -h /tmp /var/tmp | sed 's/^/df	/'
  echo "staged_laz	$(ls /tmp/laz/*.laz | wc -l) files, $(du -sb /tmp/laz | cut -f1) bytes"
} > "$O/host.txt" 2>&1
cat "$O/host.txt"

T0=$(date +%s)
python3 -u /tmp/real.py /tmp/laz /var/tmp/w "$O" "$W" > "$O/real.log" 2>&1
RC=$?
T1=$(date +%s)
cat "$O/real.log"
echo "real_py_wall_s	$((T1 - T0))" >> "$O/host.txt"
echo "cgroup_memory_peak	$(cat /sys/fs/cgroup/memory.peak 2>/dev/null)" >> "$O/host.txt"
df -h /tmp /var/tmp | sed 's/^/df_end	/' >> "$O/host.txt"
du -sb /var/tmp/w | sed 's/^/written_bytes	/' >> "$O/host.txt"
cat /var/tmp/w/*.pdal.log > "$O/pdal.log" 2>/dev/null
rm -rf /var/tmp/w
[ "$RC" = 0 ] || { echo "FAIL: real.py exit $RC"; exit 1; }
grep -q 'LASPY REAL OK' "$O/real.log" || { echo "FAIL: no completion sentinel"; exit 1; }
echo DONE
