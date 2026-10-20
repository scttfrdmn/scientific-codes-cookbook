# Sourced by both phasing-real task scripts. Portable across the aarchbio (GNU/mawk) and bioconda
# x86 (BusyBox) userlands: no join/numfmt/perl, no `date +%N`, no `sha256sum --quiet`.
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
export TMPDIR=/var/tmp HOME=/tmp LC_ALL=C
O=/tmp/out          # small evidence + the outputs, staged out by spawn
W=/var/tmp/w        # work files on the container disk, not the half-RAM tmpfs (spawn#1179)
mkdir -p "$O" "$W"
: > "$O/results.tsv"; : > "$O/checks.tsv"; : > "$O/timings.tsv"
EMPTY_MD5=d41d8cd98f00b204e9800998ecf8427e
rec(){ printf '%s\t%s\n' "$1" "$(printf '%s' "$2")" >> "$O/results.tsv"; echo "$1 = $2"; }
chk(){ # claim, lhs, rhs -- exact equality of two non-empty strings
  if [ "$2" = "$3" ] && [ -n "$2" ] && [ "$2" != "$EMPTY_MD5" ]; then s=ok; else s=FAIL; fi
  printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$s" >> "$O/checks.tsv"; echo "CHECK [$s] $1: $2 vs $3"; }
tic(){ T0=$(date +%s); }
toc(){ d=$(( $(date +%s) - T0 )); printf '%s\t%s\n' "$1" "$d" >> "$O/timings.tsv"; echo "TIME $1 ${d}s"; }
md5(){ md5sum | awk '{print $1}'; }
sha(){ sha256sum "$1" | awk '{print $1}'; }

machine(){
  rec arch "$(uname -m)"
  rec cpu_model "$(awk -F': ' '/^model name|^CPU part/{print $2; exit}' /proc/cpuinfo)"
  rec cpus "$(grep -c ^processor /proc/cpuinfo)"
  # threads per core from sysfs (lscpu is not in the x86 images): siblings of cpu0
  rec threads_per_core "$(awk -F, '{n=0; for(i=1;i<=NF;i++){ if(split($i,r,"-")==2) n+=r[2]-r[1]+1; else n++ } print n}' \
      /sys/devices/system/cpu/cpu0/topology/thread_siblings_list 2>/dev/null)"
  rec mem_total_kib "$(awk '/^MemTotal/{print $2}' /proc/meminfo)"
}

finish(){ # banner
  df -k /var/tmp | awk 'NR==2{printf "disk_used_gib\t%.1f\n", $3/1048576}' >> "$O/results.tsv"
  df -k /tmp | awk 'NR==2{printf "tmpfs_used_gib\t%.1f\n", $3/1048576}' >> "$O/results.tsv"
  [ -r /sys/fs/cgroup/memory.peak ] && rec cgroup_memory_peak_mib "$(awk '{printf "%.0f", $1/1048576}' /sys/fs/cgroup/memory.peak)"
  FAILS=$(awk -F'\t' '$4!="ok"' "$O/checks.tsv" | wc -l | tr -d ' ')
  NCHK=$(wc -l < "$O/checks.tsv" | tr -d ' ')
  echo "== checks: $FAILS of $NCHK failed =="
  cat "$O/checks.tsv"
  if [ "$FAILS" = 0 ] && [ "$NCHK" -gt 0 ]; then echo "$1 OK"; exit 0; else echo "$1 FAILED ($FAILS)"; exit 1; fi
}
