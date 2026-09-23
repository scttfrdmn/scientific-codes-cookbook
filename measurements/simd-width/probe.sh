set -euo pipefail
export PATH=/opt/conda/bin:$PATH
export TMPDIR=/tmp HOME=/tmp
cd /tmp
GEN="${GEN:-unknown}"
{
printf '== %s ==\n' "$GEN"
printf 'uname\t%s\n' "$(uname -m)"
printf 'nproc\t%s\n' "$(nproc)"
# CPU identity: implementer 0x41 = ARM; part 0xd0c=Neoverse-N1(Gv2) 0xd40=V1(Gv3) 0xd4f=V2(Gv4)
printf 'cpu_implementer\t%s\n' "$(awk -F': *' '/CPU implementer/{print $2; exit}' /proc/cpuinfo)"
printf 'cpu_part\t%s\n'        "$(awk -F': *' '/CPU part/{print $2; exit}' /proc/cpuinfo)"
printf 'cpu_variant\t%s\n'     "$(awk -F': *' '/CPU variant/{print $2; exit}' /proc/cpuinfo)"
FEAT=$(awk -F': *' '/^Features/{print $2; exit}' /proc/cpuinfo)
printf 'features\t%s\n' "$FEAT"
# the two that matter for the SVE question
for f in asimd sve sve2 i8mm bf16 svebf16 svei8mm; do
  case " $FEAT " in *" $f "*) printf 'has_%s\tyes\n' "$f";; *) printf 'has_%s\tno\n' "$f";; esac
done
# SVE vector length in BITS, if the kernel exposes it (bytes * 8)
if [ -r /proc/sys/abi/sve_default_vector_length ]; then
  printf 'sve_vector_bits\t%s\n' "$(( $(cat /proc/sys/abi/sve_default_vector_length) * 8 ))"
else
  printf 'sve_vector_bits\tn/a (no SVE)\n'
fi
printf 'neon_vector_bits\t128   (architectural, every AArch64)\n'
printf 'caches\t%s\n' "$(lscpu 2>/dev/null | awk -F': *' '/cache/{printf "%s=%s ", $1, $2}')"
printf 'numa_nodes\t%s\n' "$(lscpu 2>/dev/null | awk -F': *' '/NUMA node\(s\)/{print $2; exit}')"
} | tee "simd-$GEN.txt"
