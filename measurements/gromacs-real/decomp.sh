set -eo pipefail
export PATH=/opt/conda/bin:$PATH
export TMPDIR=/tmp HOME=/tmp
cd /tmp
echo "== 16 cores, five rank x thread splits, all on ONE box =="
# One task so instance-to-instance variance cannot contaminate a decomposition comparison.
# A split that GROMACS refuses is recorded rather than killing the task: "this split does not
# work" is a result a reader needs, not a failure to hide.
: > decomp.tsv
printf "ranks\tomp\tns_per_day\twall_s\tranks_used\ttemp_K\tstatus\n" >> decomp.tsv
for split in "16 1" "8 2" "4 4" "2 8" "1 16"; do
  set -- $split; R=$1; O=$2
  echo "--- ${R} ranks x ${O} threads ---"
  T0=$(date +%s)
  if mpiexec -n "$R" --allow-run-as-root --oversubscribe \
       gmx_mpi mdrun -s benchMEM.tpr -deffnm d_${R}x${O} -ntomp "$O" -nb cpu -pin off \
       > out_${R}x${O}.txt 2>&1; then
    T1=$(date +%s)
    NS=$(awk '/^Performance:/{print $2; exit}' d_${R}x${O}.log)
    RU=$(awk '/Using .* MPI process/{print $2; exit}' d_${R}x${O}.log)
    TM=$(awk '/Conserved En\./ && /Temperature/{getline; print $4; exit}' d_${R}x${O}.log)
    printf "%s\t%s\t%s\t%s\t%s\t%s\tok\n" "$R" "$O" "$NS" "$(( T1 - T0 ))" "$RU" "$TM" >> decomp.tsv
  else
    T1=$(date +%s)
    WHY=$(grep -iE "Fatal error" -A2 out_${R}x${O}.txt | tr '\n' ' ' | cut -c1-120)
    printf "%s\t%s\t-\t%s\t-\t-\tFAILED: %s\n" "$R" "$O" "$(( T1 - T0 ))" "$WHY" >> decomp.tsv
  fi
done
column -t decomp.tsv > decomp.txt 2>/dev/null || cp decomp.tsv decomp.txt
cat decomp.txt
OK=$(awk 'NR>1 && $7=="ok"' decomp.tsv | wc -l | tr -d ' ')
BEST=$(awk 'NR>1 && $3!="-"{if($3+0>m){m=$3+0; b=$1"x"$2}} END{print b, m}' decomp.tsv)
echo "working_splits $OK" >> decomp.txt
echo "best $BEST" >> decomp.txt
tail -2 decomp.txt
test "$OK" -ge 3
echo 'DECOMP SWEEP DONE'
