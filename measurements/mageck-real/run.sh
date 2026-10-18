#!/usr/bin/env bash
# measurements/mageck-real: a whole genome-wide CRISPR knockout screen through MAGeCK -- the
# Brunello A375 dropout screen of Sanson et al. 2018 (plasmid + 2 replicates, 252.9M reads,
# 77,441 sgRNAs) -- count, RRA test, MLE, comparisons, then an independent recount and check.py.
#   run.sh <mle threads>
# Everything the task keeps is written under /tmp/out, which the spec stages out whole.
set +e
set -uo pipefail
NT=${1:?mle threads}
export PATH=/opt/conda/bin:/usr/local/bin:$PATH HOME=/tmp TMPDIR=/tmp
# one BLAS thread per process: mle's --threads forks workers, each of which would otherwise
# start an OpenBLAS thread per core
NPROC=$(nproc)   # before OMP_NUM_THREADS, which GNU nproc honours
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
D=/tmp/data
mkdir -p /tmp/out /tmp/work
cd /tmp/work

{
  echo "arch	$(uname -m)"
  echo "nproc	$NPROC"
  lscpu 2>/dev/null | grep -E '^(Model name|Thread\(s\) per core|Core\(s\) per socket|NUMA node\(s\))' || true
  # the same facts without lscpu (BusyBox images): SMT siblings of cpu0, and the model string
  echo "cpu0_thread_siblings	$(cat /sys/devices/system/cpu/cpu0/topology/thread_siblings_list 2>/dev/null)"
  grep -m1 -iE '^model name' /proc/cpuinfo 2>/dev/null || true
  python3 -u - <<'PY'
import ctypes, glob, sys
import numpy, scipy
print("numpy\t%s\nscipy\t%s\npython\t%s" % (getattr(numpy, "__version__", "?"), getattr(scipy, "__version__", "?"), sys.version.split()[0]))
name = "none found"
for lib in sorted(glob.glob(sys.prefix + "/lib/libopenblas*.so*")):
    try:
        f = ctypes.CDLL(lib).openblas_get_corename
        f.restype = ctypes.c_char_p
        name = str(f().decode()) + " (" + lib.split("/")[-1] + ")"
        break
    except Exception as e:
        name = "unreadable: " + str(e)
print("openblas_corename\t" + name)
PY
  echo "mageck	$(mageck --version 2>&1 | tail -1)"
} > /tmp/out/host.txt 2>&1
cat /tmp/out/host.txt

echo "== staged inputs must be the pinned bytes =="
# (no --quiet: the x86 image's BusyBox sha256sum does not take it)
( cd $D && sha256sum -c /tmp/inputs.sha256 ) > /tmp/out/inputs-check.txt 2>&1 \
  || { cat /tmp/out/inputs-check.txt; echo "FAIL: a staged input does not match its pin"; exit 1; }
cat /tmp/out/inputs-check.txt

# Brunello's 1,000 non-targeting guides are genes controlguide1..controlguide1000
awk -F'\t' '$3 ~ /^controlguide/ {print $1}' $D/brunello.tsv > controls.txt
echo "control sgRNAs: $(wc -l < controls.txt)"
printf 'Samples\tbaseline\tdropout\nplasmid\t1\t0\nrepA\t1\t1\nrepB\t1\t1\n' > design.txt

CG=/sys/fs/cgroup
sampler() { : > /tmp/out/samples.tsv
  while : ; do
    printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$(awk '/^anon /{print $2; exit}' $CG/memory.stat 2>/dev/null || echo 0)" \
      "$(awk '/^usage_usec/{print $2; exit}' $CG/cpu.stat 2>/dev/null || echo 0)" \
      "$(df -k /tmp 2>/dev/null | awk 'NR==2{print $3}')" >> /tmp/out/samples.tsv
    sleep 5
  done; }
sampler & SPID=$!
step() { # step <name> <cmd...>: run, time, record exit
  local n=$1; shift; local t0; t0=$(date +%s)
  "$@" > /tmp/out/$n.stdout 2>&1; local rc=$?
  echo "$n	rc $rc	$(( $(date +%s) - t0 )) s" | tee -a /tmp/out/steps.tsv
  return $rc; }

: > /tmp/out/steps.tsv
T0=$(date +%s)
# Broad's P5 primers stagger the guide over nine 5' offsets, 22..30 here; AUTO keeps only the
# offsets that make up 75% of its first 100k reads. Both runs are counted, and the stagger run is
# the analysis.
STAGGER=22,23,24,25,26,27,28,29,30
FQ="$D/plasmid.fastq.gz $D/repA.fastq.gz $D/repB.fastq.gz"
step count mageck count -l $D/brunello.tsv -n stagger --sample-label plasmid,repA,repB --trim-5 $STAGGER --fastq $FQ; R1=$?
step rra mageck test -k stagger.count.txt -t repA,repB -c plasmid --normcounts-to-file -n rra; R2=$?
step mle mageck mle -k stagger.count.txt -d design.txt --threads "$NT" -n mle; R3=$?
T1=$(date +%s)
echo "tool_wall_s	$((T1-T0))" | tee -a /tmp/out/steps.tsv
# not part of the tool wall: MAGeCK's default AUTO offsets, a rerun for determinism, the
# non-targeting guides as RRA's null, replicate vs replicate, and the independent recount
step count_auto mageck count -l $D/brunello.tsv -n auto --sample-label plasmid,repA,repB --fastq $FQ; R4=$?
step rra_rep mageck test -k stagger.count.txt -t repA,repB -c plasmid -n rra_rep; R5=$?
step rra_ctrl mageck test -k stagger.count.txt -t repA,repB -c plasmid --control-sgrna controls.txt -n rra_ctrl; R6=$?
step rra_auto mageck test -k auto.count.txt -t repA,repB -c plasmid -n rra_auto; R7=$?
step null mageck test -k stagger.count.txt -t repB -c repA -n null; R8=$?
T2=$(date +%s)
python3 -u /tmp/recount.py $D/brunello.tsv stagger=stagger.log auto=auto.log -- \
  plasmid:$D/plasmid.fastq.gz repA:$D/repA.fastq.gz repB:$D/repB.fastq.gz > /tmp/out/recount.log 2>&1; R9=$?
echo "recount	rc $R9	$(( $(date +%s) - T2 )) s" | tee -a /tmp/out/steps.tsv
kill $SPID 2>/dev/null; wait $SPID 2>/dev/null
awk -F'\t' -v t0="$T0" -v t1="$T1" '$1<=t1 && $2>m{m=$2} $4>tk{tk=$4} $1>=t0 && !s{s=1; c0=$3} $1<=t1{c1=$3}
  END{printf "peak_anon_gib_tool\t%.2f\ntmpfs_peak_gib\t%.2f\navg_cores_tool\t%.2f\n", m/2^30, tk/2^20, (c1-c0)/((t1-t0)*1e6)}' \
  /tmp/out/samples.tsv | tee /tmp/out/footprint.txt
[ -r $CG/memory.peak ] && awk '{printf "cgroup_memory_peak_gib\t%.2f\n", $1/2^30}' $CG/memory.peak | tee -a /tmp/out/footprint.txt

for n in stagger auto; do cp $n.count.txt $n.countsummary.txt $n.log /tmp/out/ 2>/dev/null; done
for n in rra rra_rep rra_ctrl rra_auto null; do cp $n.gene_summary.txt $n.log /tmp/out/ 2>/dev/null; done
cp rra.sgrna_summary.txt rra.normalized.txt mle.gene_summary.txt mle.sgrna_summary.txt mle.log \
   controls.txt recount.*.stats recount.*.tsv /tmp/out/ 2>/dev/null
for r in $R1 $R2 $R3 $R4 $R5 $R6 $R7 $R8 $R9; do
  [ "$r" = 0 ] || { echo "FAIL: a step did not finish (see steps.tsv)"; tail -5 /tmp/out/*.stdout /tmp/out/recount.log; exit 1; }
done
cd /tmp/out
python3 -u /tmp/check.py > /tmp/out/check.txt 2>&1; RC=$?
cat /tmp/out/check.txt
# cross-chip fingerprints: whole files, and the deterministic MLE columns alone
sha256sum stagger.count.txt stagger.countsummary.txt auto.count.txt auto.countsummary.txt rra.gene_summary.txt \
  rra.sgrna_summary.txt rra.normalized.txt rra_ctrl.gene_summary.txt rra_auto.gene_summary.txt null.gene_summary.txt mle.sgrna_summary.txt mle.fixed.tsv mle.gene_summary.txt > /tmp/out/outputs.sha256
cat /tmp/out/outputs.sha256
[ "$RC" = 0 ] || { echo "FAIL: check.py"; exit 1; }
grep -q '^MAGECK REAL OK' /tmp/out/check.txt || { echo "FAIL: no completion sentinel"; exit 1; }
echo DONE
