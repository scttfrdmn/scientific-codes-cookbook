#!/usr/bin/env bash
# spades-real, task 1 (SPAdes image): assemble one whole bacterial isolate run -- the recipe's
# invocation, `spades.py --isolate -t <n>`, at a whole run's depth.
#
#   bash assemble.sh <accession> <threads> <memory cap GiB>
#
# Runs on aarch.bio's arm64 image and bioconda's x86 one (BusyBox userland: no sha256sum --quiet,
# no date +%N), so the glue is POSIX sh/awk. Footprint: cgroup memory.peak (whole task) and SPAdes'
# own per-stage peak RSS from spades.log; a 2 s sampler records anon and tmpfs use for the shape.
# The reference-based checks are task 2 (QUAST); this task asserts completion and shape only.
# The work dir is on the container's root disk (/var/tmp, sized by disk_gib), not the /tmp tmpfs:
# SPAdes' k-mer index spill ran a 16 GiB box's 8 GiB tmpfs out of space at K77 (ENOSPC).
# Writes /tmp/out/.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
export TMPDIR=/tmp HOME=/tmp
ACC=$1 T=$2 M=$3
O=/tmp/out
mkdir -p "$O"
cd /tmp
CG=/sys/fs/cgroup
ASM=/var/tmp/asm

{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  echo "mem_total_kib $(awk '/^MemTotal/{print $2}' /proc/meminfo)"
  echo "spades_version $(spades.py --version 2>&1 | head -1)"
} > "$O/host.txt"
awk '/^online_cpus/{c=$2} /^physical_cores/{p=$2} END{printf "threads_per_core %d\n", (p>0)?c/p:0}' "$O/host.txt" >> "$O/host.txt"
cat "$O/host.txt"

echo "== SPAdes --isolate, -t $T, -m $M, whole run $ACC =="
( while : ; do
    echo "$(awk '{print $1}' /proc/uptime) $(awk '/^anon /{print $2; exit}' $CG/memory.stat 2>/dev/null) $(cat $CG/memory.current 2>/dev/null) $(df -k /tmp | awk 'NR==2{print $3}') $(du -sk "$ASM" 2>/dev/null | awk '{print $1}')" >> "$O/samples.txt"
    sleep 2
  done ) &
SPID=$!
U0=$(awk '/^usage_usec/{print $2}' $CG/cpu.stat 2>/dev/null); W0=$(awk '{print $1}' /proc/uptime)
spades.py --isolate -1 "${ACC}_1.fastq.gz" -2 "${ACC}_2.fastq.gz" -o "$ASM" -t "$T" -m "$M" > "$O/spades.stdout" 2>&1
RC=$?
U1=$(awk '/^usage_usec/{print $2}' $CG/cpu.stat 2>/dev/null); W1=$(awk '{print $1}' /proc/uptime)
kill $SPID 2>/dev/null
WORKDIR_END_KIB=$(du -sk "$ASM" | awk '{print $1}')
for f in spades.log params.txt warnings.log contigs.fasta scaffolds.fasta; do
  [ -e "$ASM/$f" ] && cp "$ASM/$f" "$O/$f"
done

FAILS=0
row() { printf '%-44s %-30s %s\n' "$1" "$2" "$3" | tee -a "$O/checks.txt"; }
check() { if [ "$1" = 1 ]; then row "$2" "$3" "ok   ($4)"; else row "$2" "$3" "FAIL ($4)"; FAILS=$((FAILS+1)); fi; }
check "$([ "$RC" = 0 ] && echo 1)" "spades.py exit" "want 0" "$RC"
check "$(grep -q 'SPAdes pipeline finished' "$O/spades.log" 2>/dev/null && echo 1)" "completion sentinel in spades.log" "'SPAdes pipeline finished'" "grep"
check "$([ -s "$O/contigs.fasta" ] && head -c 6 "$O/contigs.fasta" | grep -q '^>NODE_' && echo 1)" "contigs.fasta is FASTA" "starts >NODE_" "$(head -c 20 "$O/contigs.fasta" 2>/dev/null)"
# contig stats: all, >=500 bp, total bp, largest, N50 (of all contigs)
awk '/^>/{if(l)print l; l=0; next}{l+=length($0)}END{if(l)print l}' "$O/contigs.fasta" 2>/dev/null | sort -rn > /tmp/lens.txt
awk '{n++; t+=$1; if($1>=500){n5++; t5+=$1}; a[n]=$1} END{
       h=t/2; c=0; for(i=1;i<=n;i++){c+=a[i]; if(c>=h){n50=a[i]; break}}
       printf "contigs %d contigs_ge500 %d total_bp %d total_bp_ge500 %d largest %d n50 %d\n", n, n5, t, t5, a[1], n50}' /tmp/lens.txt | tee "$O/stats.txt"
read -r _ N _ N5 _ BP _ _ _ LARGEST _ _ <<<"$(cat "$O/stats.txt")"
check "$([ "${N:-0}" -gt 0 ] && echo 1)" "contigs > 0" "want > 0" "${N:-0}"

(cd "$O" && sha256sum contigs.fasta scaffolds.fasta 2>/dev/null) > "$O/fingerprints.txt"
cat "$O/fingerprints.txt"

# SPAdes logs "<elapsed> <current> / <peak>  INFO ..." per line; the peak is the process's max RSS.
awk '{for(i=2;i<NF;i++) if($(i+1)=="/" && $(i+2) ~ /^[0-9]+[KMGT]?$/){v=$(i+2); u=substr(v,length(v)); n=v+0;
        if(u=="K")n/=1048576; else if(u=="M")n/=1024; else if(u=="T")n*=1024; else if(u!="G")n/=1073741824;
        if(n>mx)mx=n; break}} END{printf "spades_log_peak_rss_gib %.2f\n", mx}' "$O/spades.log" > "$O/footprint.txt"
{
  echo "spades_rc $RC"
  awk -v a="$W0" -v b="$W1" 'BEGIN{printf "spades_wall_s %.1f\n", b-a}'
  awk -v du="$(( ${U1:-0} - ${U0:-0} ))" -v a="$W0" -v b="$W1" 'BEGIN{printf "avg_busy_cpus %.2f\n", (b>a)? du/((b-a)*1e6) : 0}'
} | tee "$O/times.txt"
{
  echo "cgroup_memory_peak_bytes $(cat $CG/memory.peak 2>/dev/null)"
  awk '$2>m{m=$2} END{printf "sampled_anon_max_bytes %.0f\n", m}' "$O/samples.txt"
  awk '$4>m{m=$4} END{printf "sampled_tmpfs_used_max_kib %.0f\n", m}' "$O/samples.txt"
  awk '$5>m{m=$5} END{printf "sampled_workdir_max_kib %.0f\n", m}' "$O/samples.txt"
  echo "asm_dir_at_end_kib $WORKDIR_END_KIB"
} >> "$O/footprint.txt"
cat "$O/footprint.txt"
echo "assemble_failures $FAILS" | tee -a "$O/checks.txt"
[ "$FAILS" = 0 ] || exit 1
echo "ASSEMBLE OK $ACC"
