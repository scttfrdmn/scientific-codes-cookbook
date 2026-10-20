# One racon round over the whole run, on whichever chip this task landed on.
#   bash racon.sh <threads>
T=${1:?threads}
. /tmp/common.sh
O=/var/tmp/out/racon; mkdir -p $O
host_info $O/host.txt
racon --version > $O/racon-version.txt
T0=$(date +%s)
racon -t "$T" /tmp/ERR12813217.fastq.gz /tmp/ov.paf /tmp/draft.fa > $O/racon.fa 2> $O/racon.log &
P=$!
vmhwm_watch $P $O/.vmhwm &
wait $P; RC=$?
T1=$(date +%s)
NSEQ=$(grep -c '^>' $O/racon.fa)
BP=$(awk '!/^>/{n+=length($0)} END{printf "%.0f", n}' $O/racon.fa)
{
  echo "racon_rc $RC"
  echo "racon_wall_s $(( T1 - T0 ))"
  echo "racon_vmhwm_kib $(cat $O/.vmhwm 2>/dev/null)"
  echo "racon_seqs $NSEQ"
  echo "racon_bp $BP"
  echo "racon_sha256 $(sha256sum $O/racon.fa | awk '{print $1}')"
} | tee $O/checks.txt
rm -f $O/.vmhwm
[ "$RC" = 0 ] && [ "$NSEQ" -ge 1 ] || { echo "FAIL: racon"; tail -20 $O/racon.log; exit 1; }
echo "RACON OK"
