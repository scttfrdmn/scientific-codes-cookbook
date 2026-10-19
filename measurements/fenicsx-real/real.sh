#!/usr/bin/env bash
# fenicsx-real task body: a serial pass on the lower rungs, then the full ladder over NP ranks.
#
#   bash real.sh <NP> "<serial P1 N>" "<serial P2 N>" "<par P1 N>" "<par P2 N>"
#
# Writes everything to /tmp/out/, which the spec stages out as a directory.
set +e
set -uo pipefail
NP="$1"; S1="$2"; S2="$3"; P1="$4"; P2="$5"
export TMPDIR=/tmp HOME=/tmp
export OMPI_ALLOW_RUN_AS_ROOT=1 OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1
export OMPI_MCA_btl_vader_single_copy_mechanism=none
# Docker gives /dev/shm 64 MB; Open MPI's shared-memory segments go to /tmp instead (spawn#1111)
export OMPI_MCA_btl_sm_backing_directory=/tmp
# one thread per rank: no OpenMP or OpenBLAS threads under MPI
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
OUT=/tmp/out; mkdir -p "$OUT"; export OUT
cd /tmp

{
  echo "uname_m	$(uname -m)"
  echo "nproc	$(nproc --all 2>/dev/null || echo unknown)"
  grep -m1 -E '^(model name|CPU part)' /proc/cpuinfo
  grep MemTotal /proc/meminfo
  command -v lscpu >/dev/null && lscpu
} > "$OUT/host.txt" 2>&1

t0=$(date +%s)
TAG=serial EXPECTED_RANKS=1 P1_LADDER="$S1" P2_LADDER="$S2" CROSS=0 \
  python3 -u poisson3d.py > "$OUT/serial.log" 2>&1
s1=$?
t1=$(date +%s)
echo "serial pass exit $s1 in $((t1 - t0)) s"; tail -3 "$OUT/serial.log"

s2=99
if [ "$s1" = 0 ]; then
  TAG=par EXPECTED_RANKS="$NP" P1_LADDER="$P1" P2_LADDER="$P2" CROSS=1 \
    mpiexec -n "$NP" --oversubscribe python3 -u poisson3d.py > "$OUT/par.log" 2>&1
  s2=$?
fi
t2=$(date +%s)
echo "parallel pass exit $s2 in $((t2 - t1)) s"; tail -25 "$OUT/par.log"

peak=$(cat /sys/fs/cgroup/memory.peak 2>/dev/null || echo unknown)
printf 'serial_exit\t%s\nserial_s\t%s\npar_exit\t%s\npar_s\t%s\ncgroup_memory_peak_bytes\t%s\n' \
  "$s1" "$((t1 - t0))" "$s2" "$((t2 - t1))" "$peak" > "$OUT/times.tsv"
cat "$OUT/times.tsv"

test "$s1" = 0 || { echo "FAIL: serial pass"; exit 1; }
test "$s2" = 0 || { echo "FAIL: parallel pass"; exit 1; }
grep -q "^mpi_comm_world_size	$NP$" "$OUT/par.log" || { echo "FAIL: rank count $NP not reported"; exit 1; }
grep -q "^identity_two_preconditioners" "$OUT/par.log" || { echo "FAIL: no preconditioner cross-check"; exit 1; }
grep -q "^identity_serial_equals_parallel" "$OUT/par.log" || { echo "FAIL: no serial cross-check"; exit 1; }
grep -q "^identity_convergence_order" "$OUT/par.log" || { echo "FAIL: no convergence order"; exit 1; }
echo "DONE"
