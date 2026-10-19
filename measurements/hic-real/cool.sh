#!/usr/bin/env bash
# measurements/hic-real task 2 (cooler image): load task 1's combined GM12878 contact list into a
# .cool at 4DN's base resolution, zoomify it to every resolution 4DN publishes, and require the
# result to BE 4DN's published 4DNFIFBPTHCU.mcool -- bins and every pixel count, at every
# resolution -- with no contact created or lost on the way. Then balance three resolutions with
# 4DN's own recorded ICE parameters and compare the weights.
#   cool.sh <threads>
set +e
set -uo pipefail
T=${1:?threads}
export PATH=/usr/local/bin:/opt/conda/bin:$PATH HOME=/tmp TMPDIR=/var/tmp/hic
O=/tmp/out; W=/var/tmp/hic
mkdir -p $O $W
cd /tmp
fail() { echo "FAIL: $*" | tee -a $O/check.txt; exit 1; }
rec() { printf '%s\t%s\n' "$1" "$2" | tee -a $O/check.txt; }
M=4DNFIFBPTHCU.mcool; CS=4DNFI823LSII.chrom.sizes

{
  echo "uname_m	$(uname -m)"
  echo "nproc	$(grep -c ^processor /proc/cpuinfo)"
  echo "threads_per_core	$(awk -F'[,-]' '{print NF}' /sys/devices/system/cpu/cpu0/topology/thread_siblings_list 2>/dev/null)"
  grep -m1 -E '^model name' /proc/cpuinfo | sed 's/^model name[^:]*: */cpu_model	/'
  grep -m1 -E '^CPU part' /proc/cpuinfo | sed 's/^CPU part[^:]*: */cpu_part	/'
  echo "mem_total_kb	$(awk '/MemTotal/{print $2}' /proc/meminfo)"
  echo "cooler	$(cooler --version 2>&1 | head -n1)"
  echo "python_elf_machine	$(od -An -tu2 -j18 -N2 "$(readlink -f "$(command -v python3)")" | tr -d ' ')"
  python3 -I -c '
import ctypes, numpy
libs = sorted({l.split()[-1] for l in open("/proc/self/maps") if "blas" in l.lower() and l.split()[-1].startswith("/")})
for p in libs:
    try:
        f = ctypes.CDLL(p).openblas_get_corename
        f.restype = ctypes.c_char_p
        print("openblas_corename\t%s (%s)" % (f().decode(), p))
    except Exception as e:
        print("blas_lib\t%s (%s)" % (p, type(e).__name__))
print("numpy\t%s" % str(getattr(numpy, "__version__", "unknown")))'
} > $O/host.txt 2>&1
cat $O/host.txt

echo "== staged bytes must match the sha256 pins and 4DN's published md5 =="
pins() {   # pins <file>...: 4DN's md5 must match; our sha256 must match where inputs.sha256 has one
  for f in "$@"; do
    m=$(awk -v f="$f" '$2==f{print $1}' inputs.md5); s=$(awk -v f="$f" '$2==f{print $1}' inputs.sha256)
    [ -n "$m" ] || fail "$f has no published md5 in inputs.md5"
    gm=$(md5sum "$f" | cut -d' ' -f1); gs=$(sha256sum "$f" | cut -d' ' -f1)
    echo "$gs  $f" >> $O/inputs-observed.sha256
    [ "$gm" = "$m" ] || fail "$f md5 $gm, 4DN publishes $m"
    [ -z "$s" ] || [ "$gs" = "$s" ] || fail "$f sha256 $gs, pinned $s"
    echo "ok  $f  md5 $gm  sha256 $gs${s:+ (pinned)}" | tee -a $O/pins-check.txt
  done
}
pins $M $CS
# The contact count this task must conserve was measured by task 1, not written here.
NC=$(awk -F'\t' '$1=="combined_ours_records"{print $2}' pairs-check.txt)
grep -q '^PAIRS OK' pairs-check.txt || fail "task 1 did not pass"
rec contacts_from_task1 "$NC"
COLS=$(bgzip -dc combined.pairs.gz 2>/dev/null | head -c 1000000 | grep -m1 '^#columns:')
rec pairs_columns "$COLS"
case "$COLS" in "#columns: readID chrom1 pos1 chrom2 pos2 "*) ;; *) fail "unexpected column order";; esac

cat > $W/tm.py <<'PY'
import resource, subprocess, sys, time
name, cmd = sys.argv[1], sys.argv[2]
t0 = time.time()
rc = subprocess.call(["bash", "-c", "set -o pipefail\n" + cmd])
w = time.time() - t0
ru = resource.getrusage(resource.RUSAGE_CHILDREN)
with open("/tmp/out/timing.tsv", "a") as fh:
    fh.write("%s\t%.1f\t%d\t%d\t%.1f\n" % (name, w, rc, ru.ru_maxrss, ru.ru_utime + ru.ru_stime))
sys.exit(rc)
PY
printf 'step\twall_s\trc\tmaxrss_kb\tcpu_s\n' > $O/timing.tsv
tm() { python3 -u $W/tm.py "$@"; }

RES=$(python3 -I -c '
import h5py, sys
f = h5py.File(sys.argv[1], "r")
print(",".join(sorted(f["resolutions"].keys(), key=int)))' $M) || fail "cannot read the published mcool"
BASE=${RES%%,*}
rec published_resolutions "$RES"

echo "== cload at 4DN's base resolution, then zoomify to every published resolution =="
tm cload "cooler cload pairs -c1 2 -p1 3 -c2 4 -p2 5 $CS:$BASE combined.pairs.gz $W/base.cool" \
  > $O/cload.log 2>&1 || { tail -20 $O/cload.log; fail "cooler cload"; }
tm zoomify "cooler zoomify -p $T -r $RES -o $O/ours.mcool $W/base.cool" > $O/zoomify.log 2>&1 \
  || { tail -20 $O/zoomify.log; fail "cooler zoomify"; }

echo "== the identities, against 4DN's published matrix =="
python3 -I -u /tmp/ident.py $M $O/ours.mcool $NC $T 2>&1 | tee $O/identities.log
[ "${PIPESTATUS[0]}" = 0 ] || fail "an identity failed (identities.log)"
cat $O/timing.tsv
for k in /sys/fs/cgroup/memory.peak /sys/fs/cgroup/memory/memory.max_usage_in_bytes; do
  [ -r $k ] && rec cgroup_memory_peak_bytes "$(cat $k)" && break
done
echo "COOL OK" | tee -a $O/check.txt
