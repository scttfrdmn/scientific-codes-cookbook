#!/usr/bin/env bash
# MrBayes 3.2.8 to convergence on the recipe's own 114-taxon alignment, the way the analysis is run:
# mb-mpi, 2 runs x 4 chains on 8 ranks, LG+G4, stopping itself when ASDSF < 0.01.
#
#   bash mcmc.sh <ngen cap> <require convergence: 1|0>
#
# Runs in the mrbayes image (aarch.bio on arm64, bioconda's BusyBox image on x86): POSIX glue only.
# Writes /tmp/out/. Every check that fails sets F=1; the task exits F after all evidence is written.
set +e
set -uo pipefail
NGEN=${1:?ngen cap}
REQ=${2:?require convergence 1/0}
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
export TMPDIR=/tmp HOME=/tmp
# Open MPI 4 in a container: no ssh, /dev/shm is 64 MB, the x86 image runs as root, and an 8i
# 2xlarge is 4 cores x 2 threads, so 8 ranks need --oversubscribe.
export OMPI_MCA_plm_rsh_agent=false OMPI_MCA_btl_vader_single_copy_mechanism=none
export OMPI_MCA_btl_vader_backing_directory=/tmp
export OMPI_ALLOW_RUN_AS_ROOT=1 OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1
MPI="mpirun --oversubscribe --bind-to none -np 8"
cd /tmp
O=/tmp/out; mkdir -p "$O"
F=0
fail() { echo "FAIL: $*" | tee -a "$O/checks.txt"; F=1; }
ok()   { echo "ok    $*" | tee -a "$O/checks.txt"; }
rec()  { printf '%s\t%s\n' "$1" "$2" | tee -a "$O/score.tsv"; }
SAMPLEFREQ=500 DIAGNFREQ=10000 STOPVAL=0.01

echo "== the box =="
rec arch "$(uname -m)"
rec vcpus "$(grep -c ^processor /proc/cpuinfo)"
rec cpu0_thread_siblings "$(cat /sys/devices/system/cpu/cpu0/topology/thread_siblings_list 2>/dev/null)"
rec cpu_model "$(grep -m1 -E '^(model name|CPU part)' /proc/cpuinfo | cut -d: -f2 | sed 's/^ *//')"

echo "== FASTA -> NEXUS, the recipe's own converter =="
awk -f fasta2nexus.awk aln.fa > data.nex || fail "conversion"
NTAX=$(awk '/^    [A-Za-z0-9]/{c++} END{print c+0}' data.nex)
NCHAR=$(awk '/dimensions/{for(i=1;i<=NF;i++) if ($i ~ /^nchar=/) {sub(/nchar=/,"",$i); sub(/;/,"",$i); print $i}}' data.nex)
rec nexus_taxa "$NTAX"; rec nexus_sites "$NCHAR"
[ "$NTAX" = 114 ] && [ "$NCHAR" = 477 ] && ok "114 taxa x 477 sites" || fail "$NTAX x $NCHAR, expected 114 x 477"
awk '/^>/{print substr($0,2)}' aln.fa | sort > fa.labels
awk '/^    [A-Za-z0-9]/{print $1}' data.nex | sort > nx.labels
cmp -s fa.labels nx.labels && ok "FASTA and NEXUS label sets identical" || fail "label sets differ"

echo "== fixed point: MrBayes' likelihood at each ML tree, its branch lengths and its alpha =="
# startvals puts chain 1 exactly at the given tree and branch lengths; the initial log likelihood
# MrBayes prints is then the likelihood of that point, with nothing sampled.
fxeval() {  # $1 tag  $2 newick file  $3 alpha
  { echo "#NEXUS"; echo "begin trees;"; printf "  tree t = "; tr -d '\n' < "$2"; echo; echo "end;"; } > "fx_$1.tre"
  cat > "fx_$1.nex" <<EOF
#NEXUS
begin mrbayes;
  set autoclose=yes nowarn=yes seed=1 swapseed=1 usebeagle=no;
  execute data.nex;
  execute fx_$1.tre;
  lset rates=gamma ngammacat=4;
  prset aamodelpr=fixed(lg) shapepr=fixed($3);
  startvals tau=t v=t;
  mcmcp ngen=1 nruns=1 nchains=1 samplefreq=1 printfreq=1 diagnfreq=1 file=fx_$1;
  mcmc;
  quit;
end;
EOF
  mb "fx_$1.nex" > "$O/fx_$1.out" 2>&1 < /dev/null
  awk '/Initial log likelihoods/{getline; print $4}' "$O/fx_$1.out"
}
A_IQ=$(awk '/^Gamma shape alpha:/{print $4}' iqout.iqtree)
A_RX=$(sed -n 's/.*G4m{\([0-9.]*\)}.*/\1/p' rx.raxml.bestModel)
rec alpha_iqtree "$A_IQ"; rec alpha_raxml "$A_RX"
L_IQ=$(fxeval iq iqout.treefile "$A_IQ"); L_RX=$(fxeval rx rx.raxml.bestTree "$A_RX")
rec mb_lnl_at_iqtree_point "$L_IQ"; rec mb_lnl_at_raxml_point "$L_RX"
grep -q 'Fixed(0.92' "$O/fx_iq.out" && [ -n "$L_IQ" ] && [ -n "$L_RX" ] && ok "MrBayes evaluated both ML points" \
  || fail "fixed-point evaluation did not run"
grep -m1 'likelihood calculator' "$O/fx_iq.out" | sed 's/^ *//' | { read -r l; rec likelihood_calculator "$l"; }

echo "== the MCMC: LG+G4, 2 runs x 4 chains on 8 MPI ranks, stops at ASDSF < $STOPVAL =="
cat > run.nex <<EOF
#NEXUS
begin mrbayes;
  set autoclose=yes nowarn=yes seed=20261019 swapseed=20261019 usebeagle=no;
  execute data.nex;
  lset rates=gamma ngammacat=4;
  prset aamodelpr=fixed(lg);
  mcmcp ngen=$NGEN samplefreq=$SAMPLEFREQ nchains=4 nruns=2 printfreq=$DIAGNFREQ diagnfreq=$DIAGNFREQ
        relburnin=yes burninfrac=0.25 stoprule=yes stopval=$STOPVAL checkfreq=100000 file=mb;
  mcmc;
  sumt relburnin=yes burninfrac=0.25 conformat=simple;
  sump relburnin=yes burninfrac=0.25;
  quit;
end;
EOF
S=$(date +%s)
$MPI mb-mpi run.nex > "$O/mb.out" 2>&1 < /dev/null
RC=$?
E=$(date +%s)
rec mcmc_wall_s "$((E-S))"
rec cgroup_memory_peak_bytes "$(cat /sys/fs/cgroup/memory.peak 2>/dev/null)"
[ "$RC" = 0 ] && ok "mb-mpi exit 0" || { fail "mb-mpi exited $RC"; tail -30 "$O/mb.out"; }

grep -q '(Parallel version)' "$O/mb.out" && grep -q '(8 processors available)' "$O/mb.out" \
  && ok "parallel build, 8 MPI ranks (MrBayes' own banner)" || fail "not an 8-rank parallel run"
grep -q 'Setting Aamodelpr to Fixed(LG)' "$O/mb.out" && ok "model Fixed(LG), 4 gamma categories" || fail "not LG"
grep -q 'Analysis completed in' "$O/mb.out" && ok "completion sentinel" || fail "no completion sentinel"
grep -m1 'likelihood calculator' "$O/mb.out" | sed 's/^ *//'

echo "== convergence: the run's own stop rule =="
grep 'Average standard deviation of split frequencies:' "$O/mb.out" | awk '{print $NF}' > asdsf.trace
ASDSF=$(tail -1 asdsf.trace)
rec asdsf_final "$ASDSF"
rec asdsf_checks "$(wc -l < asdsf.trace | tr -d ' ')"
STOPPED=no; grep -q 'convergence diagnostic hit stop value' "$O/mb.out" && STOPPED=yes
rec stopped_by_stoprule "$STOPPED"
if [ "$REQ" = 1 ]; then
  [ "$STOPPED" = yes ] && awk -v a="$ASDSF" -v s="$STOPVAL" 'BEGIN{exit !(a+0 < s+0 && a != "")}' \
    && ok "converged: ASDSF $ASDSF < $STOPVAL, the run stopped itself" \
    || fail "not converged within $NGEN generations (ASDSF $ASDSF)"
fi

echo "== exact sample arithmetic =="
G1=$(awk 'NF && $1 ~ /^[0-9]+$/ {g=$1} END{print g}' mb.run1.p)
G2=$(awk 'NF && $1 ~ /^[0-9]+$/ {g=$1} END{print g}' mb.run2.p)
rec generations "$G1"
[ "$G1" = "$G2" ] && ok "both runs stopped at generation $G1" || fail "runs stopped at $G1 and $G2"
[ "$REQ" = 1 ] && { [ $((G1 % DIAGNFREQ)) = 0 ] && ok "stop generation is a multiple of diagnfreq" || fail "stop gen $G1 not a multiple of $DIAGNFREQ"; }
WANT=$((G1 / SAMPLEFREQ + 1))
for n in 1 2; do
  P=$(awk 'NF && $1 ~ /^[0-9]+$/' mb.run$n.p | wc -l | tr -d ' ')
  T=$(grep -c '^   tree gen\.' mb.run$n.t)
  rec "samples_run$n" "$P"
  [ "$P" = "$WANT" ] && [ "$T" = "$WANT" ] && ok "run$n: $P .p rows and $T trees == $G1/$SAMPLEFREQ + 1" \
    || fail "run$n: $P .p rows, $T trees, expected $WANT"
done

echo "== conservation: topology posteriors sum to 1 =="
LASTP=$(grep -o 'P = [0-9.]*' mb.trprobs | tail -1 | awk '{print $3}')
NTOPO=$(grep -c '^   tree tree_' mb.trprobs)
rec trprobs_cumulative_P "$LASTP"; rec distinct_topologies "$NTOPO"
[ "$LASTP" = 1.000 ] && ok "cumulative topology probability 1.000 over $NTOPO topologies" || fail "cumulative P $LASTP"

echo "== nothing sampled beats the maximum =="
# The cold chain samples the posterior; no sampled (tree, lengths, alpha) can have a higher
# likelihood than the ML optimum under the same model, evaluated by the same calculator.
MAXL=$(awk 'NF && $1 ~ /^[0-9]+$/ {if (m=="" || $2+0>m+0) m=$2} END{print m}' mb.run1.p mb.run2.p)
rec max_sampled_lnl "$MAXL"
awk -v m="$MAXL" -v a="$L_IQ" -v b="$L_RX" 'BEGIN{c=(a+0>b+0)?a:b; printf "%.6f\n", c-m; exit !(m+0 <= c+0)}' > gap.txt \
  && ok "max sampled lnL $MAXL <= ML point $(cat gap.txt) below" || fail "a sample beats the ML point: $MAXL vs $L_IQ / $L_RX"
rec ml_minus_max_sampled "$(cat gap.txt)"

echo "== diagnostics, reported =="
grep -E 'Average PSRF for parameter values|Maximum PSRF for parameter values' "$O/mb.out" | tail -2 | sed 's/^ *//'
awk '/^      (TL|alpha) /{print}' "$O/mb.out" | tail -2 | tee "$O/sump.tsv"
grep -E 'Average standard deviation of split frequencies =|Maximum standard deviation of split' "$O/mb.out" | sed 's/^ *//' | tee -a "$O/sumt-asdsf.txt"
# sumt recomputes ASDSF from the written .t files with the same 25% burn-in, so it must equal the
# last diagnostic the sampler printed: a .t file truncated or out of step with the chain cannot.
SUMT_ASDSF=$(awk '/Average standard deviation of split frequencies =/{print $NF}' "$O/sumt-asdsf.txt" | tail -1)
rec asdsf_sumt "$SUMT_ASDSF"
[ -n "$SUMT_ASDSF" ] && [ "$SUMT_ASDSF" = "$ASDSF" ] && ok "sumt's ASDSF from the .t files == the sampler's last diagnostic ($ASDSF)" \
  || fail "sumt ASDSF $SUMT_ASDSF != sampler's $ASDSF"
awk '{printf "%s_mean\t%s\n%s_minESS\t%s\n%s_avgESS\t%s\n%s_PSRF\t%s\n", $1,$2,$1,$7,$1,$8,$1,$9}' "$O/sump.tsv" >> "$O/score.tsv"

echo "== consensus -> Newick, labels asserted =="
awk -f nexus2newick.awk mb.con.tre > mb.consensus.nwk || fail "newick conversion"
head -1 mb.consensus.nwk | tr ',()' '\n\n\n' | sed 's/:.*//' | grep -E '^[A-Za-z0-9_]+$' | sort -u > nwk.labels
cmp -s fa.labels nwk.labels && ok "consensus labels == the alignment's 114" || fail "consensus labels differ"

echo "== determinism: a short 8-rank chain twice with the same seed =="
for t in d1 d2; do
  sed -e "s/file=mb;/file=$t;/" -e "s/ngen=$NGEN/ngen=2000/" -e "s/printfreq=$DIAGNFREQ diagnfreq=$DIAGNFREQ/printfreq=1000 diagnfreq=1000/" \
      -e '/sumt /d' -e '/sump /d' run.nex > $t.nex
  $MPI mb-mpi $t.nex > $t.out 2>&1 < /dev/null
done
DET=yes
for n in 1 2; do for k in p t; do
  [ "$(grep -v '^\[ID' d1.run$n.$k | md5sum)" = "$(grep -v '^\[ID' d2.run$n.$k | md5sum)" ] || DET=no
done; done
[ -s d1.run1.p ] || DET=no
rec determinism "$DET"
[ "$DET" = yes ] && ok "same seed, same 8 ranks -> identical .p and .t" || fail "a fixed seed did not reproduce"

cp mb.consensus.nwk mb.con.tre mb.trprobs mb.tstat mb.pstat mb.vstat mb.lstat asdsf.trace "$O/" 2>/dev/null
for n in 1 2; do grep -v '^\[ID' mb.run$n.p > "$O/mb.run$n.p"; done
for n in 1 2; do md5sum mb.run$n.t | awk -v n=$n '{print "run" n ".t md5 (with ID header)", $1}'; grep -v '^\[ID' mb.run$n.t | md5sum | awk -v n=$n '{print "run" n ".t", $1}'; done | tee "$O/tfile-md5.txt"
gzip -c mb.run1.t > "$O/mb.run1.t.gz"; gzip -c mb.run2.t > "$O/mb.run2.t.gz"
rec result "$([ $F = 0 ] && echo PASS || echo FAIL)"
exit $F
