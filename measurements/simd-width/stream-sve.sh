set -euo pipefail
export PATH=/opt/conda/bin:$PATH
export TMPDIR=/tmp HOME=/tmp
cd /tmp
LBL="${LBL:-unknown}"
N=$(nproc)
printf '%s  stream.c\n' a52bae5e175bea3f7832112af9c085adab47117f7d2ce219165379849231692b > s.sha
sha256sum -c s.sha

CC=""
for c in aarch64-conda-linux-gnu-gcc gcc cc; do command -v $c >/dev/null 2>&1 && { CC=$c; break; }; done
test -n "$CC" || { echo "no C compiler in this image"; exit 1; }

FEAT=$(awk -F': *' '/^Features/{print $2; exit}' /proc/cpuinfo)
has(){ case " $FEAT " in *" $1 "*) return 0;; *) return 1;; esac; }
SVEBITS=$([ -r /proc/sys/abi/sve_default_vector_length ] && echo $(( $(cat /proc/sys/abi/sve_default_vector_length) * 8 )) || echo 0)

# the generation-appropriate "best" target, per AWS's own guidance
if has sve; then TVB="-mcpu=neoverse-512tvb"; else TVB="-mcpu=neoverse-n1"; fi

{
printf 'label\t%s\n' "$LBL"
printf 'compiler\t%s (%s)\n' "$CC" "$($CC -dumpversion 2>/dev/null)"
printf 'nproc\t%s\n' "$N"
printf 'cpu_part\t%s\n' "$(awk -F': *' '/CPU part/{print $2; exit}' /proc/cpuinfo)"
printf 'sve_bits\t%s\n' "$SVEBITS"
printf 'mem_total_gib\t%s\n' "$(awk '/MemTotal/{printf "%.0f", $2/1048576}' /proc/meminfo)"
printf 'llc\t%s\n' "$(lscpu 2>/dev/null | awk -F': *' '/L3 cache/{print $2; exit}')"
} | tee "sv-$LBL.txt"

# ---------- 1. STREAM: memory bandwidth vs thread count (arrays >> LLC) ----------
echo "== STREAM (official source, array 320 MB each) =="
$CC -O3 -fopenmp -DSTREAM_ARRAY_SIZE=40000000 -DNTIMES=20 $TVB stream.c -o stream -lm 2> cc1.log \
  || { echo "compile failed with $TVB, retrying baseline"; $CC -O3 -fopenmp -DSTREAM_ARRAY_SIZE=40000000 -DNTIMES=20 stream.c -o stream -lm; }
SWEEP="1 2 4 8 16"; [ "$N" -ge 32 ] && SWEEP="$SWEEP 32"; [ "$N" -ge 64 ] && SWEEP="$SWEEP 64"
for T in $SWEEP; do
  [ "$T" -gt "$N" ] && continue
  BW=$(OMP_NUM_THREADS=$T OMP_PROC_BIND=close OMP_PLACES=cores ./stream 2>/dev/null | awk '/^Triad:/{print $2}')
  printf 'stream_triad_t%s_MBs\t%s\n' "$T" "$BW" | tee -a "sv-$LBL.txt"
done
BWMAX=$(awk -F'\t' '/^stream_triad_/{gsub(/[^0-9.]/,"",$2); if($2+0>m) m=$2+0} END{printf "%.0f", m}' "sv-$LBL.txt")
printf 'stream_peak_MBs\t%s\nstream_peak_per_core_MBs\t%s\n' "$BWMAX" \
  "$(awk -v b="$BWMAX" -v n="$N" 'BEGIN{printf "%.0f", b/n}')" | tee -a "sv-$LBL.txt"

# ---------- 2. compute-bound FP kernel, built three ways ----------
cat > kern.c <<'EOF'
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#define N 4096            /* 32 KB per array: L1/L2 resident, so this is COMPUTE bound */
#define REP 200000
static double a[N], b[N];
int main(void){
  for (long i=0;i<N;i++){ a[i]=1.0+i*1e-6; b[i]=2.0-i*1e-6; }
  double s=0; struct timespec t0,t1;
  clock_gettime(CLOCK_MONOTONIC,&t0);
  for (long r=0;r<REP;r++){ double p=0; for (long i=0;i<N;i++) p += a[i]*b[i]; s+=p; }
  clock_gettime(CLOCK_MONOTONIC,&t1);
  double dt=(t1.tv_sec-t0.tv_sec)+(t1.tv_nsec-t0.tv_nsec)/1e9;
  printf("%.2f %.6e\n", (2.0*N*REP)/dt/1e9, s);   /* GFLOP/s, and the sum so it cannot be optimised away */
  return 0;
}
EOF
echo "== compute kernel: does enabling SVE change anything? =="
build_run(){ # name flags
  if $CC -O3 -ffast-math $2 kern.c -o "k_$1" 2>> cc2.log; then
    R=$(./"k_$1" 2>/dev/null | awk '{print $1}')
    [ -n "$R" ] && printf 'kernel_%s_GFLOPs\t%s\n' "$1" "$R" || printf 'kernel_%s_GFLOPs\tSIGILL/failed-to-run\n' "$1"
  else
    printf 'kernel_%s_GFLOPs\tdid-not-compile\n' "$1"
  fi
}
{
build_run neon_armv8a  "-march=armv8-a"
build_run aws_tvb      "$TVB"
if has sve;  then build_run sve  "-march=armv8.2-a+sve";  else printf 'kernel_sve_GFLOPs\tno-sve-on-this-chip\n'; fi
if has sve2; then build_run sve2 "-march=armv9-a+sve2";   else printf 'kernel_sve2_GFLOPs\tno-sve2-on-this-chip\n'; fi
} | tee -a "sv-$LBL.txt"

# did the compiler actually emit SVE instructions where asked?
if command -v objdump >/dev/null 2>&1 && [ -f k_sve ]; then
  printf 'sve_insns_in_k_sve\t%s\n' "$(objdump -d k_sve 2>/dev/null | grep -cE '\bz[0-9]+\.[bhsd]|ptrue|whilelo')" | tee -a "sv-$LBL.txt"
  printf 'sve_insns_in_k_neon\t%s\n' "$(objdump -d k_neon_armv8a 2>/dev/null | grep -cE '\bz[0-9]+\.[bhsd]|ptrue|whilelo')" | tee -a "sv-$LBL.txt"
fi
echo "STREAM+SVE OK"
