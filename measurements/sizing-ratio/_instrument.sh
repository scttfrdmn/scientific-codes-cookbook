# ---- ratio instrument (inlined into each measurement script by build-specs.py;
#      single authoring copy so the four can't drift; NOT sourced at runtime) ----
CG=/sys/fs/cgroup
[ -r "$CG/cpu.stat" ] || echo "WARN: cgroup v2 unreadable -- cores-used degraded"
capture_topo(){ {
  echo "== box =="
  echo "instance=${INSTANCE:-?}  rate_usd_hr=${RATE:-?}  arch=$(uname -m)  vcpus=$(nproc)"
  if command -v lscpu >/dev/null 2>&1; then
    lscpu | grep -E 'Architecture|Model name|^CPU\(s\)|Thread\(s\) per core|Core\(s\) per socket|Socket\(s\)|NUMA'
  else
    # lscpu absent (biocontainers): derive threads/core from /proc/cpuinfo
    sib=$(awk -F: '/^siblings/{gsub(/ /,"",$2);print $2;exit}' /proc/cpuinfo); sib=${sib:-0}
    cor=$(awk -F: '/^cpu cores/{gsub(/ /,"",$2);print $2;exit}' /proc/cpuinfo); cor=${cor:-0}
    echo "model=$(awk -F: '/^model name/{sub(/^ /,"",$2);print $2;exit}' /proc/cpuinfo)"
    echo "siblings=$sib cpu_cores=$cor threads_per_core=$(( cor>0 ? sib/cor : 1 ))"
    echo "numa_nodes=$(ls -d /sys/devices/system/node/node[0-9]* 2>/dev/null | wc -l)"
  fi
  echo "# threads/core=1 => no SMT (Graviton/AMD here); =2 => SMT (Intel c8i: 8 vCPU=4 cores)"
} > /tmp/topo.txt; }
# wall clock from /proc/uptime (seconds, 2-decimal) -- portable; biocontainers' date
# lacks +%N and yields "<s>%N", which then reads as modulo-N in bash arithmetic.
usnap(){ printf '%s %s\n' "$(awk '{print $1}' /proc/uptime)" \
  "$(awk '/^usage_usec/{print $2}' $CG/cpu.stat 2>/dev/null)"; }
mon_start(){ SF="/tmp/samples-$1.tsv"; : > "$SF"
  usnap > "/tmp/snap0-$1"                        # before-snapshot: avg_cores survives a dead sampler
  ( while :; do printf '%s\t%s\t%s\t%s\n' "$(awk '{print $1}' /proc/uptime)" \
      "$(cat $CG/memory.current 2>/dev/null)" \
      "$(awk '/^anon /{print $2}' $CG/memory.stat 2>/dev/null)" \
      "$(awk '/^usage_usec/{print $2}' $CG/cpu.stat 2>/dev/null)"; sleep 1
    done ) >> "$SF" & echo $! > "/tmp/mon-$1.pid"; }
mon_stop(){ usnap > "/tmp/snap1-$1"; kill "$(cat /tmp/mon-$1.pid 2>/dev/null)" 2>/dev/null || true; }
# report TAG: avg_cores/wall/cost from the sampler-INDEPENDENT before/after snapshot;
#   peak_rss/anon_max/peak_cores from the 1 Hz sampler; sampler_ticks makes a dead
#   sampler explicit (sane avg + ticks<=1 => sampler died, peak_* unreliable, not "idle").
report(){
  read -r w0 u0 < "/tmp/snap0-$1"; read -r w1 u1 < "/tmp/snap1-$1"
  local WALL AVG
  WALL=$(awk -v a="$w0" -v b="$w1" 'BEGIN{printf "%.1f", b-a}')
  AVG=$(awk -v du="$(( ${u1:-0} - ${u0:-0} ))" -v w="$WALL" \
        'BEGIN{printf "%.2f",(w>0? du/(w*1e6):0)}')
  awk -F'\t' -v tag="$1" -v rate="${RATE:-0}" -v wall="$WALL" -v avg="$AVG" '
    {ticks++; if($2>pm)pm=$2; if($3>am)am=$3;
     if(NR>1){dt=$1-pt; if(dt>0){c=($4-pu)/(dt*1e6); if(c>pc)pc=c}}
     pt=$1; pu=$4}
    END{printf "%-6s peak_rss=%.0f anon_max=%.0f wall_s=%.1f avg_cores=%s peak_cores=%.2f sampler_ticks=%d cost_usd=%.4f\n",
        tag, pm+0, am+0, wall, avg, pc+0, ticks+0, rate*wall/3600}' "/tmp/samples-$1.tsv"; }
# --------------------------------------------------------------------------------
