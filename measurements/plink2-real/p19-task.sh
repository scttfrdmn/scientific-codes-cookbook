#!/usr/bin/env bash
# Task body, staged to the bucket and sha256-checked by its spec (spawn user data is capped at 16 KiB).
# == topology and BLAS kernel: diagnostic only, never fails the task ==
( echo "topo_model $(awk -F': ' '/^model name/{print $2; exit}' /proc/cpuinfo)"
  echo "topo_siblings $(awk -F': ' '/^siblings/{print $2; exit}' /proc/cpuinfo) topo_cpu_cores $(awk -F': ' '/^cpu cores/{print $2; exit}' /proc/cpuinfo)"
  echo "topo_cpu0_thread_siblings $(cat /sys/devices/system/cpu/cpu0/topology/thread_siblings_list)"
  echo "topo_lscpu_threads_per_core $(lscpu 2>/dev/null | awk -F: '/per core/{gsub(/ /,"",$2); print $2}')"
  for b in plink; do p=$(PATH=/opt/conda/bin:$PATH command -v $b) || continue
    echo "blas_link $b: $(ldd "$p" 2>/dev/null | grep -i -E 'blas|lapack' | awk '{print $1}' | tr '\n' ' ')"
    OPENBLAS_VERBOSE=2 "$p" --version 2>&1 | grep -i -E '^core' | sed "s/^/openblas_core $b: /"
  done
  ls /opt/conda/lib/ /usr/local/lib/ 2>/dev/null | grep -i openblas | sed 's/^/openblas_lib /' ) 2>&1 || true
set +e
cd /tmp
# inputs arrive as staged directories; p19.sh reads them from /tmp, so link them there
ln -s /tmp/in/ALL.chr*.vcf.gz /tmp/in2/vcf.sha256 /tmp/in2/panel /tmp/in2/p19.sh /tmp/
for f in p2.counts.tsv.gz dup.txt prune.prune.in causal.txt pheno.txt covar.txt pcax.eigenval glm.causal.tsv; do ln -s "/tmp/p2/$f" /tmp/; done
echo "d372d22b57836c79069971f419e56fc83b79c2ea84b2a6880d8e9203102c7bb3  p19.sh" | sha256sum -c > /dev/null || { echo "FAIL: p19.sh does not match its pin"; exit 1; }
bash /tmp/p19.sh 8
RC=$?
mkdir -p /tmp/out
for f in score.tsv logs19.tar pca19.eigenval lin.assoc.linear; do [ -e "/tmp/$f" ] && mv "/tmp/$f" /tmp/out/; done
exit $RC
