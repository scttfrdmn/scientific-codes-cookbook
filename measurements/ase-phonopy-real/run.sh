#!/usr/bin/env bash
# measurements/ase-phonopy-real: the phonopy workflow on bulk Si with real DFT forces.
# GPAW computes PBE forces on phonopy's own committed displaced supercells (Si-QE: 64 atoms,
# Si: 16 atoms, v4.4.0 examples); phonopy turns those and the committed QE and VASP forces into
# force constants, dispersion, DOS and thermal properties; analyse.py asserts the identities.
#   run.sh <MPI ranks>
set +e
set -uo pipefail
NP=${1:?ranks}
export PATH=/opt/conda/bin:$PATH HOME=/tmp TMPDIR=/tmp OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
export OMPI_MCA_btl_sm_backing_directory=/tmp
MPI="mpiexec -n $NP --allow-run-as-root --oversubscribe"
W=/tmp/out
mkdir -p $W
cd /tmp

{
  echo "uname_m	$(uname -m)"
  echo "cpu_model	$(grep -m1 -E '^(model name|CPU part)' /proc/cpuinfo | cut -d: -f2)"
  lscpu 2>/dev/null | grep -E '^(Model name|Thread\(s\) per core|Core\(s\) per socket|Socket\(s\)|CPU\(s\)):' | sed 's/:  */	/'
  python3 -u - <<'PY' 2>&1
import ctypes, glob, os, sys
# Describes the run only; nothing here may fail it.
try:
    for lib in sorted(glob.glob(os.path.join(sys.prefix, "lib", "libopenblas*.so*"))):
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
for m in ("gpaw", "phonopy", "ase", "spglib", "phonors", "numpy"):
    try:
        print("%s_version\t%s" % (m, str(getattr(__import__(m), "__version__", "unknown"))))
    except Exception as e:
        print("%s_version\tunimportable: %s" % (m, e))
PY
} > $W/host.txt
cat $W/host.txt

echo "== staged inputs must match their pins =="
sha256sum -c /tmp/inputs.sha256 || { echo "FAIL: a staged input does not match its pin"; exit 1; }
for f in Si-QE_phonopy_disp.yaml Si-QE_supercell-001.out Si_phonopy_disp.yaml Si_vasprun.xml; do cp /tmp/$f $W/; done

RC=0
T0=$(date +%s)
for CASE in vasp16 qe64; do
  echo "== GPAW forces: $CASE, $NP ranks =="
  S=$(date +%s)
  $MPI python3 -u /tmp/forces.py $CASE $W > $W/forces-$CASE.out 2>&1
  R=$?
  echo "gpaw_${CASE}_exit	$R	$(( $(date +%s) - S )) s"
  tail -3 $W/forces-$CASE.out
  [ "$R" = 0 ] || RC=1
done
T1=$(date +%s)
echo "== phonopy: force constants, dispersion, DOS, thermal properties for four force sets =="
python3 -u -W ignore::DeprecationWarning /tmp/analyse.py $W "$NP" 2>&1 | tee $W/check.txt
CHK=${PIPESTATUS[0]}
T2=$(date +%s)
{
  echo "gpaw_wall_s	$((T1-T0))"
  echo "phonopy_wall_s	$((T2-T1))"
  echo "tool_wall_s	$((T2-T0))"
  CG=/sys/fs/cgroup
  [ -r $CG/memory.peak ] && awk '{printf "cgroup_peak_gib\t%.2f\n", $1/2^30}' $CG/memory.peak
  [ -r $CG/cpu.stat ] && awk -v w=$((T2-T0)) '/^usage_usec/{printf "avg_cores_whole_task\t%.2f\n", $2/(w*1e6)}' $CG/cpu.stat
} | tee $W/footprint.txt
tar -cf /tmp/ase-phonopy-real-out.tar -C /tmp out
[ "$RC" = 0 ] && [ "$CHK" = 0 ] && echo "ALL CHECKS PASSED" || { echo "FAIL: gpaw rc=$RC check rc=$CHK"; exit 1; }
