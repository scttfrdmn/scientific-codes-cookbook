#!/usr/bin/env bash
# Task 2 of 2 (percolator image): rescore each run's PIN, written by task 1 and read back from
# S3, then compare against the submitters' own published identifications.
#
# Staged and pinned rather than inline (EC2 user data is 16 KiB, spawn#1063). awk only: this is
# a single-tool image around a C++ binary.
set +e
set -uo pipefail

mkdir -p out
SC=out/score-percolator.tsv
echo "observable	value" > "$SC"
fail () { echo "FAIL: $*"; exit 1; }
rec  () { printf '%s\t%s\n' "$1" "$2"; printf '%s\t%s\n' "$1" "$2" >> "$SC"; }

command -v percolator >/dev/null 2>&1 || fail "percolator absent from this image"
PV=$(percolator -h 2>&1 | grep -oiE 'percolator version [0-9a-z.,-]+' | head -1)
rec percolator_version "${PV:-unknown}"

RUNS=$(cat runs.txt)
SEED_RUN=$(awk '{print $1; exit}' runs.txt)

CG=/sys/fs/cgroup
sampler () {
  : > out/samples-percolator.tsv
  while : ; do
    mc=$(cat $CG/memory.current 2>/dev/null || echo 0)
    cu=$(awk '/^usage_usec/{print $2; exit}' $CG/cpu.stat 2>/dev/null || echo 0)
    printf '%s\t%s\t%s\n' "$(date +%s)" "${mc:-0}" "${cu:-0}" >> out/samples-percolator.tsv
    sleep 1
  done
}
sampler & SPID=$!

# percolator <seed> <run> <tag>: one training, fixed seed, PSM level, no protein inference
perc () {
  percolator -U -S "$1" -m "t_$2_$3.tab" -M "d_$2_$3.tab" "pin/pin_$2.tsv" \
    > "out/perc_$2_$3.stdout" 2> "out/perc_$2_$3.log"
  local rc=$?
  test "$rc" = 0 -a -s "t_$2_$3.tab" || { tail -10 "out/perc_$2_$3.log"; return 1; }
}

# backbone: strip flanks (K.PEPTIDE.R), bracketed and parenthesised mods, and fold I->L,
# because I and L are isobaric and no engine can tell them apart from an MS2 spectrum.
BB='function bb(p) { sub(/^[^.]*\./, "", p); sub(/\.[^.]*$/, "", p); gsub(/\[[^]]*\]/, "", p);
                     gsub(/\([^)]*\)/, "", p); gsub(/[^A-Z]/, "", p); gsub(/I/, "L", p); return p }'

TP=0
for R in $RUNS; do
  echo "== $R =="
  test -s "pin/pin_$R.tsv" || fail "no PIN for $R staged from task 1"
  PIN_T=$(awk -F'\t' 'NR==1{for(i=1;i<=NF;i++) if($i=="Label") c=i; next} $c=="1"{n++} END{print n+0}' "pin/pin_$R.tsv")
  PIN_D=$(awk -F'\t' 'NR==1{for(i=1;i<=NF;i++) if($i=="Label") c=i; next} $c=="-1"{n++} END{print n+0}' "pin/pin_$R.tsv")
  T0=$(date +%s)
  perc 1 "$R" a || fail "percolator failed on $R (seed 1)"
  T1=$(date +%s)
  TP=$((TP + T1 - T0))
  rec "${R}_percolator_wall_s" "$((T1 - T0))"
  perc 1 "$R" b || fail "percolator failed on $R (seed 1, repeat)"
  cp "t_${R}_a.tab" "d_${R}_a.tab" out/     # staged out even if a check below fails

  # -------- same seed, same bytes: the SVM's CV splits are seeded, so this is exact
  cmp -s "t_${R}_a.tab" "t_${R}_b.tab" && cmp -s "d_${R}_a.tab" "d_${R}_b.tab" \
    || fail "$R: two seed-1 runs differ"
  rec "${R}_seed1_sha256" "$(sha256sum "t_${R}_a.tab" | cut -c1-16)"

  # -------- conservation per class, then the partition by each row's own proteins
  NT=$(awk 'NR>1' "t_${R}_a.tab" | wc -l | tr -d ' ')
  ND=$(awk 'NR>1' "d_${R}_a.tab" | wc -l | tr -d ' ')
  rec "${R}_in_targets_decoys" "$PIN_T + $PIN_D"
  rec "${R}_out_targets_decoys" "$NT + $ND"
  test "$NT" = "$PIN_T" -a "$ND" = "$PIN_D" || fail "$R: $NT+$ND out, $PIN_T+$PIN_D in"
  # proteinIds is tab-separated and spills over columns 6..NF
  BAD=$(awk -F'\t' 'FNR==1{next} {
      nd = 0; nt = 0
      for (i = 6; i <= NF; i++) { if ($i == "") continue; nt++; if ($i ~ /^DECOY_/) nd++ }
      if (nt == 0) { np++; next }
      isd = (nd == nt); want = (FILENAME ~ /^d_/)
      if (isd != want) w++ }
    END { printf "%d %d", w + 0, np + 0 }' "t_${R}_a.tab" "d_${R}_a.tab")
  read -r WRONG NOPROT <<< "$BAD"
  rec "${R}_rows_in_wrong_class" "$WRONG"
  test "$WRONG" = 0 -a "$NOPROT" = 0 || fail "$R: $WRONG misfiled rows, $NOPROT with no protein"

  # -------- q and PEP in [0,1]; q non-increasing across score groups (ties are allowed any
  # internal order, because score prints to 6 significant figures)
  RG=$(awk -F'\t' 'FNR==1{next} $3<0||$3>1{q++} $4<0||$4>1{p++} END{printf "%d %d", q+0, p+0}' \
       "t_${R}_a.tab" "d_${R}_a.tab")
  read -r QOUT POUT <<< "$RG"
  test "$QOUT" = 0 -a "$POUT" = 0 || fail "$R: $QOUT q / $POUT PEP outside [0,1]"
  MV=$(awk -F'\t' 'FNR==1{next} { s = $2 + 0; if (!(s in lo) || $3 < lo[s]) lo[s] = $3; if (!(s in hi) || $3 > hi[s]) hi[s] = $3 }
       END { for (s in lo) print s "\t" lo[s] "\t" hi[s] }' "t_${R}_a.tab" \
       | sort -g -r -k1,1 | awk -F'\t' '{ if (NR > 1 && $3 < pmax - 1e-12) v++; if (NR == 1 || $3 > pmax) pmax = $3 } END { print v + 0 }')
  rec "${R}_q_monotonicity_violations" "$MV"
  test "$MV" = 0 || fail "$R: percolator q decreases as the score falls"

  ACC=$(awk -F'\t' 'FNR>1 && $3<=0.01{n++} END{print n+0}' "t_${R}_a.tab")
  rec "${R}_percolator_targets_q0.01" "$ACC"
  test "$ACC" -gt 0 || fail "$R: nothing accepted at q<=0.01"

  # -------- the submitters' published answer: Proteome Discoverer (SEQUEST + Percolator) PSMs
  # at their 1% FDR, extracted from their own .pdResult at staging (ref/ref_$R.tsv: spectrum,
  # peptide; a spectrum PD gives two equally ranked peptides has two rows, either one agrees).
  # The key is the mzML's own scan=N, which is this PIN's SpecId; stage.py mapped every
  # published PSM onto it by PD's own retention time, uniquely and with matching precursor
  # charge (PD's raw scan numbers are not in the mzML). Compared where BOTH engines are confident: on every
  # spectrum both accept, is it the same peptide? Count agreement is not asserted -- the
  # engines score differently, so how many each accepts is an observation.
  REF="ref/ref_$R.tsv"
  test -s "$REF" || fail "no published reference for $R"
  NREF=$(wc -l < "$REF" | tr -d ' ')
  rec "${R}_published_psms_1pct" "$NREF"
  awk -F'\t' 'FNR>1 && $3<=0.01 { s = $1; sub(/.*scan=/, "", s); sub(/[^0-9].*/, "", s); print s "\t" $5 }' \
    "t_${R}_a.tab" > "conf_$R.tsv"
  X=$(awk -F'\t' "$BB"'
      FNR==NR { ref[$1] = ref[$1] "|" bb($2) "|"; next }
      { if ($1 in ref) { both++; if (index(ref[$1], "|" bb($2) "|")) same++ } }
      END { printf "%d %d", both + 0, same + 0 }' "$REF" "conf_$R.tsv")
  read -r BOTH SAME <<< "$X"
  rec "${R}_scans_confident_in_both" "$BOTH"
  rec "${R}_same_peptide_on_shared_scans" "$SAME"
  rec "${R}_agreement" "$(awk -v a="$SAME" -v b="$BOTH" 'BEGIN{printf "%.4f", b ? a/b : 0}')"
  test "$BOTH" -gt 1000 || fail "$R: only $BOTH scans confident in both -- the scan join is wrong"
  # The floor is the two FDR budgets, not the observed value: each set is accepted at q<=0.01,
  # so on a spectrum both accept at most ~1% + ~1% of calls can be wrong, and a correct pair of
  # engines agrees on >= 98% of them. (Chimeric spectra, where two co-isolated peptides are both
  # right, are the legitimate way a pair could fall below it on other data.)
  awk -v a="$SAME" -v b="$BOTH" 'BEGIN{exit !(a >= 0.98 * b)}' \
    || fail "$R: the two engines agree on only $SAME of $BOTH shared confident scans"
done
rec percolator_wall_s_all_runs "$TP"

# -------- the seed is a real input: a different seed must be reported, not assumed equal
perc 7 "$SEED_RUN" c || fail "percolator failed on $SEED_RUN (seed 7)"
NQD=$(paste <(awk -F'\t' 'NR>1{print $1"\t"$3}' "t_${SEED_RUN}_a.tab" | sort) \
            <(awk -F'\t' 'NR>1{print $1"\t"$3}' "t_${SEED_RUN}_c.tab" | sort) \
      | awk -F'\t' '$2 != $4 {n++} END {print n+0}')
rec "${SEED_RUN}_seed7_targets_with_different_q" "$NQD"

kill $SPID 2>/dev/null; wait $SPID 2>/dev/null
awk -F'\t' 'NR==1{c0=$3; t0=$1} {if($2>m)m=$2; c=$3; t=$1}
  END{printf "peak_mem_mib\t%.0f\navg_cores\t%.2f\n", m/1048576, (t>t0)?(c-c0)/((t-t0)*1e6):0}' \
  out/samples-percolator.tsv | tee -a "$SC"
echo "RESCORE OK" | tee -a "$SC"
