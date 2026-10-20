# Sourced by align.sh. Portable across the aarchbio (GNU) and bioconda x86 (BusyBox) userlands:
# no join/numfmt/perl, no `date +%N`, no `sha256sum --quiet`, no awk bit functions.
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
export TMPDIR=/var/tmp HOME=/tmp LC_ALL=C
O=/tmp/out          # small evidence, staged out by spawn
X=/tmp/red          # bwa's per-record core fields, staged out for the bwa-mem2 task to compare against
W=/var/tmp/w        # SAM and the untarred index on the container disk, not the half-RAM tmpfs (spawn#1179)
mkdir -p "$O" "$X" "$W"
: > "$O/results.tsv"; : > "$O/checks.tsv"; : > "$O/timings.tsv"
EMPTY_SHA=e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
rec(){ printf '%s\t%s\n' "$1" "$(printf '%s' "$2")" >> "$O/results.tsv"; echo "$1 = $2"; }
chk(){ # claim, lhs, rhs -- exact equality of two non-empty strings
  if [ "$2" = "$3" ] && [ -n "$2" ] && [ "$2" != "$EMPTY_SHA" ]; then s=ok; else s=FAIL; fi
  printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$s" >> "$O/checks.tsv"; echo "CHECK [$s] $1: $2 vs $3"; }
tic(){ T0=$(date +%s); }
toc(){ d=$(( $(date +%s) - T0 )); printf '%s\t%s\n' "$1" "$d" >> "$O/timings.tsv"; echo "TIME $1 ${d}s"; }
sha(){ sha256sum | awk '{print $1}'; }

machine(){
  rec arch "$(uname -m)"
  rec cpu_model "$(awk -F': ' '/^model name|^CPU part/{print $2; exit}' /proc/cpuinfo)"
  rec cpus "$(grep -c ^processor /proc/cpuinfo)"
  # threads per core from sysfs (lscpu is not in the x86 images): siblings of cpu0
  rec threads_per_core "$(awk -F, '{n=0; for(i=1;i<=NF;i++){ if(split($i,r,"-")==2) n+=r[2]-r[1]+1; else n++ } print n}' \
      /sys/devices/system/cpu/cpu0/topology/thread_siblings_list 2>/dev/null)"
  rec cpu_avx512bw "$(grep -m1 -c 'avx512bw' /proc/cpuinfo)"
  rec cpu_sve "$(grep -m1 -c -w 'sve' /proc/cpuinfo)"
  rec mem_total_kib "$(awk '/^MemTotal/{print $2}' /proc/meminfo)"
}

peak(){ # peak <pid>: the process's VmHWM in kB; a high-water mark, so the last read is the peak
  local h=0 x
  while kill -0 "$1" 2>/dev/null; do
    x=$(awk '/^VmHWM/{print $2}' /proc/$1/status 2>/dev/null); [ -n "$x" ] && h=$x
    sleep 1
  done
  echo $h
}
cpu_usec(){ awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null || echo 0; }

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
