# Sourced by every polish-real task. POSIX sh + awk only: bioconda's x86 images are BusyBox.

# host_info <file>: the chip, its core/thread layout and memory -- what makes timings comparable.
host_info() {
  {
    echo "uname_m $(uname -m)"
    echo "online_cpus $(getconf _NPROCESSORS_ONLN)"
    # threads per core from the kernel's own sibling list ("0", "0,96", "0-1")
    echo "threads_per_core $(awk -F, '{n=0; for(i=1;i<=NF;i++){ if(split($i,r,"-")==2) n+=r[2]-r[1]+1; else n++ } print n}' \
      /sys/devices/system/cpu/cpu0/topology/thread_siblings_list)"
    awk -F': *' '/^model name/{m=$2} /^CPU part/{p=$2} END{print "cpu_model " (m!=""?m:"arm cpu_part " p)}' /proc/cpuinfo
    awk '/^MemTotal/{print "mem_total_kib " $2}' /proc/meminfo
    df -k /tmp /var/tmp | awk 'NR>1{print "fs " $6 " " $2 " KiB"}'
  } > "$1"
  cat "$1"
}

# vmhwm_watch <pid> <file>: the kernel's own high-water mark (VmHWM) for one process. It is
# monotonic, so reading it often loses nothing but the last second's growth -- unlike sampling RSS.
vmhwm_watch() {
  while kill -0 "$1" 2>/dev/null; do
    v=$(awk '/^VmHWM/{print $2}' /proc/"$1"/status 2>/dev/null)
    [ -n "$v" ] && echo "$v" > "$2"
    sleep 1
  done
}
