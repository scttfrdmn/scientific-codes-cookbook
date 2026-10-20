#!/usr/bin/env bash
# measurements/mdtraj-real: MDTraj (and MDAnalysis on the same bytes) over the whole Folding@home
# FAST run of SARS-CoV-2 Mpro, then openmm-mdanalysis-real's DHFR DCD.
#   bash run.sh <worker processes> [first N files]
set +e
set -uo pipefail
NP=${1:?workers}
NF=${2:-}
# The env's root: /opt/conda in the aarchsci image, $CONDA_PREFIX in the x86 twin.
P="${CONDA_PREFIX:-/opt/conda}"
export PATH=$P/bin:$PATH TMPDIR=/tmp HOME=/tmp LC_ALL=C
# one thread per worker process: MDTraj's OpenMP kernels and numpy's BLAS must not start a thread per core each
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
W=/tmp/out
mkdir -p $W /tmp/jac
cd /tmp
{
  echo "uname_m	$(uname -m)"
  echo "nproc_all	$(nproc --all)"
  lscpu 2>/dev/null | grep -E '^(Model name|Thread\(s\) per core|Core\(s\) per socket|Socket\(s\)|CPU\(s\)|NUMA node\(s\)):' | sed 's/:  */	/'
  grep MemTotal /proc/meminfo
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
for m in ("mdtraj", "MDAnalysis", "numpy"):
    try:
        print("%s_version\t%s" % (m, str(getattr(__import__(m), "__version__", "unknown"))))
    except Exception as e:
        print("%s_version\tunimportable: %s" % (m, e))
PY
} > $W/host.txt
cat $W/host.txt

echo "== the staged inputs must match their pins =="
if [ -f /tmp/inputs.sha256 ]; then
  sha256sum -c /tmp/inputs.sha256 > $W/inputs-check.txt 2>&1 || { grep -v ': OK$' $W/inputs-check.txt | tail -5; echo "FAIL: a staged input does not match inputs.sha256" | tee $W/check.txt; exit 1; }
  echo "all $(wc -l < /tmp/inputs.sha256) staged inputs match their pins"
else
  # The sizing probe has no pins yet: it records them, on AWS, for inputs.sha256.
  sha256sum Amber20_Benchmark_Suite.tar.gz jacin/* fah/input_files/* fah/FAST_simulations/* > $W/inputs.sha256
  echo "probe: recorded $(wc -l < $W/inputs.sha256) sha256s"
fi
tar -xzf /tmp/Amber20_Benchmark_Suite.tar.gz -O PME/Topologies/JAC.prmtop > /tmp/jac/JAC.prmtop || { echo "FAIL: extract JAC.prmtop" | tee $W/check.txt; exit 1; }
cp /tmp/jacin/traj-JAC.dcd /tmp/jacin/constraints-JAC.npz /tmp/jac/

T0=$(date +%s)
python3 -u /tmp/real.py /tmp/fah $W "$NP" $NF 2>&1 | tee $W/real.log
rc=${PIPESTATUS[0]}
T1=$(date +%s)
echo "tool_wall_s	$((T1-T0))" >> $W/host.txt
cat /sys/fs/cgroup/memory.peak 2>/dev/null | awk '{printf "cgroup_memory_peak_gib\t%.2f\n", $1/1073741824}' >> $W/host.txt
df -h /tmp /var/tmp | sed 's/^/df	/' >> $W/host.txt
[ "$rc" = 0 ] || { echo "FAIL: real.py exited $rc" | tee -a $W/check.txt; cat $W/host.txt; exit 1; }
python3 -u /tmp/check.py $W "$NF" 2>&1 | tee $W/check.txt
rc=${PIPESTATUS[0]}
cat $W/host.txt
exit $rc
