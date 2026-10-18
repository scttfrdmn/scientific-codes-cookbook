#!/usr/bin/env bash
# measurements/lammps-real: LAMMPS' own scaled rhodopsin benchmark (bench/in.rhodo.scaled at
# patch_22Jul2025) at the size upstream assigns to 64 processors -- 32,000 atoms per rank,
# replicated 4x4x4 to 2,048,000 atoms -- run 1000 steps, plus three single-evaluation (run 0)
# decks that check it against upstream's committed reference log and against extensivity.
#   run.sh <MPI ranks> <x> <y> <z> <steps>
set +e
set -uo pipefail
NP=${1:?ranks}; X=${2:?x}; Y=${3:?y}; Z=${4:?z}; STEPS=${5:?steps}
export PATH=/opt/conda/bin:$PATH HOME=/tmp TMPDIR=/tmp OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
# Docker gives a container a 64 MB /dev/shm, and Open MPI's shared-memory transport puts its
# per-rank segments there: at 64 ranks they no longer fit and the first collective dies with
# SIGBUS (exit 135). Back them with host /tmp (tmpfs, half the RAM) instead.
export OMPI_MCA_btl_sm_backing_directory=/tmp
MPI="mpiexec -n $NP --allow-run-as-root --oversubscribe"
cd /tmp
mkdir -p /tmp/out

{
  echo "uname_m	$(uname -m)"
  echo "threads_per_core_sysfs	$(awk -F'[,-]' '{print NF}' /sys/devices/system/cpu/cpu0/topology/thread_siblings_list 2>/dev/null)"
  echo "cpu_model	$(grep -m1 -E '^(model name|CPU part)' /proc/cpuinfo | cut -d: -f2)"
  lscpu 2>/dev/null | grep -E '^(Model name|Thread\(s\) per core|Core\(s\) per socket|Socket\(s\)|NUMA node\(s\)|CPU\(s\)):' | sed 's/:  */	/'
  python3 -u - <<'PY' 2>&1
import ctypes, glob, os, sys
# Describes the run only; nothing here may fail it.
try:
    pre = sys.prefix
    for lib in sorted(glob.glob(os.path.join(pre, "lib", "libopenblas*.so*"))):
        try:
            f = ctypes.CDLL(lib).openblas_get_corename
            f.restype = ctypes.c_char_p
            print("openblas_corename\t%s (%s)" % (f().decode(), os.path.basename(lib)))
            break
        except Exception as e:
            print("openblas_corename\tunreadable: %s" % e)
    else:
        print("openblas_corename\tnone (no libopenblas in %s/lib)" % pre)
    print("blas_libs\t%s" % " ".join(sorted(os.path.basename(p) for p in glob.glob(os.path.join(pre, "lib", "lib*blas*.so")))))
except Exception as e:
    print("openblas_corename\terror: %s" % e)
PY
  echo "lmp_version	$(lmp_mpi -h 2>/dev/null | grep -m1 -E '^Large-scale|LAMMPS' | tr -s ' ')"
  echo "fft	$(lmp_mpi -h 2>/dev/null | grep -i -A3 'FFT information' | tr -s ' ' | tr '\n' ' ')"
  echo "dev_shm	$(df -m /dev/shm 2>/dev/null | awk 'NR==2{print $2" MB"}')"
} > /tmp/out/host.txt
cat /tmp/out/host.txt

echo "== staged inputs must match their pins =="
sha256sum -c /tmp/inputs.sha256 || { echo "FAIL: a staged input does not match its pin"; exit 1; }

# The production deck is upstream's in.rhodo.scaled with one change: run 100 -> run $STEPS.
sed "s/^run             100\$/run             $STEPS/" in.rhodo.scaled > in.real
NCH=$(awk 'NR==FNR{a[FNR]=$0; next} a[FNR]!=$0{n++} END{print n+0}' in.rhodo.scaled in.real)
echo "deck lines changed from upstream: $NCH (want 1)"
[ "$NCH" = 1 ] || { echo "FAIL: the deck edit did not apply exactly once"; exit 1; }

# Single-evaluation decks: upstream's physics, run 0, every energy term at full precision.
# $1 = replicate factors, $2 = optional kspace_modify line.
mkdeck() {
  cat <<DECK
units           real
neigh_modify    delay 5 every 1
atom_style      full
atom_modify     map hash
bond_style      harmonic
angle_style     charmm
dihedral_style  charmm
improper_style  harmonic
pair_style      lj/charmm/coul/long 8.0 10.0
pair_modify     mix arithmetic
kspace_style    pppm 1e-4
$2
read_data       data.rhodo
replicate       $1
fix             1 all shake 0.0001 5 0 m 1.0 a 232
fix             2 all npt temp 300.0 300.0 100.0 z 0.0 0.0 1000.0 mtk no pchain 0 tchain 1
special_bonds   charmm
thermo_style    custom step ke pe ebond eangle edihed eimp evdwl ecoul elong press vol
thermo_modify   format float %.17g
timestep        2.0
run             0
DECK
}

echo "== run 0: the 32,000-atom system, PPPM chosen automatically (upstream's reference) =="
mkdeck "1 1 1" "" > in.base-auto
$MPI lmp_mpi -in in.base-auto -log /tmp/out/base-auto.log > /tmp/out/base-auto.out 2>&1
echo "base_auto_exit	$?"
GEW=$(awk '/G vector/{print $NF; exit}' /tmp/out/base-auto.log)
read -r MX MY MZ <<< "$(awk '/^  grid = /{print $3, $4, $5; exit}' /tmp/out/base-auto.log)"
echo "auto PPPM: g_ewald $GEW, grid $MX $MY $MZ"
[ -n "$GEW" ] && [ -n "$MZ" ] || echo "WARN: no PPPM parameters from the base run; the fixed-mesh decks will fail and be reported"

echo "== run 0: base and replicated system with the SAME g_ewald and a mesh scaled by the replication =="
mkdeck "1 1 1" "kspace_modify   gewald $GEW mesh $MX $MY $MZ" > in.base-fixed
mkdeck "$X $Y $Z" "kspace_modify   gewald $GEW mesh $((MX*X)) $((MY*Y)) $((MZ*Z))" > in.rep-fixed
$MPI lmp_mpi -in in.base-fixed -log /tmp/out/base-fixed.log > /tmp/out/base-fixed.out 2>&1
echo "base_fixed_exit	$?"
$MPI lmp_mpi -in in.rep-fixed -log /tmp/out/rep-fixed.log > /tmp/out/rep-fixed.out 2>&1
echo "rep_fixed_exit	$?"

CG=/sys/fs/cgroup
( : > /tmp/out/samples.tsv
  while : ; do
    printf '%s\t%s\t%s\n' "$(date +%s)" "$(cat $CG/memory.current 2>/dev/null || echo 0)" \
      "$(awk '/^usage_usec/{print $2; exit}' $CG/cpu.stat 2>/dev/null || echo 0)" >> /tmp/out/samples.tsv
    sleep 5
  done ) &
SPID=$!

echo "== production: $((32000*X*Y*Z)) atoms, $STEPS steps, $NP ranks =="
T0=$(date +%s)
$MPI lmp_mpi -var x "$X" -var y "$Y" -var z "$Z" -in in.real -log /tmp/out/lmp-real.log > /tmp/out/lmp-real.out 2>&1
RC=$?
T1=$(date +%s)
kill $SPID 2>/dev/null; wait $SPID 2>/dev/null
echo "production_exit	$RC"
{
  echo "production_wall_s	$((T1-T0))"
  awk -F'\t' -v wall="$((T1-T0))" 'NR==1{cu0=$3} {if($2>mc)mc=$2; cu=$3}
    END{printf "peak_mem_gib\t%.2f\n", mc/2^30; if(wall>0) printf "avg_cores\t%.2f\n", (cu-cu0)/(wall*1e6)}' /tmp/out/samples.tsv
  [ -r $CG/memory.peak ] && awk '{printf "cgroup_peak_gib\t%.2f\n", $1/2^30}' $CG/memory.peak
} | tee /tmp/out/footprint.txt

python3 -u /tmp/check.py "$NP" "$X" "$Y" "$Z" "$STEPS" 2>&1 | tee /tmp/out/check.txt
CHK=${PIPESTATUS[0]}
tar -cf /tmp/lammps-real-out.tar -C /tmp out in.real in.base-auto in.base-fixed in.rep-fixed
[ "$RC" = 0 ] && [ "$CHK" = 0 ] && echo "ALL CHECKS PASSED" || { echo "FAIL: production rc=$RC check rc=$CHK"; exit 1; }
