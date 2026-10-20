#!/usr/bin/env bash
# megahit-real, task 1 (MEGAHIT image): assemble one whole shotgun run of the ZymoBIOMICS D6300
# mock community -- the recipe's invocation, `megahit --min-count 2 -t <n>`, at a whole run's depth.
#
#   bash assemble.sh <accession> <threads> <read count> <memory bytes> [extra megahit flag]
#   (writes /tmp/out/<accession>/; the extra flag is only --no-hw-accel, for 01-assemble-nohw)
#
# Runs on aarch.bio's arm64 image and bioconda's x86 one (BusyBox userland: no sha256sum --quiet,
# no date +%N), so the glue is POSIX sh/awk; python3 is MEGAHIT's own interpreter. Peak RSS is
# ru_maxrss over every descendant (RUSAGE_CHILDREN), plus MEGAHIT's own per-step maxrss lines.
# The reference-based checks are task 2 (QUAST); this task asserts completion, read accounting and
# the contig file's agreement with MEGAHIT's own summary. The work dir is on the container's root
# disk (/var/tmp, sized by disk_gib), not the /tmp tmpfs.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
export TMPDIR=/tmp HOME=/tmp
ACC=$1 T=$2 WANT_READS=$3 MEM=$4 EXTRA=${5:-}
O=/tmp/out/$ACC
mkdir -p "$O"
cd /tmp
CG=/sys/fs/cgroup
ASM=/var/tmp/asm-$ACC MHTMP=/var/tmp/mhtmp-$ACC
mkdir -p "$MHTMP"

{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  echo "x86_flags $(grep -m1 '^flags' /proc/cpuinfo | tr ' ' '\n' | grep -E '^(popcnt|bmi2|avx2|avx512f)$' | tr '\n' ' ')"
  echo "mem_total_kib $(awk '/^MemTotal/{print $2}' /proc/meminfo)"
  echo "megahit_version $(megahit --version 2>&1 | head -1)"
} > "$O/host.txt"
awk '/^online_cpus/{c=$2} /^physical_cores/{p=$2} END{printf "threads_per_core %d\n", (p>0)?c/p:0}' "$O/host.txt" >> "$O/host.txt"
cat "$O/host.txt"

echo "== MEGAHIT --min-count 2, -t $T, -m $MEM ${EXTRA}, whole run $ACC =="
( while : ; do
    echo "$(awk '{print $1}' /proc/uptime) $(awk '/^anon /{print $2; exit}' $CG/memory.stat 2>/dev/null) $(df -k /tmp | awk 'NR==2{print $3}') $(du -sk "$ASM" "$MHTMP" 2>/dev/null | awk '{s+=$1}END{print s}')" >> "$O/samples.txt"
    sleep 5
  done ) &
SPID=$!
U0=$(awk '/^usage_usec/{print $2}' $CG/cpu.stat 2>/dev/null); W0=$(awk '{print $1}' /proc/uptime)
python3 -u -c '
import resource, subprocess, sys
rc = subprocess.call(sys.argv[2:])
open(sys.argv[1], "w").write("ru_maxrss_children_kib %d\n" % resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
sys.exit(rc)' "$O/rusage.txt" \
  megahit -1 "${ACC}_1.fastq.gz" -2 "${ACC}_2.fastq.gz" -t "$T" --min-count 2 -m "$MEM" $EXTRA \
    --tmp-dir "$MHTMP" -o "$ASM" > "$O/megahit.stdout" 2>&1
RC=$?
U1=$(awk '/^usage_usec/{print $2}' $CG/cpu.stat 2>/dev/null); W1=$(awk '{print $1}' /proc/uptime)
kill $SPID 2>/dev/null
WORKDIR_END_KIB=$(du -sk "$ASM" | awk '{print $1}')
cp "$ASM/log" "$O/megahit.log" 2>/dev/null
cp "$ASM/options.json" "$O/" 2>/dev/null
cp "$ASM/final.contigs.fa" "$O/final.contigs.fa" 2>/dev/null

FAILS=0
row() { printf '%-48s %-30s %s\n' "$1" "$2" "$3" | tee -a "$O/checks.txt"; }
check() { if [ "$1" = 1 ]; then row "$2" "$3" "ok   ($4)"; else row "$2" "$3" "FAIL ($4)"; FAILS=$((FAILS+1)); fi; }
check "$([ "$RC" = 0 ] && echo 1)" "megahit exit" "want 0" "$RC"
check "$(grep -q 'ALL DONE' "$O/megahit.log" 2>/dev/null && echo 1)" "completion sentinel in log" "'ALL DONE'" "grep"
# "Lib 0 (...): pe, <n> reads, <m> max length" -- every staged read entered the library
NR_LIB=$(grep -o 'pe, [0-9]* reads' "$O/megahit.log" | awk '{print $2}' | head -1)
check "$([ "${NR_LIB:-x}" = "$WANT_READS" ] && echo 1)" "reads in library == staged reads" "want $WANT_READS" "${NR_LIB:-none}"
check "$([ -s "$O/final.contigs.fa" ] && head -c 2 "$O/final.contigs.fa" | grep -q '^>k' && echo 1)" "final.contigs.fa is FASTA" "starts >k" "$(head -c 30 "$O/final.contigs.fa" 2>/dev/null)"

# every header's len= equals its sequence; count and total equal MEGAHIT's own summary line
awk '/^>/{if(h!=""){print h, l}; h=$0; l=0; next}{l+=length($0)} END{if(h!="")print h, l}' "$O/final.contigs.fa" \
  | awk -v acc="$ACC" '{d=""; for(i=1;i<=NF;i++) if($i ~ /^len=/){split($i,a,"="); d=a[2]} if(d!=$NF) bad++; n++; t+=$NF; f="/tmp/lens-" acc ".txt"; print $NF > f}
         END{printf "contigs %d total_bp %d len_field_mismatches %d\n", n, t, bad+0}' | tee "$O/file-stats.txt"
read -r _ N _ BP _ BAD <<<"$(cat "$O/file-stats.txt")"
SUM=$(grep -E '[0-9]+ contigs, total [0-9]+ bp' "$O/megahit.log" | tail -1)
echo "megahit_summary $SUM" | tee -a "$O/file-stats.txt"
LN=$(echo "$SUM" | grep -oE '[0-9]+ contigs' | awk '{print $1}'); LBP=$(echo "$SUM" | grep -oE 'total [0-9]+ bp' | awk '{print $2}')
check "$([ "${BAD:-1}" = 0 ] && [ "${N:-0}" -gt 0 ] && echo 1)" "every header len= == its sequence length" "want 0 mismatches" "${BAD:-?} of ${N:-?}"
check "$([ "${N:-0}" = "${LN:-x}" ] && echo 1)" "contigs in file == MEGAHIT's summary" "want ${LN:-?}" "${N:-?}"
check "$([ "${BP:-0}" = "${LBP:-x}" ] && echo 1)" "total bp in file == MEGAHIT's summary" "want ${LBP:-?}" "${BP:-?}"
sort -rn "/tmp/lens-$ACC.txt" | awk '{n++; t+=$1; a[n]=$1; if($1>=1000){n1++; t1+=$1}} END{h=t/2; c=0; for(i=1;i<=n;i++){c+=a[i]; if(c>=h){n50=a[i]; break}}
  printf "contigs %d total_bp %d contigs_ge1000 %d total_bp_ge1000 %d largest %d n50 %d\n", n, t, n1, t1, a[1], n50}' | tee "$O/stats.txt"

(cd "$O" && sha256sum final.contigs.fa) > "$O/fingerprints.txt"
python3 -u canon.py "$O/final.contigs.fa" >> "$O/fingerprints.txt"
cat "$O/fingerprints.txt"
echo "megahit_core_line $(grep -m1 'megahit_core' "$O/megahit.log")" >> "$O/host.txt"
{
  echo "megahit_rc $RC"
  awk -v a="$W0" -v b="$W1" 'BEGIN{printf "megahit_wall_s %.1f\n", b-a}'
  awk -v du="$(( ${U1:-0} - ${U0:-0} ))" -v a="$W0" -v b="$W1" 'BEGIN{printf "avg_busy_cpus %.2f\n", (b>a)? du/((b-a)*1e6) : 0}'
  grep -oE 'ALL DONE\. Time elapsed: [0-9.]+ seconds' "$O/megahit.log" | tail -1
} | tee "$O/times.txt"
grep -E ' - (Convert reads|Start assembly|k list|Extract solid|Build graph|Assemble contigs|Local assembly|Extract iterative|Merging|ALL DONE)' "$O/megahit.log" > "$O/steps.txt"
{
  cat "$O/rusage.txt" 2>/dev/null
  grep -oE 'maxrss: [0-9]+' "$O/megahit.log" | awk '$2>m{m=$2} END{printf "megahit_log_max_maxrss_kib %d\n", m}'
  echo "cgroup_memory_peak_bytes_task_so_far $(cat $CG/memory.peak 2>/dev/null)"
  awk '$2>m{m=$2} END{printf "sampled_anon_max_bytes %.0f\n", m}' "$O/samples.txt"
  awk '$3>m{m=$3} END{printf "sampled_tmpfs_used_max_kib %.0f\n", m}' "$O/samples.txt"
  awk '$4>m{m=$4} END{printf "sampled_workdir_max_kib %.0f\n", m}' "$O/samples.txt"
  echo "asm_dir_at_end_kib $WORKDIR_END_KIB"
} | tee "$O/footprint.txt"
rm -rf "$ASM" "$MHTMP"   # built by this task; the outputs are copied above
echo "assemble_failures $FAILS" | tee -a "$O/checks.txt"
[ "$FAILS" = 0 ] || exit 1
echo "ASSEMBLE OK $ACC"
