#!/usr/bin/env bash
# measurements/warpx-real: WarpX's own Turner et al. (2013) capacitive-discharge benchmark, case 1
# (He, 450 V at 13.56 MHz, 128 cells, 512 particles/cell, 1280 RF periods = 512,000 steps), run
# through upstream's PICMI deck unmodified and checked by upstream's analysis script against the
# published ion-density profile.
#   run.sh <rf periods> <threads,threads,...> [repeat-threads] [extra WarpX parameter]
# 1280 periods is the benchmark. Anything else is a sizing probe: the deck's total_time is edited
# (and the edit counted), and the Turner comparison is reported, not asserted.
set +e
set -uo pipefail
CYC=${1:?rf periods}; THREADS=${2:?threads}; REPEAT=${3:-}; EXTRA=${4:-}
[ -d /var/tmp/env/bin ] || export PATH=/opt/conda/bin:$PATH
export HOME=/tmp TMPDIR=/tmp OPENBLAS_NUM_THREADS=1
OUT=/tmp/out; W=/tmp/wx
mkdir -p "$OUT" "$W/warpx-data/MCC_cross_sections/He"
cd /tmp

{
  echo "uname_m	$(uname -m)"
  echo "cpu_model	$(grep -m1 -E '^(model name|CPU part)' /proc/cpuinfo | cut -d: -f2)"
  lscpu 2>/dev/null | grep -E '^(Model name|Thread\(s\) per core|Core\(s\) per socket|Socket\(s\)|CPU\(s\)):' | sed 's/:  */	/'
  echo "python	$(command -v python3)"
  python3 -u - <<'PY' 2>&1
# Describes the run only; nothing here may fail it.
import ctypes, glob, os, sys
def v(m):
    try:
        mod = __import__(m)
        return str(getattr(mod, "__version__", "unknown"))
    except Exception as e:
        return "import failed: %s" % type(e).__name__
for m in ("pywarpx", "numpy", "scipy", "matplotlib", "yt"):
    print("%s\t%s" % (m, v(m)))
try:
    import pywarpx, os.path as p
    print("pywarpx_libs\t%s" % " ".join(sorted(os.path.basename(x) for x in glob.glob(os.path.join(sys.prefix, "lib", "libwarpx*")))))
except Exception as e:
    print("pywarpx_libs\terror: %s" % e)
for lib in sorted(glob.glob(os.path.join(sys.prefix, "lib", "libopenblas*.so*"))):
    try:
        f = ctypes.CDLL(lib).openblas_get_corename
        f.restype = ctypes.c_char_p
        print("openblas_corename\t%s" % f().decode())
    except Exception as e:
        print("openblas_corename\tunreadable: %s" % e)
    break
else:
    print("openblas_corename\tnone in %s/lib" % sys.prefix)
PY
} > "$OUT/host.txt"
cat "$OUT/host.txt"

echo "== staged inputs must match their pins =="
sha256sum -c /tmp/inputs.sha256 || { echo "FAIL: a staged input does not match its pin"; exit 1; }
for f in electron_scattering excitation_1 excitation_2 ionization ion_scattering ion_back_scatter; do
  cp "/tmp/$f.dat" "$W/warpx-data/MCC_cross_sections/He/"
done

# The deck reads its cross sections from ../../../../warpx-data, i.e. as if run from inside a
# checkout of WarpX's Examples tree; each run gets a directory at that depth instead of an edit.
cp /tmp/inputs_base_1d_picmi.py "$W/deck.py"
if [ "$CYC" != 1280 ]; then
  sed -i "s/total_time = np.array(\[1280, /total_time = np.array([$CYC, /" "$W/deck.py"
fi
NCH=$(python3 -c 'import sys; a=open(sys.argv[1]).read().splitlines(); b=open(sys.argv[2]).read().splitlines(); print(sum(x!=y for x,y in zip(a,b))+abs(len(a)-len(b)))' /tmp/inputs_base_1d_picmi.py "$W/deck.py")
echo "extra_parameters	${EXTRA:-none}" | tee -a "$OUT/host.txt"
echo "deck_lines_changed	$NCH (rf periods $CYC)" | tee -a "$OUT/host.txt"
if [ "$CYC" = 1280 ] && [ "$NCH" != 0 ]; then echo "FAIL: the benchmark deck is not upstream's"; exit 1; fi
if [ "$CYC" != 1280 ] && [ "$NCH" = 0 ]; then echo "FAIL: the probe edit did not apply"; exit 1; fi

RUNS=""
for t in ${THREADS//,/ } ${REPEAT:+r$REPEAT}; do RUNS="$RUNS $t"; done
RC=0
for r in $RUNS; do
  t=${r#r}
  d="$W/t$r/a/b/c"; mkdir -p "$d"
  echo "== WarpX, $t OpenMP thread(s) ($r) =="
  ( cd "$d" && OMP_NUM_THREADS=$t python3 -u /tmp/check.py run "$OUT/run-t$r.tsv" \
        python3 -u "$W/deck.py" -n 1 $EXTRA ) > "$OUT/warpx-t$r.log" 2>&1
  rc=$?
  tail -5 "$OUT/warpx-t$r.log"
  cp "$d/ion_density_case_1.npy" "$OUT/ion_density_t$r.npy" 2>/dev/null
  ls "$d/diags" > "$OUT/diags-t$r.txt" 2>/dev/null
  [ "$rc" = 0 ] || { echo "FAIL: WarpX at $t threads exited $rc"; RC=1; continue; }
  if [ "$CYC" = 1280 ]; then
    # Upstream's own analysis, unmodified, in the run directory: it asserts the RMS error < 0.065.
    ( cd "$d" && MPLBACKEND=Agg python3 -u /tmp/analysis_1d.py ) > "$OUT/analysis-t$r.log" 2>&1
    echo "upstream_analysis_rc	$?" >> "$OUT/run-t$r.tsv"
    tail -3 "$OUT/analysis-t$r.log"
    cp "$d/ion_density_case_1.png" "$OUT/ion_density_t$r.png" 2>/dev/null
  fi
done

python3 -u /tmp/check.py check "$OUT" "$CYC" $RUNS 2>&1 | tee "$OUT/check.txt"
[ "${PIPESTATUS[0]}" = 0 ] || RC=1
tar -C /tmp -cf /tmp/warpx-real-out.tar out
test "$RC" = 0 || { echo "FAIL"; exit 1; }
echo DONE
