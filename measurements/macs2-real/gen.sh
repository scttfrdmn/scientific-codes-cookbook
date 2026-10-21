set +e
set -uo pipefail
export PATH=/opt/conda/bin:$PATH
export TMPDIR=/tmp HOME=/tmp MPLCONFIGDIR=/tmp/mpl
cd /tmp

# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
#
# One rung of the generation sweep. Everything except the chip is held fixed: same image
# digest, same two input objects, same 4 vCPU / 32 GiB shape, same macs2 flags.
#
# SIZED FROM A MEASURED RUN, NOT A GUESS. The m8g.2xlarge clocking run reported macs2 at
# 389 s, peak RSS 7.6 GiB, avg_cores 1.01 and a tmpfs high-water of 11.1 GiB. So: 4 cores is
# ample for a single-threaded tool, and the 32 GiB is bought for /tmp (tmpfs = half of RAM,
# and 11.1 GiB of it is needed) rather than for compute. A 16 GiB box would have run out of
# staging space, which is why this is r*.xlarge and not m*.large.

echo "== the bytes must match ENCODE's own published md5 before any compute =="
md5sum -c encode.md5 || { echo "FAIL: a BAM does not match ENCODE's published md5"; exit 1; }

CG=/sys/fs/cgroup
sampler() {
  : > /tmp/samples.tsv
  while : ; do
    mc=$(cat $CG/memory.current 2>/dev/null || echo 0)
    cu=$(awk '/^usage_usec/{print $2; exit}' $CG/cpu.stat 2>/dev/null || echo 0)
    tk=$(df -k /tmp 2>/dev/null | awk 'NR==2{print $3}')
    printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "${mc:-0}" "${cu:-0}" "${tk:-0}" >> /tmp/samples.tsv
    sleep 1
  done
}
sampler & SPID=$!

echo "== macs2 callpeak, whole genome =="
T0=$(date +%s)
macs2 callpeak -t ENCFF933NSJ.bam -c ENCFF768XTH.bam -f BAM -g hs -q 0.05 \
  -n real --outdir /tmp > /tmp/macs2.log 2>&1
RC=$?
T1=$(date +%s)
kill $SPID 2>/dev/null; wait $SPID 2>/dev/null
WALL=$((T1-T0))
echo "macs2_rc	$RC"
echo "macs2_wall_s	$WALL"
test "$RC" = "0" || { tail -20 /tmp/macs2.log; echo "FAIL: macs2 exited $RC"; exit 1; }

awk -F'\t' -v wall="$WALL" '
  NR==1{cu0=$3}
  {if($2>mc)mc=$2; if($4>tk)tk=$4; cu=$3; n++}
  END{
    printf "peak_rss_mib\t%.0f\n", mc/1048576;
    printf "tmpfs_peak_mib\t%.0f\n", tk/1024;
    if(wall>0) printf "avg_cores\t%.2f\n", (cu-cu0)/(wall*1000000);
  }' /tmp/samples.tsv

# ---------------------------------------------------- the identity that makes n=1 defensible
# macs2 is deterministic, so the answer must not depend on which chip ran it. Asserting the
# peak count exactly is safe: it is an integer, far from any floating-point edge. The file
# sha256 is REPORTED rather than asserted -- it embeds q-values to five decimals, and if a
# generation ever changes those last digits that is a finding worth seeing, not a test
# failure to be absorbed by a tolerance.
PEAKS=$(grep -vc '^#' /tmp/real_peaks.narrowPeak 2>/dev/null)
echo "peaks	${PEAKS:-0}"
test "${PEAKS:-0}" = "50276" || { echo "FAIL: $PEAKS peaks, every other generation gave 50276"; exit 1; }

BADQ=$(awk -F'\t' '$0!~/^#/ && NF>=9 && $9 < 1.30103 {n++} END{print n+0}' /tmp/real_peaks.narrowPeak)
echo "peaks_below_q_cutoff	$BADQ"
test "$BADQ" = "0" || { echo "FAIL: $BADQ peaks below the -q 0.05 cutoff"; exit 1; }

SUM=$(grep -vc '^#' /tmp/real_summits.bed 2>/dev/null)
echo "summits	${SUM:-0}"
test "${SUM:-0}" = "${PEAKS:-0}" || { echo "FAIL: $SUM summits for $PEAKS peaks"; exit 1; }

sha256sum /tmp/real_peaks.narrowPeak | awk '{print "narrowPeak_sha256\t"$1}'
echo "narrowPeak_sha256_baseline	687b97a9bc39fed53555d7e566dd2d44f49cd926d1b91ebaea3ecc7c1638c54d"

{ echo -e "observable\tvalue"
  echo -e "macs2_wall_s\t$WALL"
  echo -e "peaks\t$PEAKS"
} > /tmp/rung.tsv

nproc | awk '{print "nproc\t"$1}'
uname -m | awk '{print "arch\t"$1}'
cat /proc/cpuinfo | grep -m1 "CPU part" | awk '{print "cpu_part\t"$4}'
echo "MACS2 RUNG OK"
