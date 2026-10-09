#!/usr/bin/env bash
# Check the two OpenMS legs: the committed reproduction, then the real run's FDR identities.
#
# WHY THIS IS SHELL AND NOT PYTHON. The aarchbio openms image ships no interpreter -- it is a
# single-tool bioconda image around a C++ toolkit, so `python3` is simply absent even with
# /opt/conda/bin on PATH. Everything here is awk.
#
# WHY IT READS idXML DIRECTLY. OpenMS writes one <PeptideHit .../> and one <ProteinHit .../>
# per line with named attributes, so awk can pull them out by NAME. That is more robust than
# routing through TextExporter and guessing which columns it emits, and it keeps the decoy
# signal where it is reliable: the _rev suffix on the protein accession.
#
# WHY IT IS A STAGED FILE. A spawn task command travels in EC2 user data, capped at 16,384
# bytes; inlining this pushed the task past the limit and RunInstances refused to launch.
# Staging is better anyway -- the script then sits under pins.sha256 like every other input.
set +e
set -uo pipefail

SCORE_TOL=1e-6   # leg A: relative agreement required against the committed scores
FLOOR=8          # leg B: minimum reference backbones recovered (justified below)

# ---------------------------------------------------------------------------------------------
# idxml_hits <file>  ->  score \t sequence \t charge \t accessions(space-joined)
#
# Two passes over the same file: build id -> accession from the ProteinHit lines, then resolve
# each PeptideHit's protein_refs through it. Attributes are matched with a LEADING SPACE so
# `score=` cannot also match `protein_score=` or any other suffixed attribute.
# ---------------------------------------------------------------------------------------------
idxml_hits () {
  awk '
    function attr(line, name,   re, v) {
      re = " " name "=\"[^\"]*\""
      if (match(line, re)) {
        v = substr(line, RSTART, RLENGTH)
        sub(" " name "=\"", "", v); sub(/"$/, "", v)
        return v
      }
      return ""
    }
    FNR == NR {
      if ($0 ~ /<ProteinHit /) acc[attr($0, "id")] = attr($0, "accession")
      next
    }
    /<PeptideHit / {
      refs = attr($0, "protein_refs"); out = ""
      n = split(refs, r, " ")
      for (i = 1; i <= n; i++) out = out (i > 1 ? " " : "") (r[i] in acc ? acc[r[i]] : r[i])
      printf "%s\t%s\t%s\t%s\n", attr($0, "score"), attr($0, "sequence"), attr($0, "charge"), out
    }
  ' "$1" "$1"
}

fail () { echo "FAIL: $*"; exit 1; }
rec  () { printf '%s\t%s\n' "$1" "$2"; printf '%s\t%s\n' "$1" "$2" >> score.tsv; }

echo "observable	value" > score.tsv

# =============================================================================================
# LEG A -- reproduce SimpleSearchEngine's own committed output, score and all.
# =============================================================================================
echo "== LEG A: against the committed SimpleSearchEngine_1_out.idXML =="
idxml_hits SimpleSearchEngine_1_out.idXML | sort > legA_exp.tsv
idxml_hits ref_out.idXML                  | sort > legA_got.tsv
NEXP=$(wc -l < legA_exp.tsv | tr -d ' ')
NGOT=$(wc -l < legA_got.tsv | tr -d ' ')
rec legA_committed_hits  "$NEXP"
rec legA_reproduced_hits "$NGOT"
test "$NEXP" -gt 0 || fail "parsed no hits from the committed output -- the parser is wrong"
test "$NGOT" = "$NEXP" || fail "reproduced $NGOT hits, the committed output has $NEXP"

# Sequence and charge are categorical, so they are compared as exact sets -- including the
# decoy hit (test2_rev) the fixture deliberately contains.
cut -f2,3 legA_exp.tsv | sort > legA_exp.key
cut -f2,3 legA_got.tsv | sort > legA_got.key
if ! diff -q legA_exp.key legA_got.key >/dev/null; then
  diff legA_exp.key legA_got.key | head -10
  fail "the reproduced (sequence, charge) set differs from the committed one"
fi
rec legA_sequence_charge_match "exact, all $NEXP"

# The score is a per-spectrum hyperscore over a handful of peaks -- not an iterative path, and
# far below the size at which OpenBLAS kernel dispatch can change a result. So it is compared
# tightly, and whether it came out bit-identical is REPORTED rather than assumed.
SCMP=$(awk -F'\t' -v tol="$SCORE_TOL" '
  FNR == NR { e[$2 "\t" $3] = $1; next }
  {
    k = $2 "\t" $3
    if (!(k in e)) { miss++; next }
    d = e[k] - $1; if (d < 0) d = -d
    r = (e[k] != 0) ? d / (e[k] < 0 ? -e[k] : e[k]) : d
    if (r > worst) worst = r
    if (e[k] "" == $1 "") bit++; else diff++
    printf "  score  committed %-21s reproduced %-21s rel %.3e  %s\n",
           e[k], $1, r, (e[k] "" == $1 "") ? "bit-identical" : "differs by the last bits" \
           > "/dev/stderr"
  }
  END { printf "%.3e %d %d %d", worst + 0, bit + 0, diff + 0, miss + 0 }
' legA_exp.tsv legA_got.tsv)
read -r WORST NBIT NDIFF NMISS <<< "$SCMP"
rec legA_max_relative_score_diff "$WORST"
rec legA_scores_bit_identical    "$NBIT of $NEXP"
test "$NMISS" -eq 0 || fail "$NMISS reproduced hits had no committed counterpart"
awk -v w="$WORST" -v t="$SCORE_TOL" 'BEGIN { exit !(w <= t) }' \
  || fail "a reproduced score is $WORST away from the committed value (tolerance $SCORE_TOL)"
rec legA_reproduction "committed output reproduced within $SCORE_TOL"

# =============================================================================================
# LEG B -- the real BSA run. Raw search scores come from indexed.idXML (BEFORE FDR) and
# q-values from fdr.idXML (AFTER), which is what makes the monotonicity check below real:
# it relates two independently produced columns rather than a column to itself.
# =============================================================================================
echo "== LEG B: the real run's FDR identities =="
idxml_hits indexed.idXML > legB_raw.tsv
idxml_hits fdr.idXML     > legB_q.tsv
NRAW=$(wc -l < legB_raw.tsv | tr -d ' ')
NQ=$(wc -l < legB_q.tsv | tr -d ' ')
rec legB_psms_before_fdr "$NRAW"
rec legB_psms_after_fdr  "$NQ"
test "$NRAW" -gt 0 || fail "no PSMs parsed from indexed.idXML"
test "$NQ" = "$NRAW" || fail "FDR changed the PSM count ($NRAW -> $NQ); the row join is invalid"

# Joining by row order is only sound if the rows ARE aligned, so that is asserted rather than
# assumed: the sequence and charge columns must agree row for row.
MISALIGNED=$(paste <(cut -f2,3 legB_raw.tsv) <(cut -f2,3 legB_q.tsv) \
  | awk -F'\t' '$1 != $3 || $2 != $4 { n++ } END { print n+0 }')
rec legB_misaligned_rows "$MISALIGNED"
test "$MISALIGNED" -eq 0 || fail "$MISALIGNED rows differ between indexed and FDR output"

paste <(cut -f1 legB_raw.tsv) legB_q.tsv > legB_join.tsv   # raw_score q seq charge accs

# A PSM is a decoy when EVERY protein it maps to carries the _rev suffix. Requiring all of them
# is deliberate: a peptide shared between a target and a decoy protein is not decoy evidence.
awk -F'\t' '{
    n = split($5, a, " "); dec = (n > 0)
    for (i = 1; i <= n; i++) if (a[i] !~ /_rev$/) dec = 0
    print $1 "\t" $2 "\t" $3 "\t" dec
  }' legB_join.tsv > legB_psm.tsv       # raw_score q seq is_decoy

NDEC=$(awk -F'\t' '$4 == 1' legB_psm.tsv | wc -l | tr -d ' ')
rec legB_psms_decoy  "$NDEC"
rec legB_psms_target "$((NRAW - NDEC))"
test "$NDEC" -gt 0 || fail "no decoy PSMs at all -- target-decoy FDR would be meaningless"

RANGE=$(awk -F'\t' '$2 < 0 || $2 > 1 { n++ } END { print n+0 }' legB_psm.tsv)
rec legB_qvalues_outside_0_1 "$RANGE"
test "$RANGE" -eq 0 || fail "$RANGE q-values outside [0,1]"

# -------- IDENTITY: q is a well-defined, non-increasing function of the raw search score ------
# Two separate claims, both exact. (a) Every PSM sharing a raw score must share a q-value --
# q depends only on where the threshold falls, so if it varies within a score it is not a
# q-value. (b) Walking down the score ranking, q can never decrease.
INCONSISTENT=$(awk -F'\t' '$4 == 0 {
    if ($1 in q) { if (q[$1] != $2) bad[$1] = 1 } else q[$1] = $2 }
  END { n = 0; for (k in bad) n++; print n }' legB_psm.tsv)
rec legB_scores_with_inconsistent_q "$INCONSISTENT"
test "$INCONSISTENT" -eq 0 || fail "$INCONSISTENT raw scores map to more than one q-value"

VIOL=$(awk -F'\t' '$4 == 0 { if (!($1 in q)) q[$1] = $2 }
  END { for (k in q) print k "\t" q[k] }' legB_psm.tsv \
  | sort -g -r -k1,1 \
  | awk -F'\t' '{ if (NR > 1 && $2 < prev - 1e-12) n++; prev = $2 } END { print n+0 }')
rec legB_q_monotonicity_violations "$VIOL"
test "$VIOL" -eq 0 || fail "q-values decrease as the raw score falls"
rec legB_identity_qvalue "on targets, q is single-valued and non-increasing in the score"

# ------- IDENTITY: every reported q-value is reproduced from the decoy counts, exactly ------
# This is the strong one. A target-decoy q-value is not a measured quantity -- it is a function
# of where each PSM sits in the score ranking and how many decoys outrank it. So it can be
# recomputed here from nothing but the scores and the _rev flags, and must come back the same.
#
# MEASURED, because the parameter description does not predict it: with conservative=true the
# docs name (D+1)/T, but the PSM-level q-values this tool emits reproduce as a running minimum
# of D/T -- at the top of the ranking D=0 and the reported q is 0, where (D+1)/T would be 1/13.
# The formula below is the one the output actually follows, not the one the flag advertises.
sort -g -r -k1,1 legB_psm.tsv > legB_sorted.tsv            # best-scoring first

# Cumulative target/decoy counts at each DISTINCT score, so tied PSMs share one threshold.
awk -F'\t' '
  { if (NR > 1 && $1 != prev) printf "%s\t%.17g\n", prev, (T ? D / T : 0)
    if ($4 == 1) D++; else T++
    prev = $1 }
  END { printf "%s\t%.17g\n", prev, (T ? D / T : 0) }' legB_sorted.tsv > fdr_by_score.tsv

# q is the running minimum from the MOST PERMISSIVE end: q(i) = min FDR over all thresholds at
# least as permissive as i. Ascending score order walks permissive-to-strict.
sort -g -k1,1 fdr_by_score.tsv \
  | awk -F'\t' '{ if (NR == 1 || $2 < m) m = $2; printf "%s\t%.17g\n", $1, m }' > q_by_score.tsv

CMP=$(awk -F'\t' '
  FNR == NR { q[$1] = $2; next }
  $4 == 0 {
    n++
    if (!($1 in q)) { miss++; next }
    d = q[$1] - $2; if (d < 0) d = -d
    if (d > worst) worst = d
    if (q[$1] "" == $2 "") exact++
  }
  END { printf "%d %d %d %.3e", n + 0, exact + 0, miss + 0, worst + 0 }' \
  q_by_score.tsv legB_psm.tsv)
read -r NCMP NEXACT NMISSQ QWORST <<< "$CMP"
rec legB_qvalues_compared       "$NCMP"
rec legB_qvalues_recomputed_max_diff "$QWORST"
test "$NMISSQ" -eq 0 || fail "$NMISSQ target PSMs had no recomputed q-value"
test "$NCMP" -gt 100 || fail "only $NCMP q-values compared -- too few for this to mean anything"
# Tolerance is float representation only: the recomputation divides in doubles while the file
# carries 17-digit decimals, so identical values can differ in the last bit. 1e-12 is ~4 orders
# of magnitude tighter than the smallest real difference any counting error could produce
# (one decoy among 513 targets moves D/T by 0.002).
awk -v w="$QWORST" 'BEGIN { exit !(w <= 1e-12) }' \
  || fail "recomputed q-values differ from the reported ones by $QWORST"
rec legB_identity_fdr "all $NCMP reported q-values reproduced from the decoy counts (max diff $QWORST)"

# The accepted sets, reported so the operating point is legible.
for T in 0.01 0.05; do
  R=$(awk -F'\t' -v t="$T" '
      FNR == NR { if ($4 == 0 && $2 <= t) { if (!seen++ || $1 < lo) lo = $1 }; next }
      { if (seen && $1 >= lo) { if ($4 == 1) d++; else g++ } }
      END { if (!seen) print "NA 0 0"; else printf "%.6g %d %d", lo, g + 0, d + 0 }
    ' legB_psm.tsv legB_psm.tsv)
  read -r SSTAR NTGTA NDECA <<< "$R"
  rec "legB_score_threshold_q$T"  "$SSTAR"
  rec "legB_targets_accepted_q$T" "$NTGTA"
  rec "legB_decoys_above_q$T"     "$NDECA"
  test "$NTGTA" -gt 0 || fail "nothing accepted at q<=$T -- the checks would be vacuous"
done

# =============================================================================================
# CROSS-CODE -- the same spectra and the same database, scored by two unrelated engines.
# =============================================================================================
# The expected answer is DERIVED FROM THE REFERENCE FILE, never written in here as a constant:
# a check that agrees with a literal in its own script is a restatement, not a comparison.
idxml_hits BSA1_OMSSA.idXML > refB.tsv
REFPSM=$(wc -l < refB.tsv | tr -d ' ')
rec reference_psms "$REFPSM"
test "$REFPSM" -gt 0 || fail "parsed no hits from the OMSSA reference"

# Accessions are not spelled identically across engines, so both sides are reduced to the
# UniProt-style mnemonic they share (P02769|ALBU_BOVIN -> ALBU_BOVIN).
top_protein () {   # $1 = tsv with accessions in the given column, $2 = column
  awk -F'\t' -v c="$2" '{
      n = split($c, a, " ")
      for (i = 1; i <= n; i++) {
        if (a[i] ~ /_rev$/ || a[i] == "") continue
        k = a[i]; sub(/.*\|/, "", k)
        cnt[k]++
      }
    } END { for (k in cnt) print cnt[k] "\t" k }' "$1" | sort -rn -k1,1
}
top_protein refB.tsv 4 > ref_prot.tsv
head -3 ref_prot.tsv | awk -F'\t' '{printf "  reference protein  %-22s %s PSMs\n", $2, $1}'
REFTOP=$(head -1 ref_prot.tsv | cut -f2)
REFTOPN=$(head -1 ref_prot.tsv | cut -f1)
rec reference_top_protein      "$REFTOP"
rec reference_top_protein_psms "$REFTOPN of $REFPSM"

# This run's confident target PSMs, at its own q<=0.01 operating point.
awk -F'\t' 'NR == FNR { if ($2 <= 0.01 && $4 == 0) keep[FNR] = 1; next }
            FNR in keep' legB_psm.tsv legB_join.tsv > legB_conf.tsv
NCONF=$(wc -l < legB_conf.tsv | tr -d ' ')
rec legB_confident_target_psms "$NCONF"
test "$NCONF" -gt 0 || fail "no confident target identifications at q<=0.01"

top_protein legB_conf.tsv 5 > run_prot.tsv
head -5 run_prot.tsv | awk -F'\t' '{printf "  this run protein   %-22s %s PSMs\n", $2, $1}'
RUNTOP=$(head -1 run_prot.tsv | cut -f2)
RUNTOPN=$(head -1 run_prot.tsv | cut -f1)
rec legB_top_protein      "$RUNTOP"
rec legB_top_protein_psms "$RUNTOPN of $NCONF"
# Categorical, no band: BSA1.mzML is a bovine serum albumin digest. Two engines agreeing on
# WHICH protein dominates is the claim; no threshold on any score or intensity.
test "$RUNTOP" = "$REFTOP" \
  || fail "this run's top protein is $RUNTOP, the reference's is $REFTOP"
rec legB_top_protein_agrees_with_reference "yes -- both engines put $REFTOP first"

# Peptide overlap is compared on the BARE backbone: the reference scored Carbamidomethyl as a
# VARIABLE modification while this run fixes it, so modification notation differs by
# configuration rather than by result. Stripping it compares the claim the two runs share.
awk -F'\t' '{ s = $3; gsub(/\([^)]*\)/, "", s); if (s != "") print s }' legB_conf.tsv \
  | sort -u > run_backbones.txt
sort -u ref_peptides.txt > ref_backbones.txt
comm -12 ref_backbones.txt run_backbones.txt > shared_backbones.txt
NREF=$(wc -l < ref_backbones.txt | tr -d ' ')
NRUN=$(wc -l < run_backbones.txt | tr -d ' ')
NSH=$(wc -l < shared_backbones.txt | tr -d ' ')
rec legB_reference_backbones "$NREF"
rec legB_confident_backbones "$NRUN"
rec legB_shared_backbones    "$NSH"
rec legB_reference_recovered "$(awk -v a="$NSH" -v b="$NREF" 'BEGIN{printf "%.4f", a/b}')"
head -12 shared_backbones.txt | sed 's/^/  shared peptide  /'

# The floor is justified by being orders of magnitude above chance, not by the observed value:
# these backbones are drawn from the tryptic peptides of 9,439 proteins, so re-finding even a
# third of a 23-peptide set by coincidence is not a possibility. It sits deliberately well
# below what two engines on identical spectra should manage, because the 0.3 -> 0.1 Da fragment
# tolerance gap -- the tool refuses anything wider -- costs real sensitivity.
test "$NSH" -ge "$FLOOR" \
  || fail "only $NSH of $NREF reference backbones recovered, floor is $FLOOR"
rec legB_cross_code "recovered $NSH of $NREF reference backbones (floor $FLOOR)"

echo "PROTEOMICS OK"
