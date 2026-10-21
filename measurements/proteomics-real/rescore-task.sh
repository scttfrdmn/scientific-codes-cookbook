#!/usr/bin/env bash
# Task body, staged to the bucket and sha256-checked by its spec (spawn user data is capped at 16 KiB).
# == topology and BLAS kernel: diagnostic only, never fails the task ==
( echo "topo_model $(awk -F': ' '/^model name/{print $2; exit}' /proc/cpuinfo)"
  echo "topo_siblings $(awk -F': ' '/^siblings/{print $2; exit}' /proc/cpuinfo) topo_cpu_cores $(awk -F': ' '/^cpu cores/{print $2; exit}' /proc/cpuinfo)"
  echo "topo_cpu0_thread_siblings $(cat /sys/devices/system/cpu/cpu0/topology/thread_siblings_list)"
  echo "topo_lscpu_threads_per_core $(lscpu 2>/dev/null | awk -F: '/per core/{gsub(/ /,"",$2); print $2}')"
  for b in percolator; do p=/opt/conda/bin/$b; [ -x "$p" ] || continue
    echo "blas_link $b: $(ldd "$p" 2>/dev/null | grep -i -E 'blas|lapack' | awk '{print $1}' | tr '\n' ' ')"
    OPENBLAS_VERBOSE=2 "$p" --version 2>&1 | grep -i -E '^core' | sed "s/^/openblas_core $b: /"
  done
  ls /opt/conda/lib/ 2>/dev/null | grep -i openblas | sed 's/^/openblas_lib /' ) 2>&1 || true
set +e
set -uo pipefail
# spawn runs task commands under bash -e; set -uo pipefail does not clear it (spawn#707).
export PATH=/opt/conda/bin:$PATH
export TMPDIR=/tmp HOME=/tmp
cd /tmp
mkdir -p out
echo "== staged bytes must match their pins =="
# Only the pinned files this task staged are checked (BusyBox sha256sum in the x86 image has no
# --ignore-missing), and every input the run list needs must be among them, or the filter passed
# vacuously.
while read -r h f; do test -e "$f" && printf '%s  %s\n' "$h" "$f"; done < pins.sha256 > pins.staged
sha256sum -c pins.staged || { echo "FAIL: a staged file does not match its pin"; exit 1; }
for f in rescore.sh $(for r in $(cat runs.txt); do printf 'ref/ref_%s.tsv ' "$r"; done); do grep -q "  $f\$" pins.staged || { echo "FAIL: $f was not verified"; exit 1; }; done
echo "pinned_files_verified	$(wc -l < pins.staged | tr -d ' ')"
grep -m1 -iE 'model name|CPU part' /proc/cpuinfo | sed 's/^/cpu\t/'; nproc | sed 's/^/nproc\t/'
T0=$(date +%s)
bash rescore.sh > rescore-run.log 2>&1
RC=$?
echo "rescore.sh exit $RC in $(( $(date +%s) - T0 ))s"
cp rescore-run.log out/
grep -v '^  ' rescore-run.log
test "$RC" = 0 || { echo "FAIL: the rescoring task failed"; exit 1; }
grep -q 'RESCORE OK' out/score-percolator.tsv || { echo "FAIL: no completion sentinel"; exit 1; }
uname -m | awk '{print "arch\t"$1}'
echo DONE
