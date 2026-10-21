#!/usr/bin/env bash
# Task body, staged to the bucket and sha256-checked by its spec (spawn user data is capped at 16 KiB).
# == topology and BLAS kernel: diagnostic only, never fails the task ==
( echo "topo_model $(awk -F': ' '/^model name/{print $2; exit}' /proc/cpuinfo)"
  echo "topo_siblings $(awk -F': ' '/^siblings/{print $2; exit}' /proc/cpuinfo) topo_cpu_cores $(awk -F': ' '/^cpu cores/{print $2; exit}' /proc/cpuinfo)"
  echo "topo_cpu0_thread_siblings $(cat /sys/devices/system/cpu/cpu0/topology/thread_siblings_list)"
  echo "topo_lscpu_threads_per_core $(lscpu 2>/dev/null | awk -F: '/per core/{gsub(/ /,"",$2); print $2}')"
  for b in plink; do p=/opt/conda/bin/$b; [ -x "$p" ] || continue
    echo "blas_link $b: $(ldd "$p" 2>/dev/null | grep -i -E 'blas|lapack' | awk '{print $1}' | tr '\n' ' ')"
    OPENBLAS_VERBOSE=2 "$p" --version 2>&1 | grep -i -E '^core' | sed "s/^/openblas_core $b: /"
  done
  ls /opt/conda/lib/ 2>/dev/null | grep -i openblas | sed 's/^/openblas_lib /' ) 2>&1 || true
set +e
set -uo pipefail
# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
export PATH=/opt/conda/bin:$PATH TMPDIR=/tmp HOME=/tmp CAUSAL=22:49552130:A:G
cd /tmp/in
mkdir -p /tmp/out
CHROMS="1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22"
echo "== staged bytes must match their pins =="
for c in $CHROMS; do grep -F ".chr$c.phase3" pins.sha256; done > /tmp/pins.sel
grep -E 'panel|noise|real.sh' pins.sha256 >> /tmp/pins.sel
sha256sum -c /tmp/pins.sel > /tmp/out/pins.check 2>&1 || { cat /tmp/out/pins.check; echo "FAIL: a staged input does not match its pin"; exit 1; }
echo "$(wc -l < /tmp/pins.sel) files match their pins"
# footprint, every 5 s: anonymous memory (RSS, not page cache), the container-disk work dir,
# and cumulative CPU (cpu.stat usage_usec), from which the average busy cores follow
( while : ; do echo "$(date +%s) $(awk '/^(anon|rss) /{print $2; exit}' /sys/fs/cgroup/memory.stat 2>/dev/null) $(du -sk /var/tmp/plink-real 2>/dev/null | cut -f1) $(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null) $(awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8, $6}' /proc/stat)" >> /tmp/out/samples.txt; sleep 5; done ) &
SPID=$!
T0=$(date +%s)
bash real.sh /tmp/in $CHROMS > /tmp/out/real.log 2>&1
RC=$?
echo "real.sh exit $RC in $(( $(date +%s) - T0 ))s"
kill $SPID
awk 'NR==1{t0=$1; u0=$4; j0=$5; w0=$6} $2>m{m=$2} $3>d{d=$3} {t=$1; u=$4; j=$5; w=$6} END{printf "peak_anon_gib %.2f\npeak_workdir_gib %.1f\navg_busy_cores %.2f\nhost_iowait_fraction %.3f\n", m/1073741824, d/1048576, (t>t0)?(u-u0)/1e6/(t-t0):0, (j>j0)?(w-w0)/(j-j0):0}' /tmp/out/samples.txt | tee /tmp/out/footprint.txt
cat /tmp/out/real.log
test "$RC" = 0 || { echo "FAIL: the workload failed"; exit 1; }
grep -q 'PLINK REAL OK' /tmp/out/real.log || { echo "FAIL: no completion sentinel"; exit 1; }
echo DONE
