#!/usr/bin/env bash
# Task body, staged to the bucket and sha256-checked by its spec (spawn user data is capped at 16 KiB).
# == topology and BLAS kernel: diagnostic only, never fails the task ==
( echo "topo_model $(awk -F': ' '/^model name/{print $2; exit}' /proc/cpuinfo)"
  echo "topo_siblings $(awk -F': ' '/^siblings/{print $2; exit}' /proc/cpuinfo) topo_cpu_cores $(awk -F': ' '/^cpu cores/{print $2; exit}' /proc/cpuinfo)"
  echo "topo_cpu0_thread_siblings $(cat /sys/devices/system/cpu/cpu0/topology/thread_siblings_list)"
  echo "topo_lscpu_threads_per_core $(lscpu 2>/dev/null | awk -F: '/per core/{gsub(/ /,"",$2); print $2}')"
  for b in plink2; do p=$(PATH=/opt/conda/bin:$PATH command -v $b) || continue
    echo "blas_link $b: $(ldd "$p" 2>/dev/null | grep -i -E 'blas|lapack' | awk '{print $1}' | tr '\n' ' ')"
    OPENBLAS_VERBOSE=2 "$p" --version 2>&1 | grep -i -E '^core' | sed "s/^/openblas_core $b: /"
  done
  ls /opt/conda/lib/ /usr/local/lib/ 2>/dev/null | grep -i openblas | sed 's/^/openblas_lib /' ) 2>&1 || true
set +e
cd /tmp
# inputs arrive as two staged directories; p2.sh reads them from /tmp, so link them there
ln -s /tmp/in/ALL.chr*.vcf.gz /tmp/in2/vcf.sha256 /tmp/in2/panel /tmp/in2/p2.sh /tmp/
echo "4fa7913f9b01540cb3ed2cda033dc433dd7fd046a549fd22f3a184aaaeba2fae  p2.sh" | sha256sum -c > /dev/null || { echo "FAIL: p2.sh does not match its pin"; exit 1; }
mkdir -p /tmp/out
# footprint every 5 s (diagnostic only): tmpfs used/free MiB, MemAvailable KiB, container cgroup memory
( while :; do echo "$(date +%s) $(df -m /tmp | awk 'NR==2{print $3, $4}') $(awk '/^MemAvailable/{print $2}' /proc/meminfo) $(cat /sys/fs/cgroup/memory.current 2>/dev/null) $(cat /sys/fs/cgroup/memory.max 2>/dev/null)" >> /tmp/out/footprint.txt; sleep 5; done ) &
SPID=$!
bash /tmp/p2.sh 16
RC=$?
kill $SPID 2>/dev/null
if [ "$RC" != 0 ]; then   # a plink2 log's tail is progress lines joined by \r: print its real last lines
  for l in /tmp/log.*.txt; do echo "== $l"; tail -c 4000 "$l" | tr '\r' '\n' | tail -8; done 2>&1 | tail -60
  cp /tmp/log.*.txt /tmp/out/ 2>/dev/null
fi
# the outputs it wrote, collected for one directory stage-out
for f in score.tsv dup.txt logs.tar p2.counts.tsv.gz prune.prune.in causal.txt beta.txt pheno.txt covar.txt pca.eigenval pca.eigenvec pcax.eigenval glm.causal.tsv recovery.tsv; do [ -e "/tmp/$f" ] && mv "/tmp/$f" /tmp/out/; done
exit $RC
