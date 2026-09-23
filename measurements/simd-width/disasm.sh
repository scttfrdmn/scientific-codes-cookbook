set -euo pipefail
export PATH=/opt/conda/bin:$PATH
export TMPDIR=/tmp HOME=/tmp
cd /tmp
LBL="${LBL:-unknown}"
CC=aarch64-conda-linux-gnu-gcc
OD=""; for o in aarch64-conda-linux-gnu-objdump objdump; do command -v $o >/dev/null 2>&1 && { OD=$o; break; }; done
test -n "$OD" || { echo "no objdump"; exit 1; }
FEAT=$(awk -F': *' '/^Features/{print $2; exit}' /proc/cpuinfo)
has(){ case " $FEAT " in *" $1 "*) return 0;; *) return 1;; esac; }
cat > kern.c <<'EOF'
#include <stdio.h>
#include <time.h>
#define N 4096
#define REP 200000
static double a[N], b[N];
int main(void){ for(long i=0;i<N;i++){a[i]=1.0+i*1e-6;b[i]=2.0-i*1e-6;}
 double s=0; struct timespec t0,t1; clock_gettime(CLOCK_MONOTONIC,&t0);
 for(long r=0;r<REP;r++){ double p=0; for(long i=0;i<N;i++) p+=a[i]*b[i]; s+=p; }
 clock_gettime(CLOCK_MONOTONIC,&t1);
 double dt=(t1.tv_sec-t0.tv_sec)+(t1.tv_nsec-t0.tv_nsec)/1e9;
 printf("%.2f %.6e\n",(2.0*N*REP)/dt/1e9,s); return 0; }
EOF
count(){ $OD -d "$1" 2>/dev/null | grep -cE '\bz[0-9]+\.[bhsd]|\bptrue|\bwhilelo|\bld1[bhwd]|\bst1[bhwd]'; }
countneon(){ $OD -d "$1" 2>/dev/null | grep -cE '\bv[0-9]+\.[0-9]+[bhsd]'; }
{
printf 'label\t%s\n' "$LBL"
printf 'sve_bits\t%s\n' "$([ -r /proc/sys/abi/sve_default_vector_length ] && echo $(( $(cat /proc/sys/abi/sve_default_vector_length)*8 )) || echo 0)"
if has sve; then TVB="-mcpu=neoverse-512tvb"; else TVB="-mcpu=neoverse-n1"; fi
printf 'tvb_flag\t%s\n' "$TVB"
for v in "neon:-march=armv8-a" "tvb:$TVB" "sve:-march=armv8.2-a+sve" "sve2:-march=armv9-a+sve2"; do
  n=${v%%:*}; f=${v#*:}
  case "$n" in sve)  has sve  || { printf 'build_%s\tskipped (no sve)\n' "$n"; continue; };; esac
  case "$n" in sve2) has sve2 || { printf 'build_%s\tskipped (no sve2)\n' "$n"; continue; };; esac
  if $CC -O3 -ffast-math $f kern.c -o "k_$n" 2>/dev/null; then
    printf 'build_%s\tGFLOPs=%s  sve_insns=%s  neon_insns=%s\n' "$n" "$(./k_$n | awk '{print $1}')" "$(count k_$n)" "$(countneon k_$n)"
  else printf 'build_%s\tdid-not-compile\n' "$n"; fi
done
} | tee "dis-$LBL.txt"
