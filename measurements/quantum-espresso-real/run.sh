#!/usr/bin/env bash
# measurements/quantum-espresso-real: the AUSURF112 benchmark (112 Au atoms, 800 bands, US PBE)
# run to a converged SCF with forces, twice on the same box under two MPI decompositions; then
# check.py asserts the identities.
#   run.sh <MPI ranks> <extra pw.x flags for the second decomposition, e.g. "-nk 2">
set +e
set -uo pipefail
NP=${1:?ranks}
DECOMP_B=${2:?second decomposition}
# arm64: the aarch.science image's env; x86: the twin xenv-install.sh put at /var/tmp/env.
[ -x /var/tmp/env/bin/pw.x ] || export PATH=/opt/conda/bin:$PATH
export HOME=/tmp TMPDIR=/tmp OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
export OMPI_MCA_btl_sm_backing_directory=/tmp OMPI_MCA_rmaps_base_oversubscribe=yes
# --use-hwthread-cpus: Intel's 16 vCPU are 8 cores x 2 threads, and Open MPI counts cores, so without
# it 16 ranks would be "oversubscribed" there and Open MPI would yield-when-idle on Intel only.
MPI="mpirun --allow-run-as-root --use-hwthread-cpus -n $NP"
O=/tmp/out
W=/var/tmp/qe            # root disk, not the half-RAM /tmp tmpfs
mkdir -p $O $W
cd /tmp

{
  echo "uname_m	$(uname -m)"
  echo "pw_x	$(command -v pw.x)"
  lscpu 2>/dev/null | grep -E '^(Model name|Thread\(s\) per core|Core\(s\) per socket|Socket\(s\)|CPU\(s\)|NUMA node\(s\)):' | sed 's/:  */	/'
  python3 -u - <<'PY' 2>&1
import ctypes, glob, os, sys
# Describes the run only; nothing here may fail it.
try:
    libs = sorted(glob.glob(os.path.join(sys.prefix, "lib", "libopenblas*.so*")))
    for lib in libs:
        try:
            f = ctypes.CDLL(lib).openblas_get_corename
            f.restype = ctypes.c_char_p
            print("openblas_corename\t%s (%s)" % (f().decode(), os.path.basename(lib)))
            break
        except Exception as e:
            print("openblas_corename\tunreadable: %s" % e)
    else:
        print("openblas_corename\tnone in %s/lib" % sys.prefix)
except Exception as e:
    print("openblas_corename\terror: %s" % e)
PY
  for l in libblas.so.3 liblapack.so.3 libscalapack.so libfftw3.so.3; do
    p=$(ls -l "$(dirname "$(command -v pw.x)")/../lib/$l" 2>/dev/null | awk '{print $NF}')
    echo "lib_$l	${p:-absent}"
  done
} > $O/host.txt
cat $O/host.txt

echo "== staged inputs must match their pins =="
sha256sum -c /tmp/inputs.sha256 || { echo "FAIL: a staged input does not match its pin"; exit 1; }

# The benchmark stops after two SCF iterations (it times them); a researcher's run converges. Two
# edits, nothing else: run to convergence, and converge tightly enough to compare forces.
for D in A B; do
  mkdir -p $W/$D
  cp /tmp/Au.pbe-nd-van.UPF $W/$D/
  sed -e 's/electron_maxstep = 2$/electron_maxstep = 100/' \
      -e 's/mixing_beta = 0.7$/mixing_beta = 0.7\n    conv_thr = 1.d-8/' /tmp/ausurf.in > $W/$D/ausurf.in
done
diff /tmp/ausurf.in $W/A/ausurf.in > $O/input-edits.diff
cat $O/input-edits.diff
grep -q 'electron_maxstep = 100' $W/A/ausurf.in && grep -q 'conv_thr = 1.d-8' $W/A/ausurf.in \
  || { echo "FAIL: the convergence edits did not apply"; exit 1; }

# Peak RSS of the largest rank, from the kernel's own accounting of reaped children.
run_pw() {  # <label> <dir> <extra flags>
  ( cd "$2" && python3 -u - "$1" "$MPI pw.x $3 -in ausurf.in" <<'PY'
import resource, subprocess, sys, time
label, cmd = sys.argv[1], sys.argv[2]
t0 = time.time()
with open("pw.out", "w") as fh:
    rc = subprocess.call(cmd.split(), stdout=fh, stderr=subprocess.STDOUT)
rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
print("%s\trc=%d\twall_s=%.1f\tmax_rank_rss_gib=%.2f\tcmd=%s" % (label, rc, time.time() - t0, rss / 2**20, cmd))
sys.exit(rc)
PY
  )
}

RC=0
T0=$(date +%s)
echo "== A: $NP ranks, pw.x defaults =="
run_pw A $W/A "" | tee -a $O/runs.txt
[ "${PIPESTATUS[0]}" = 0 ] || RC=1
cp $W/A/pw.out $O/pw-A.out
grep -E 'Number of MPI|K-points division|R & G space|subspace diag|total energy  |^!|convergence has|WALL' $W/A/pw.out | tail -12
T1=$(date +%s)
echo "== B: $NP ranks, $DECOMP_B =="
run_pw B $W/B "$DECOMP_B" | tee -a $O/runs.txt
[ "${PIPESTATUS[0]}" = 0 ] || RC=1
cp $W/B/pw.out $O/pw-B.out
grep -E 'Number of MPI|K-points division|R & G space|subspace diag|^!|convergence has|WALL' $W/B/pw.out | tail -8
T2=$(date +%s)

echo "== identities =="
python3 -u /tmp/check.py $O "$NP" "$DECOMP_B" 2>&1 | tee $O/check.txt
CHK=${PIPESTATUS[0]}
{
  echo "A_wall_s	$((T1-T0))"
  echo "B_wall_s	$((T2-T1))"
  echo "tool_wall_s	$((T2-T0))"
  CG=/sys/fs/cgroup
  [ -r $CG/memory.peak ] && awk '{printf "cgroup_peak_gib\t%.2f\n", $1/2^30}' $CG/memory.peak
  [ -r $CG/cpu.stat ] && awk -v w=$((T2-T0)) '/^usage_usec/{printf "avg_cores_whole_task\t%.2f\n", $2/(w*1e6)}' $CG/cpu.stat
  df -h /var/tmp /tmp 2>/dev/null | tail -2
} | tee $O/footprint.txt
tar -cf /tmp/quantum-espresso-real-out.tar -C /tmp out
[ "$RC" = 0 ] && [ "$CHK" = 0 ] && echo "ALL CHECKS PASSED" || { echo "FAIL: pw rc=$RC check rc=$CHK"; exit 1; }
