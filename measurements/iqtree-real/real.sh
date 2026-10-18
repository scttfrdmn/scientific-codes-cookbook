#!/usr/bin/env bash
# iqtree-real, task 1 of 2 (IQ-TREE image): the IQ-TREE run a gene-family study actually does --
# ModelFinder, ML search, 1000 ultrafast bootstraps -- then the same topology re-optimised under a
# fixed LG+G4 for task 2's cross-code check against RAxML-NG. Every check is an identity, not a band.
#
#   bash real.sh <alignment.fa> <threads> <expected taxa> <expected sites>
#
# Writes everything to /tmp/out/. Runs on the aarch.bio image and on bioconda's x86 one (BusyBox:
# no sha256sum --quiet, no date +%N, no python), so the glue is POSIX awk.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
ALN=$1 T=$2 NWANT=$3 SWANT=$4
O=/tmp/out
mkdir -p "$O"
cd /tmp
FAILS=0
row() { printf '%-34s %-22s %s\n' "$1" "$2" "$3" | tee -a "$O/checks.txt"; }
ok()  { row "$1" "$2" "ok   ($3)"; }
bad() { row "$1" "$2" "FAIL ($3)"; FAILS=$((FAILS+1)); }
check() { if [ "$1" = 1 ]; then ok "$2" "$3" "$4"; else bad "$2" "$3" "$4"; fi; }
num() { awk -v a="$1" -v b="$2" -v t="$3" 'BEGIN{d=a-b; if(d<0)d=-d; print (a!="" && b!="" && d<=t)?1:0}'; }

echo "== host ==" | tee "$O/host.txt"
{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  echo "iqtree3 $(command -v iqtree3)"
  (ldd "$(command -v iqtree3)" 2>&1 || echo "no ldd") | awk '{print "  " $1}'
} | tee -a "$O/host.txt"
awk '/^online_cpus/{c=$2} /^physical_cores/{p=$2} END{printf "threads_per_core %d\n", (p>0)?c/p:0}' "$O/host.txt" | tee -a "$O/host.txt"

# footprint every 5 s: anonymous memory (RSS, not page cache) and cumulative cgroup CPU
( while : ; do echo "$(date +%s) $(awk '/^anon /{print $2; exit}' /sys/fs/cgroup/memory.stat 2>/dev/null) $(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)" >> "$O/samples.txt"; sleep 5; done ) &
SPID=$!

echo "== IQ-TREE: ModelFinder + ML search + 1000 UFBoot, -T $T --seed 12345 =="
# -mset: the four general-purpose matrices fit for a nuclear/bacterial enzyme family. The default
# grid adds 24 more (mitochondrial, chloroplast, viral, clade-specific Q.*), each refitted with up
# to 10 FreeRate categories on 919 taxa -- most of a day of ModelFinder spent on matrices this
# family cannot be described by.
T0=$(date +%s)
iqtree3 -s "$ALN" -m MFP -mset LG,WAG,JTT,Q.pfam -B 1000 -T "$T" --seed 12345 --prefix real -redo > "$O/real.stdout" 2>&1
RC=$?
T1=$(date +%s)
echo "iqtree exit $RC in $((T1-T0)) s" | tee "$O/times.txt"

echo "== the same topology, fixed LG+G4, re-optimised (for the RAxML-NG cross-check) =="
iqtree3 -s "$ALN" -te real.treefile -m LG+G4 -T "$T" --seed 12345 -me 0.001 --prefix evlg -redo > "$O/evlg.stdout" 2>&1
RC2=$?
T2=$(date +%s)
echo "evaluate exit $RC2 in $((T2-T1)) s" | tee -a "$O/times.txt"
kill $SPID 2>/dev/null
for f in real.iqtree real.log real.treefile real.contree real.splits.nex real.mldist real.model.gz \
         evlg.iqtree evlg.log evlg.treefile; do
  [ -e "$f" ] && cp "$f" "$O/"
done
awk 'NR==1{t0=$1; u0=$3} $2>m{m=$2} {t=$1; u=$3} END{printf "peak_anon_gib %.2f\navg_busy_cores %.2f\n", m/1073741824, (t>t0)?(u-u0)/1e6/(t-t0):0}' "$O/samples.txt" | tee -a "$O/times.txt"
awk '/^Total wall-clock time used/{print "iqtree_reported_wall_s", $5}' real.log | tee -a "$O/times.txt"

echo "== checks ==" | tee "$O/checks.txt"
check "$([ "$RC" = 0 ] && [ "$RC2" = 0 ] && echo 1)" "both iqtree3 runs exit 0" "want 0 0" "$RC $RC2"
check "$(tail -n 3 real.log | grep -c '^Date and Time')" "real.log completion footer" "want 1" "footer"

N=$(grep -c '^>' "$ALN")
read -r NI SI <<<"$(awk '/^Input data:/{print $3, $6; exit}' real.iqtree)"
check "$([ "$N" = "$NWANT" ] && [ "$NI" = "$NWANT" ] && echo 1)" "taxa: alignment / IQ-TREE" "want $NWANT" "$N / $NI"
check "$([ "$SI" = "$SWANT" ] && echo 1)" "sites read by IQ-TREE" "want $SWANT" "$SI"

tips() { grep -oE '[(,][A-Za-z0-9_]+:' "$1" | wc -l | tr -d ' '; }
TT=$(tips real.treefile); TC=$(tips real.contree); TE=$(tips evlg.treefile)
check "$([ "$TT" = "$N" ] && [ "$TC" = "$N" ] && [ "$TE" = "$N" ] && echo 1)" "tips: ML / consensus / evaluated" "want $N each" "$TT / $TC / $TE"
BL=$(tr -cd ':' < real.treefile | wc -c | tr -d ' ')
check "$([ "$BL" = $((2*N-3)) ] && echo 1)" "ML tree branches (unrooted binary)" "want 2n-3 = $((2*N-3))" "$BL"
read -r NS SMAX SMIN <<<"$(grep -oE '\)[0-9.]+:' real.treefile | tr -d '):' | awk 'NR==1{mn=$1} {n++; if($1>mx)mx=$1; if($1<mn)mn=$1} END{print n+0, mx+0, mn+0}')"
check "$([ "$NS" = $((N-3)) ] && awk -v a="$SMAX" -v b="$SMIN" 'BEGIN{exit !(a<=100 && b>=0)}' && echo 1)" "UFBoot supports, one per split" "want n-3 = $((N-3)), 0..100" "$NS, $SMIN..$SMAX"

# information criteria are closed-form in logL, k and n, as IQ-TREE prints them (4 dp)
read -r L K AIC BIC <<<"$(awk '/^Log-likelihood of the tree:/{l=$5} /^Number of free parameters/{k=$NF} /^Akaike information criterion/{a=$NF} /^Bayesian information criterion/{b=$NF} END{print l, k, a, b}' real.iqtree)"
check "$(num "$AIC" "$(awk -v l="$L" -v k="$K" 'BEGIN{printf "%.4f", -2*l+2*k}')" 0.002)" "AIC == -2L + 2k" "4 dp" "L $L, k $K, AIC $AIC"
check "$(num "$BIC" "$(awk -v l="$L" -v k="$K" -v n="$SI" 'BEGIN{printf "%.4f", -2*l+k*log(n)}')" 0.002)" "BIC == -2L + k ln(sites)" "4 dp" "BIC $BIC"
BEST=$(awk '/^BEST SCORE FOUND/{print $NF}' real.log)
check "$(num "$BEST" "$L" 0.001)" "BEST SCORE FOUND == report logL" "3 vs 4 dp" "$BEST vs $L"

# ModelFinder: every tested model's BIC is closed-form in its own logL and df, the chosen model
# is the BIC minimum over all of them, and the search cannot end below the chosen model's
# likelihood on ModelFinder's starting tree.
MODEL=$(awk -F': ' '/^Best-fit model according to BIC/{print $2; exit}' real.iqtree)
# real.model.gz is ModelFinder's checkpoint: one "NAME: lnL df seconds" line per fitted model
gzip -dc real.model.gz | awk '$1 ~ /^[A-Za-z].*:$/ && NF==4 && $2 ~ /^-[0-9.]+$/ && $3 ~ /^[0-9]+$/ {sub(/:$/, "", $1); print $1, $2, $3}' > "$O/modelfinder.tsv"
NM=$(wc -l < "$O/modelfinder.tsv" | tr -d ' ')
read -r MINM MINB <<<"$(awk -v n="$SI" '{b=-2*$2+$3*log(n); if(NR==1||b<mb){mb=b; mm=$1}} END{printf "%s %.3f\n", mm, mb}' "$O/modelfinder.tsv")"
check "$([ "$NM" -gt 20 ] && [ "$MINM" = "$MODEL" ] && echo 1)" "chosen model is the BIC minimum" "over $NM fitted models" "$MINM ($MINB) vs chosen $MODEL"
# the report's ranked table: every printed AIC and BIC is closed-form in that row's logL and df
awk '/^List of models sorted by BIC/{f=1; next} f && /^Model /{g=1; next} g && NF==0{exit} g{print $1, $2, $3, $(NF-2)}' real.iqtree > "$O/bic-table.tsv"
read -r NROW NBAD FIRST <<<"$(awk -v n="$SI" 'NR==FNR{df[$1]=$3; next} {r++; if(r==1)first=$1; k=df[$1]; if(k==""){bad++; next} a=-2*$2+2*k; b=-2*$2+k*log(n); if((a-$3)^2>1e-4 || (b-$4)^2>1e-4) bad++} END{print r+0, bad+0, first}' "$O/modelfinder.tsv" "$O/bic-table.tsv")"
check "$([ "$NROW" -gt 20 ] && [ "$NBAD" = 0 ] && [ "$FIRST" = "$MODEL" ] && echo 1)" "every row: AIC, BIC closed-form" "3 dp, row 1 = chosen" "$NROW rows, $NBAD off, row 1 $FIRST"
MFL=$(awk -v m="$MODEL" '$1==m{print $2}' "$O/modelfinder.tsv")
check "$(awk -v a="$L" -v b="$MFL" 'BEGIN{print (a!="" && b!="" && a>=b-0.001)?1:0}')" "search >= ModelFinder start, same model" "final >= start" "$L >= $MFL"

EL=$(awk '/^Log-likelihood of the tree:/{print $5}' evlg.iqtree)
EM=$(awk -F': *' '/^Model of substitution/{print $2}' evlg.iqtree)
check "$([ "$EM" = "LG+G4" ] && [ -n "$EL" ] && echo 1)" "fixed-topology LG+G4 evaluated" "model LG+G4" "logL $EL"

{
  echo "model $MODEL"
  echo "logL $L"
  echo "lg_g4_fixed_topology_logL $EL"
  echo "models_tested $NM"
  grep -E 'correlation coefficient|did not converge' real.log | tail -n 2
  grep -E '^Total number of iterations' real.log
  grep -E '^Kernel:' real.log
} | tee "$O/summary.txt"

if [ "$FAILS" = 0 ]; then echo "IQTREE REAL OK" | tee -a "$O/checks.txt"; else echo "IQTREE REAL FAILED: $FAILS" | tee -a "$O/checks.txt"; exit 1; fi
