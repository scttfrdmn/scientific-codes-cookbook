#!/usr/bin/env bash
# measurements/pointcloud-real: a whole USGS 3DEP project through PDAL, then laspy (real.py).
#
#   bash real.sh <workers>
#
# Runs on the aarch.science pointcloud image (arm64) and on its linux-64 env twin (x86), so it uses
# only /proc, /sys, coreutils and python. Everything kept lands in /tmp/out/, which the spec stages
# out; the tile LAZ files and DTM tiles are built on the root disk (/var/tmp/w), not the tmpfs.
set +e
set -uo pipefail
W=$1
O=/tmp/out
mkdir -p "$O" /var/tmp/w
cd /tmp
export HOME=/tmp TMPDIR=/var/tmp LC_ALL=C
# the tile jobs and the laspy workers are the parallelism: one BLAS / OpenMP / GDAL thread each
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 GDAL_NUM_THREADS=1
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
for m in ("laspy", "lazrs", "numpy", "rasterio", "osgeo.gdal"):
    try:
        mod = importlib.import_module(m)
        print(m + "\t" + str(getattr(mod, "__version__", "unknown")))
    except Exception as e:
        print(m + "\tunavailable " + str(type(e).__name__))
' 2>&1
  df -h /tmp /var/tmp | sed 's/^/df	/'
} > "$O/host.txt" 2>&1
cat "$O/host.txt"

T0=$(date +%s)
python3 -u /tmp/real.py /tmp/pins.tsv /var/tmp/w "$O" "$W" > "$O/real.log" 2>&1
RC=$?
T1=$(date +%s)
cat "$O/real.log"
echo "real_py_wall_s	$((T1 - T0))" >> "$O/host.txt"
echo "cgroup_memory_peak	$(cat /sys/fs/cgroup/memory.peak 2>/dev/null)" >> "$O/host.txt"
df -h /tmp /var/tmp | sed 's/^/df_end	/' >> "$O/host.txt"
cat /var/tmp/w/*.log > "$O/pdal.log" 2>/dev/null
[ "$RC" = 0 ] || { echo "FAIL: real.py exit $RC"; exit 1; }
grep -q 'POINTCLOUD REAL OK' "$O/real.log" || { echo "FAIL: no completion sentinel"; exit 1; }
echo DONE
