set -euo pipefail
export PATH=/opt/conda/bin:$PATH
export TMPDIR=/tmp HOME=/tmp
cd /tmp
LBL="${LBL:-unknown}"
N=$(nproc)

cat > triad.py <<'PY'
# STREAM-like triad on arrays far larger than any cache: a = b + s*c
# numpy elementwise is single-threaded, so ONE process = one core's worth of bandwidth.
import numpy as np, time, sys
MB = 64
n = MB*1024*1024//8                       # float64 elements per array
b = np.ones(n); c = np.ones(n); a = np.empty(n); s = 3.0
for _ in range(2): a[:] = b + s*c         # warm
best = 0.0
for _ in range(5):
    t0 = time.perf_counter(); a[:] = b + s*c; dt = time.perf_counter() - t0
    gb = (3*n*8)/1e9                      # 2 reads + 1 write
    best = max(best, gb/dt)
print(f"{best:.3f}")
PY

cat > dgemm.py <<'PY'
# Compute-bound FP: threaded OpenBLAS dgemm. Matrices sized to stay in-core-ish.
import numpy as np, time, os
m = 3000
A = np.random.rand(m,m); B = np.random.rand(m,m)
A @ B                                      # warm
best = 0.0
for _ in range(3):
    t0 = time.perf_counter(); A @ B; dt = time.perf_counter() - t0
    best = max(best, (2.0*m**3)/dt/1e9)    # GFLOP/s
print(f"{best:.2f}")
PY

echo "== identity =="
{
printf 'label\t%s\n' "$LBL"
printf 'nproc\t%s\n' "$N"
printf 'cpu_part\t%s\n' "$(awk -F': *' '/CPU part/{print $2; exit}' /proc/cpuinfo)"
printf 'sve_bits\t%s\n' "$([ -r /proc/sys/abi/sve_default_vector_length ] && echo $(( $(cat /proc/sys/abi/sve_default_vector_length) * 8 )) || echo none)"
printf 'mem_total_gib\t%s\n' "$(awk '/MemTotal/{printf "%.0f", $2/1048576}' /proc/meminfo)"
printf 'gib_per_core\t%s\n' "$(awk -v n="$N" '/MemTotal/{printf "%.2f", $2/1048576/n}' /proc/meminfo)"
printf 'numpy_blas\t%s\n' "$(python -c "
import numpy as np
try:
    import numpy.__config__ as c
    d = getattr(c,'CONFIG',None)
    print((d or {}).get('Build Dependencies',{}).get('blas',{}).get('name','?') if d else '?')
except Exception: print('?')" 2>/dev/null)"
} | tee "bwfp-$LBL.txt"

echo "== aggregate memory bandwidth: N single-threaded triads in parallel =="
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
# 1 process = per-core bandwidth; N processes = what the socket actually delivers
python triad.py > bw1.txt
for i in $(seq 1 "$N"); do python triad.py > "bwp_$i.txt" & done
wait
AGG=$(cat bwp_*.txt | awk '{s+=$1} END{printf "%.1f", s}')
ONE=$(cat bw1.txt)
{
printf 'triad_1proc_GBs\t%s\n' "$ONE"
printf 'triad_%sproc_agg_GBs\t%s\n' "$N" "$AGG"
printf 'triad_per_core_GBs\t%s\n' "$(awk -v a="$AGG" -v n="$N" 'BEGIN{printf "%.2f", a/n}')"
} | tee -a "bwfp-$LBL.txt"

echo "== compute-bound FP: threaded dgemm =="
export OPENBLAS_NUM_THREADS="$N" OMP_NUM_THREADS="$N"
G=$(python dgemm.py)
{
printf 'dgemm_%sthread_GFLOPs\t%s\n' "$N" "$G"
printf 'dgemm_per_core_GFLOPs\t%s\n' "$(awk -v g="$G" -v n="$N" 'BEGIN{printf "%.2f", g/n}')"
} | tee -a "bwfp-$LBL.txt"
echo "BWFP OK"
