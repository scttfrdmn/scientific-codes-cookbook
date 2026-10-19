# Sourced by every imputation-real task script. Portable across the aarchbio (GNU/mawk) and
# bioconda x86 (BusyBox) userlands: no join/numfmt/perl, no `date +%N`, no `sha256sum --quiet`.
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
export TMPDIR=/var/tmp HOME=/tmp LC_ALL=C
O=/tmp/out          # small evidence + the outputs, staged out by spawn
W=/var/tmp/w        # work files on the container disk, not the half-RAM tmpfs (spawn#1179)
mkdir -p "$O" "$W"
: > "$O/results.tsv"; : > "$O/checks.tsv"; : > "$O/timings.tsv"
FAILS=0
EMPTY_MD5=d41d8cd98f00b204e9800998ecf8427e
VCF=ALL.chr20.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz
T=16
rec(){ printf '%s\t%s\n' "$1" "$(printf '%s' "$2")" >> "$O/results.tsv"; echo "$1 = $2"; }
chk(){ # claim, lhs, rhs -- exact equality of two non-empty strings
  if [ "$2" = "$3" ] && [ -n "$2" ] && [ "$2" != "$EMPTY_MD5" ]; then s=ok; else s=FAIL; FAILS=$((FAILS+1)); fi
  printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$s" >> "$O/checks.tsv"; echo "CHECK [$s] $1: $2 vs $3"; }
tic(){ T0=$(date +%s); }
toc(){ d=$(( $(date +%s) - T0 )); printf '%s\t%s\n' "$1" "$d" >> "$O/timings.tsv"; echo "TIME $1 ${d}s"; }
md5(){ md5sum | awk '{print $1}'; }
body_md5(){ gzip -dc "$1" | grep -v '^##' | md5; }   # records + column header, not the dated meta lines

machine(){
  rec arch "$(uname -m)"
  rec cpu_model "$(awk -F': ' '/^model name|^CPU part/{print $2; exit}' /proc/cpuinfo)"
  rec cpus "$(grep -c ^processor /proc/cpuinfo)"
  # threads per core from sysfs (lscpu is not in the x86 images): siblings of cpu0
  rec threads_per_core "$(awk -F, '{n=0; for(i=1;i<=NF;i++){ if(split($i,r,"-")==2) n+=r[2]-r[1]+1; else n++ } print n}' \
      /sys/devices/system/cpu/cpu0/topology/thread_siblings_list 2>/dev/null)"
  rec mem_total_kib "$(awk '/^MemTotal/{print $2}' /proc/meminfo)"
}

# Peak resident memory per process name: VmHWM is the kernel's own high-water mark, so reading it
# once a second loses only growth in a process's final second -- unlike sampling current RSS.
hwm_start(){ # names...
  : > "$W/hwm.samples"
  ( while :; do
      for f in /proc/[0-9]*/status; do
        awk -v names=" $* " '/^Name:/{n=$2} /^VmHWM:/{ if (index(names, " " n " ")) print n, $2 }' "$f" 2>/dev/null
      done >> "$W/hwm.samples"
      sleep 1
    done ) &
  HWM_PID=$!
}
hwm_stop(){
  kill "$HWM_PID" 2>/dev/null
  awk '{ if ($2 > m[$1]) m[$1] = $2 } END { for (n in m) printf "peak_vmhwm_mib_%s\t%.0f\n", n, m[n]/1024 }' "$W/hwm.samples" \
    | sort | while IFS="$(printf '\t')" read -r k v; do rec "$k" "$v"; done
  [ -r /sys/fs/cgroup/memory.peak ] && rec cgroup_memory_peak_mib "$(awk '{printf "%.0f", $1/1048576}' /sys/fs/cgroup/memory.peak)"
}

finish(){ # banner
  df -k /var/tmp | awk 'NR==2{printf "disk_used_gib\t%.1f\n", $3/1048576}' >> "$O/results.tsv"
  df -k /tmp | awk 'NR==2{printf "tmpfs_used_gib\t%.1f\n", $3/1048576}' >> "$O/results.tsv"
  echo "== checks: $FAILS failed =="
  cat "$O/checks.tsv"
  if [ "$FAILS" = 0 ]; then echo "$1 OK"; exit 0; else echo "$1 FAILED ($FAILS)"; exit 1; fi
}
