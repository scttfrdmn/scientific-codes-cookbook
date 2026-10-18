#!/usr/bin/env bash
# Task 1 of 2 (openms image): every run of PXD083239 searched against the submitters' own
# UniProt/Swiss-Prot human FASTA plus OpenMS-generated decoys, target-decoy FDR per run, and
# a Percolator input (PIN) per run written by OpenMS's own writer for task 2.
#
# Staged and pinned rather than inline: a spawn task command rides in EC2 user data (16 KiB,
# spawn#1063). Shell + awk because the openms image ships no Python interpreter.
set +e
set -uo pipefail

THREADS="${THREADS:?set THREADS}"
mkdir -p out
SC=out/score.tsv
echo "observable	value" > "$SC"
fail () { echo "FAIL: $*"; exit 1; }
rec  () { printf '%s\t%s\n' "$1" "$2"; printf '%s\t%s\n' "$1" "$2" >> "$SC"; }

RUNS=$(cat runs.txt)
NRUNS=$(wc -w <<< "$RUNS" | tr -d ' ')
rec runs "$NRUNS"
test "$NRUNS" -ge 1 || fail "runs.txt lists no runs"

for t in DecoyDatabase SimpleSearchEngine PeptideIndexer FalseDiscoveryRate PercolatorAdapter; do
  command -v "$t" >/dev/null 2>&1 || fail "TOPP tool $t is absent from this image"
done

# ------------------------------------------------------------------ inline instrument
# 1 Hz cgroup-v2 sampler (measurements/sizing-ratio's fields). Fail-soft: unreadable cgroup
# reads as 0 and the wall clock and the science stay valid.
CG=/sys/fs/cgroup
sampler () {
  : > out/samples.tsv
  while : ; do
    mc=$(cat $CG/memory.current 2>/dev/null || echo 0)
    cu=$(awk '/^usage_usec/{print $2; exit}' $CG/cpu.stat 2>/dev/null || echo 0)
    tk=$(df -k /tmp 2>/dev/null | awk 'NR==2{print $3}')
    printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "${mc:-0}" "${cu:-0}" "${tk:-0}" >> out/samples.tsv
    sleep 1
  done
}
sampler & SPID=$!

# =============================================================== the decoy database
# Reverse decoys with the enzyme's cleavage sites kept (OpenMS's `reverse` with Trypsin
# reverses each tryptic peptide in place). So each decoy must be the SAME LENGTH and the SAME
# AMINO-ACID COMPOSITION as its target -- a full-sequence reversal is not what this tool does,
# and asserting one would fail a correct database. Composition equality is what makes the
# decoy search space mass-matched to the target one, which is the premise of the FDR.
echo "== decoys: DecoyDatabase -method reverse, DECOY_ prefix =="
DecoyDatabase -in target.fasta -out td.fasta -decoy_string DECOY_ \
  -decoy_string_position prefix -method reverse -enzyme Trypsin > out/decoy.log 2>&1
test "$?" = 0 || { tail -5 out/decoy.log; fail "DecoyDatabase failed"; }
DB=$(awk '
  function flush() { if (h != "") seq[h] = s }
  /^>/ { flush(); h = substr($1, 2); s = ""; next } { s = s $0 }
  END {
    flush()
    for (k in seq) {
      if (k ~ /^DECOY_/) { nd++; continue }
      nt++
      d = "DECOY_" k
      if (!(d in seq)) { unpaired++; continue }
      a = seq[k]; b = seq[d]
      if (length(a) != length(b)) { badlen++; continue }
      delete ca; delete cb
      for (i = 1; i <= length(a); i++) { ca[substr(a, i, 1)]++; cb[substr(b, i, 1)]++ }
      for (c in ca) if (ca[c] != cb[c]) { badcomp++; break }
      if (a == b) same++
    }
    printf "%d %d %d %d %d %d", nt, nd, unpaired + 0, badlen + 0, badcomp + 0, same + 0
  }' td.fasta)
read -r DBT DBD DBU DBL DBC DBS <<< "$DB"
rec db_targets "$DBT"; rec db_decoys "$DBD"
rec db_unpaired "$DBU"; rec db_length_mismatch "$DBL"; rec db_composition_mismatch "$DBC"
rec db_decoy_identical_to_target "$DBS"
NTGT_IN=$(grep -c '^>' target.fasta)
test "$DBT" = "$NTGT_IN" || fail "$DBT targets in td.fasta, $NTGT_IN in the staged FASTA"
test "$DBD" = "$DBT" -a "$DBU" = 0 -a "$DBL" = 0 -a "$DBC" = 0 \
  || fail "decoys are not 1:1 length- and composition-matched to their targets"

# ================================================================== per-run search
# Parameters are the submitters' Proteome Discoverer settings where this engine has them
# (PXD083239 dataProcessingProtocol): trypsin full, 2 missed cleavages, 50 ppm precursor,
# 0.06 Da fragment, fixed Carbamidomethyl (C), variable Oxidation (M) and protein N-term
# acetyl. 0.06 Da sits under SimpleSearchEngine's 0.1 Da fragment ceiling, which is what makes
# this dataset searchable by this engine at the submitters' own tolerance.
FalseDiscoveryRate -write_ini fdr.ini > /dev/null 2>&1
sed -i 's|\(name="add_decoy_peptides" *value="\)false|\1true|' fdr.ini
grep -q 'name="add_decoy_peptides" *value="true"' fdr.ini || fail "add_decoy_peptides still false"

idxml_hits () {   # score \t sequence \t charge \t accessions -- one line per PeptideHit
  awk '
    function attr(line, name,   re, v) {
      re = " " name "=\"[^\"]*\""
      if (match(line, re)) { v = substr(line, RSTART, RLENGTH); sub(" " name "=\"", "", v); sub(/"$/, "", v); return v }
      return ""
    }
    FNR == NR { if ($0 ~ /<ProteinHit /) acc[attr($0, "id")] = attr($0, "accession"); next }
    /<PeptideHit / {
      refs = attr($0, "protein_refs"); out = ""
      n = split(refs, r, " ")
      for (i = 1; i <= n; i++) out = out (i > 1 ? " " : "") (r[i] in acc ? acc[r[i]] : r[i])
      printf "%s\t%s\t%s\t%s\n", attr($0, "score"), attr($0, "sequence"), attr($0, "charge"), out
    }' "$1" "$1"
}

TS=0
for R in $RUNS; do
  echo "== $R =="
  NMS2=$(grep -c 'name="ms level" value="2"' "$R.mzML")
  rec "${R}_ms2_spectra" "$NMS2"
  T0=$(date +%s)
  SimpleSearchEngine -in "$R.mzML" -database td.fasta -out "$R.psm.idXML" \
    -Search:enzyme Trypsin -Search:peptide:missed_cleavages 2 \
    -Search:precursor:mass_tolerance 50 -Search:precursor:mass_tolerance_unit ppm \
    -Search:precursor:min_charge 2 -Search:precursor:max_charge 4 \
    -Search:fragment:mass_tolerance 0.06 -Search:fragment:mass_tolerance_unit Da \
    -Search:modifications:fixed "Carbamidomethyl (C)" \
    -Search:modifications:variable "Oxidation (M)" "Acetyl (Protein N-term)" \
    -threads "$THREADS" > "out/search_$R.log" 2>&1
  RC=$?
  T1=$(date +%s)
  TS=$((TS + T1 - T0))
  rec "${R}_search_wall_s" "$((T1 - T0))"
  test "$RC" = 0 || { tail -5 "out/search_$R.log"; fail "SimpleSearchEngine exited $RC on $R"; }

  PeptideIndexer -in "$R.psm.idXML" -fasta td.fasta -out "$R.idx.idXML" \
    -decoy_string DECOY_ -decoy_string_position prefix > "out/index_$R.log" 2>&1
  test "$?" = 0 || { tail -5 "out/index_$R.log"; fail "PeptideIndexer failed on $R"; }
  FalseDiscoveryRate -ini fdr.ini -in "$R.idx.idXML" -out "$R.fdr.idXML" \
    -PSM true -protein false > "out/fdr_$R.log" 2>&1
  test "$?" = 0 || { tail -5 "out/fdr_$R.log"; fail "FalseDiscoveryRate failed on $R"; }

  # -------- accounting: every PSM survives FDR, rows aligned, target + decoy = total
  idxml_hits "$R.idx.idXML" > raw.tsv
  idxml_hits "$R.fdr.idXML" > q.tsv
  NRAW=$(wc -l < raw.tsv | tr -d ' '); NQ=$(wc -l < q.tsv | tr -d ' ')
  rec "${R}_psms" "$NRAW"
  test "$NRAW" -gt 0 -a "$NQ" = "$NRAW" || fail "$R: $NRAW PSMs before FDR, $NQ after"
  MIS=$(paste <(cut -f2,3 raw.tsv) <(cut -f2,3 q.tsv) | awk -F'\t' '$1!=$3||$2!=$4{n++}END{print n+0}')
  test "$MIS" = 0 || fail "$R: $MIS rows misaligned between indexed and FDR output"
  # Decoy only when EVERY protein is a decoy: a peptide shared with a target is target evidence.
  paste <(cut -f1 raw.tsv) q.tsv | awk -F'\t' '{
      n = split($5, a, " "); dec = (n > 0)
      for (i = 1; i <= n; i++) if (a[i] !~ /^DECOY_/) dec = 0
      print $1 "\t" $2 "\t" $3 "\t" dec }' > psm.tsv            # raw_score q seq is_decoy
  NDEC=$(awk -F'\t' '$4==1' psm.tsv | wc -l | tr -d ' ')
  NTGT=$((NRAW - NDEC))
  rec "${R}_targets" "$NTGT"; rec "${R}_decoys" "$NDEC"
  test "$NDEC" -gt 0 || fail "$R: no decoy PSMs -- FDR would be meaningless"
  BADQ=$(awk -F'\t' '$2<0||$2>1{n++}END{print n+0}' psm.tsv)
  test "$BADQ" = 0 || fail "$R: $BADQ q-values outside [0,1]"

  # -------- q is single-valued and non-increasing in the raw score (targets)
  INC=$(awk -F'\t' '$4==0{ if ($1 in q) { if (q[$1] != $2) bad[$1] = 1 } else q[$1] = $2 }
        END { n = 0; for (k in bad) n++; print n }' psm.tsv)
  VIOL=$(awk -F'\t' '$4==0{ if (!($1 in q)) q[$1] = $2 } END { for (k in q) print k "\t" q[k] }' psm.tsv \
         | sort -g -r -k1,1 | awk -F'\t' '{ if (NR > 1 && $2 < prev - 1e-12) n++; prev = $2 } END { print n+0 }')
  rec "${R}_q_inconsistent_scores" "$INC"; rec "${R}_q_monotonicity_violations" "$VIOL"
  test "$INC" = 0 -a "$VIOL" = 0 || fail "$R: q is not a non-increasing function of the score"

  # -------- every reported q recomputed from decoy counts: running min of D/T (the recipe's
  # measured formula), cumulative at each DISTINCT score so ties share a threshold.
  sort -g -r -k1,1 psm.tsv | awk -F'\t' '
    { if (NR > 1 && $1 != prev) printf "%s\t%.17g\n", prev, (T ? D / T : 0)
      if ($4 == 1) D++; else T++; prev = $1 }
    END { printf "%s\t%.17g\n", prev, (T ? D / T : 0) }' \
    | sort -g -k1,1 | awk -F'\t' '{ if (NR == 1 || $2 < m) m = $2; printf "%s\t%.17g\n", $1, m }' > qrec.tsv
  CMP=$(awk -F'\t' 'FNR==NR{q[$1]=$2; next}
        $4==0{ n++; if (!($1 in q)) { miss++; next }; d = q[$1] - $2; if (d < 0) d = -d; if (d > w) w = d }
        END { printf "%d %d %.3e", n + 0, miss + 0, w + 0 }' qrec.tsv psm.tsv)
  read -r NC NMQ QW <<< "$CMP"
  rec "${R}_q_recomputed" "$NC"; rec "${R}_q_recomputed_max_diff" "$QW"
  test "$NMQ" = 0 -a "$NC" = "$NTGT" || fail "$R: $NMQ targets had no recomputed q"
  awk -v w="$QW" 'BEGIN{exit !(w <= 1e-12)}' || fail "$R: recomputed q differs by $QW"

  # -------- the operating point: targets accepted, and decoys above that score, at q<=0.01
  A=$(awk -F'\t' 'FNR==NR{ if ($4==0 && $2<=0.01) { if (!s++ || $1<lo) lo=$1 }; next }
       { if (s && $1>=lo) { if ($4==1) d++; else g++ } } END { printf "%d %d", g+0, d+0 }' psm.tsv psm.tsv)
  read -r ACC ADEC <<< "$A"
  rec "${R}_openms_targets_q0.01" "$ACC"; rec "${R}_openms_decoys_above_q0.01" "$ADEC"
  test "$ACC" -gt 0 || fail "$R: nothing accepted at q<=0.01"
  awk -v d="$ADEC" -v t="$ACC" 'BEGIN{exit !(d <= 0.01 * t)}' \
    || fail "$R: $ADEC decoys above the 1% threshold over $ACC targets is more than 1%"
  cp psm.tsv "out/psm_$R.tsv"

  # -------- the PIN, written by OpenMS's own Percolator writer
  # PercolatorAdapter writes -out_pin BEFORE it calls the percolator binary, but it validates
  # -percolator_executable first and percolator lives in a different image. So it is pointed at
  # /bin/true: the PIN is written, the no-op "percolator" exits 0, and the adapter then fails
  # looking for percolator's output. That nonzero exit is EXPECTED and is not the check; the
  # PIN is, and its labels must account for exactly the PSMs the indexer classified.
  PercolatorAdapter -in "$R.idx.idXML" -out pa.idXML -out_pin "out/pin_$R.tsv" \
    -percolator_executable /bin/true > "out/pinwrite_$R.log" 2>&1
  rec "${R}_pin_writer_rc_expected_nonzero" "$?"
  test -s "out/pin_$R.tsv" || { tail -5 "out/pinwrite_$R.log"; fail "$R: no PIN written"; }
  PL=$(awk -F'\t' 'NR==1{for(i=1;i<=NF;i++) if($i=="Label") c=i; next} {n[$c]++}
       END{printf "%d %d %d", n["1"]+0, n["-1"]+0, NR-1}' "out/pin_$R.tsv")
  read -r PT PD PN <<< "$PL"
  rec "${R}_pin_rows" "$PN"; rec "${R}_pin_targets" "$PT"; rec "${R}_pin_decoys" "$PD"
  test "$PN" = "$NRAW" -a "$PT" = "$NTGT" -a "$PD" = "$NDEC" \
    || fail "$R: PIN carries $PT/$PD target/decoy rows, the indexer classified $NTGT/$NDEC"
done
rec search_wall_s_all_runs "$TS"

kill $SPID 2>/dev/null; wait $SPID 2>/dev/null
awk -F'\t' 'NR==1{c0=$3; t0=$1} {if($2>m)m=$2; if($4>k)k=$4; c=$3; t=$1}
  END{printf "peak_mem_mib\t%.0f\ntmpfs_peak_mib\t%.0f\navg_cores\t%.2f\n", m/1048576, k/1024, (t>t0)?(c-c0)/((t-t0)*1e6):0}' \
  out/samples.tsv | tee -a "$SC"
cat $CG/memory.peak 2>/dev/null | awk '{printf "cgroup_memory_peak_mib\t%.0f\n", $1/1048576}' | tee -a "$SC"
echo "SEARCH OK" | tee -a "$SC"
