#!/usr/bin/env bash
# Task 02: pangolin on the whole query, and on the first SUB genomes alone at 1 thread, then every
# check (check.py). Both runs pass --skip-designation-cache, so every genome is placed by UShER:
# pangolin's default would hand a genome whose aligned bytes hash to a designated sequence that
# designation by lookup, which for this query is most of it. The lookup's labels are kept anyway
# (designation_status.csv, from --no-temp) and compared, not used.
# Work directories go to /var/tmp (the root disk): the alignment, its hashed copy and the VCF of a
# 100k-genome batch are GB each, and /tmp is a tmpfs of half the RAM.
#   pangolin.sh <threads> [query, default query.fasta.gz]
set +e
export PATH=/opt/conda/bin:$PATH HOME=/tmp TMPDIR=/var/tmp XDG_CACHE_HOME=/tmp/cache
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
# usher-sampled is linked against Open MPI, whose singleton start-up looks for ssh/rsh and aborts
# in a container that has neither. Then `usher-sampled --help` exits non-zero and pangolin falls
# back to the original `usher`, printing "usher-sampled is not installed". Any existing path
# satisfies the check; nothing is ever launched through it.
export OMPI_MCA_plm_rsh_agent=false
mkdir -p /tmp/cache /tmp/out
cd /tmp
T="$1"; export QUERY="${2:-query.fasta.gz}"   # a sizing probe passes sub.fasta.gz
: > out/pangolin_full.log; : > out/pangolin_sub_t1.log; : > out/check.log

{ echo "arch $(uname -m)"; echo "nproc_all $(nproc --all 2>/dev/null || nproc)"
  (lscpu 2>/dev/null | grep -E "^(Model name|Thread\(s\) per core|Core\(s\) per|Socket|NUMA node\(s\))") \
    || grep -m1 -iE "model name|cpu part" /proc/cpuinfo
  echo "threads_per_core_cpu0 $(cat /sys/devices/system/cpu/cpu0/topology/thread_siblings_list)"
  grep MemTotal /proc/meminfo
  pangolin --all-versions 2>&1; } > out/host.txt
cat out/host.txt

echo "== staged bytes must match their pins =="
grep -E "  (query.fasta.gz|sub.fasta.gz|truth.tsv)$" inputs.sha256 > q.sha256; sha256sum -c q.sha256 || { echo "FAIL: query does not match its pin"; exit 1; }

run() {   # label threads fasta
  local label="$1" t="$2" fa="$3" d="/var/tmp/p_$1"
  mkdir -p "$d"
  python3 -u timed.py out/timing.tsv "pangolin_$label" "out/pangolin_$label.log" -- \
    pangolin "$fa" --outdir "$d" --outfile "pangolin_$label.csv" --no-temp --threads "$t" --skip-designation-cache
  local rc=$?
  for f in "pangolin_$label.csv" designation_status.csv hash_map.csv; do cp "$d/$f" "out/${label}_$f" 2>/dev/null; done
  cp "$d/logs/usher.log" "out/${label}_usher.log" 2>/dev/null
  mv "out/${label}_pangolin_$label.csv" "out/pangolin_$label.csv" 2>/dev/null
  printf 'pangolin_%s_usher_invocations\t%s\n' "$label" "$(grep -c 'Using UShER as inference engine' "out/pangolin_$label.log")" >> out/timing.tsv
  test "$rc" = "0" || { tail -30 "out/pangolin_$label.log"; echo "FAIL: pangolin $label exited $rc"; exit 1; }
  if grep -q "usher-sampled is not installed" "out/pangolin_$label.log"; then
    echo "FAIL: pangolin $label fell back to the original usher"; exit 1
  fi
  rm -rf "$d"
}
echo "== the whole query, $T threads =="
run full "$T" "$QUERY"
echo "== the first genomes alone, 1 thread =="
run sub_t1 1 sub.fasta.gz
printf "cgroup_memory_peak_bytes\t%s\n" "$(cat /sys/fs/cgroup/memory.peak 2>/dev/null || echo NA)" >> out/timing.tsv
cat out/timing.tsv

echo "== checks =="
python3 -u check.py > out/check.log 2>&1
RC=$?
cat out/check.log
gzip -f out/pangolin_full.csv out/full_designation_status.csv out/full_hash_map.csv
test "$RC" = "0" || { echo "FAIL: check.py exited $RC"; exit 1; }
echo "PANGOLIN OK"
