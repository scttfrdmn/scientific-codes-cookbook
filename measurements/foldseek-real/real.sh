#!/usr/bin/env bash
# foldseek-real: all-vs-all structural search over the whole AlphaFold DB v6 human proteome.
#
#   bash real.sh <threads> full|probe
#
# full : createdb on all 23,586 models, `foldseek search db db` (defaults: 3Di+AA, -e 10, plus -a for the backtraces convertalis needs,
#        --max-seqs 1000), convertalis with TM-score and LDDT columns, the self-hit checks, and
#        a 1-in-50 query subset re-searched against the same database at 2 threads, which must
#        reproduce the full run's rows for those queries exactly.
# probe: createdb plus the 1-in-50 subset at <threads> and at 2 threads (sizing + determinism).
#
# Runs on aarchbio's arm64 image and bioconda's x86 image (BusyBox userland), so the glue is
# POSIX sh tools only: awk keyed by query (23,586 keys), sort, sha256sum. Everything kept lands
# in /tmp/out, which the spec stages out; the work directory is on the root disk (/var/tmp).
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH TMPDIR=/tmp HOME=/tmp LC_ALL=C
T=$1
MODE=$2
O=/tmp/out
W=/var/tmp/fs
mkdir -p "$O" "$W"
TAR=/tmp/UP000005640_9606_HUMAN_v6.tar
NMODELS=23586
FMT="query,target,fident,alnlen,mismatch,gapopen,qstart,qend,tstart,tend,qlen,tlen,evalue,bits,alntmscore,qtmscore,ttmscore,lddt,prob"
FAIL=0
fail() { echo "FAIL: $*" | tee -a "$O/score.tsv"; FAIL=1; }
rec() { printf '%s\t%s\n' "$1" "$2" | tee -a "$O/score.tsv"; }
phase() { printf '%s\t%s\n' "$1" "$2" | tee -a "$O/phases.tsv"; }
: > "$O/score.tsv"; : > "$O/phases.tsv"
for l in createdb search convertalis nohit sub_search sub_search16 sub_conv sub_conv16; do : > "$O/$l.log"; done

{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}
    /^flags|^Features/{ n=split($2,a," "); s=""; for(i=1;i<=n;i++) if (a[i] ~ /^(asimd|sve|sve2|avx2|avx512f|avx512bw)$/) s=s" "a[i]; print "simd:"s }' /proc/cpuinfo | sort | uniq -c
  grep MemTotal /proc/meminfo
  echo "sort: $(readlink -f "$(command -v sort)")"
  foldseek version
} > "$O/host.txt" 2>&1
awk '/^online_cpus/{c=$2} /^physical_cores/{p=$2} END{printf "threads_per_core %d\n", (p>0)?c/p:0}' "$O/host.txt" >> "$O/host.txt"
cat "$O/host.txt"
rec tool_version "$(foldseek version)"
rec threads "$T"

cd "$W"
# ------------------------------------------------------------------ the database
t0=$(date +%s)
# The archive holds each model as .cif.gz and as .pdb.gz (23,586 of each): index the mmCIFs only.
foldseek createdb "$TAR" db --file-include '.*\.cif\.gz$' --threads "$T" > "$O/createdb.log" 2>&1
RC=$?; t1=$(date +%s); phase createdb_s $((t1 - t0))
[ "$RC" = 0 ] || { tail -30 "$O/createdb.log"; fail "createdb exit $RC"; exit 1; }
NDB=$(wc -l < db.index | tr -d ' ')
NNAMES=$(awk '{print $2}' db.lookup | sort -u | wc -l | tr -d ' ')
rec db_entries "$NDB"
rec db_distinct_names "$NNAMES"
[ "$NDB" = "$NMODELS" ] || fail "db holds $NDB entries, EBI publishes $NMODELS models"
[ "$NNAMES" = "$NMODELS" ] || fail "$NNAMES distinct entry names, expected $NMODELS"
# Residues: an entry's AA record is its sequence plus a newline and a NUL terminator, so the
# lengths in db.index are L+2; the 3Di and CA records must describe the same residue count.
awk '{s+=$3-2} END{printf "aa_residues\t%d\n", s}' db.index | tee -a "$O/score.tsv"
awk '{s+=$3-2} END{printf "3di_residues\t%d\n", s}' db_ss.index | tee -a "$O/score.tsv"
paste db.index db_ss.index | awk '$1!=$4 || $3!=$6 {n++} END{printf "aa_vs_3di_length_mismatch\t%d\n", n+0; exit (n>0)}' | tee -a "$O/score.tsv"
[ "${PIPESTATUS[1]}" = 0 ] || fail "an entry's 3Di string is not the length of its sequence"

# ---- every query must find itself, perfectly aligned. The queries ARE the database, so the
# self-alignment is the identity map: full length both sides, every residue identical, and a
# superposition that is exact (TM-score and LDDT 1). Nothing can exceed a TM-score of 1 normalised
# by the query's length, so "nothing beats itself" on qtmscore is algorithmic; ties are legal.
# Bits are reported, not asserted: the 3Di+AA score of the identity alignment is not provably maximal.
selfcheck() {   # <sorted tsv> <expected queries> <row prefix>
  awk -F'\t' -v N="$2" -v O="$O" -v P="$3" '
  { q=$1; seen[q]=1; n[q]++
    if (q == $2) {
      sc[q]++
      if (!($3 == 1 && $7 == 1 && $8 == $11 && $9 == 1 && $10 == $12 && $11 == $12 && $5 == 0 && $6 == 0)) badaln[q]=1
      if (!($16 == 1 && $17 == 1 && $15 == 1)) badtm[q]=$15" "$16" "$17
      if ($18 != 1) badlddt[q]=$18
      sbits[q]=$14; sq[q]=$16
    } else {
      if ($14+0 > ob[q]+0) ob[q]=$14
      if ($16+0 > otm[q]+0) otm[q]=$16
      if ($16 == 1) tie[q]++
    }
  }
  END {
    nq=0; noself=0; dup=0; ba=0; bt=0; bl=0; beat_tm=0; beat_bits=0; ties=0
    for (q in seen) {
      nq++
      if (!(q in sc)) { noself++; print q > (O "/" P "noself.txt"); continue }
      if (sc[q] > 1) dup++
      if (q in badaln) ba++
      if (q in badtm) { bt++; print q, badtm[q] > (O "/" P "badtm.txt") }
      if (q in badlddt) bl++
      if ((q in otm) && otm[q]+0 > sq[q]+0) beat_tm++
      if ((q in ob) && ob[q]+0 > sbits[q]+0) { beat_bits++; print q, sbits[q], ob[q] > (O "/" P "outscored_bits.txt") }
      if (q in tie) ties++
    }
    printf P "queries_with_hits\t%d\n", nq
    printf P "missing_self_hit\t%d\n", noself
    printf P "self_hit_repeated\t%d\n", dup
    printf P "self_alignment_not_identity\t%d\n", ba
    printf P "self_tmscore_not_1\t%d\n", bt
    printf P "self_lddt_not_1\t%d\n", bl
    printf P "queries_outscored_on_qtmscore\t%d\n", beat_tm
    printf P "obs_queries_tied_at_qtmscore_1\t%d\n", ties
    printf P "obs_queries_outscored_on_bits\t%d\n", beat_bits
    exit (nq != N || noself || dup || ba || bt || bl || beat_tm) ? 1 : 0
  }' "$1" | tee -a "$O/score.tsv"
  [ "${PIPESTATUS[0]}" = 0 ] || fail "the self-hit check on $1"
}

# ------------------------------------------------------------------ the 1-in-50 subset
# Chosen by NAME: createdb's key order is not reproducible (it differed between two builds), so a
# key-based 1-in-50 would pick different proteins on different boxes.
awk '{print $2"\t"$1}' db.lookup | sort | awk -F'\t' 'NR % 50 == 1 {print $2}' > sub.keys
rec sha_db_lookup_by_key "$(sha256sum < db.lookup | cut -c1-64)"
rec sha_db_names "$(cut -f2 db.lookup | sort | sha256sum | cut -c1-64)"
rec sha_subset_names "$(awk 'NR==FNR{k[$1]; next} ($1 in k){print $2}' sub.keys db.lookup | sort | sha256sum | cut -c1-64)"
NSUB=$(wc -l < sub.keys | tr -d ' ')
rec subset_queries "$NSUB"
for s in "" _ss _ca _h; do
  foldseek createsubdb sub.keys "db$s" "qsub$s" >> "$O/createdb.log" 2>&1 || fail "createsubdb db$s"
done
subsearch() {   # <threads> <tag>
  local th=$1 tag=$2 a b c
  a=$(date +%s)
  foldseek search qsub db "aln_sub$tag" "tmp_sub$tag" -a --threads "$th" > "$O/sub_search$tag.log" 2>&1 || fail "subset search ($th threads)"
  b=$(date +%s)
  foldseek convertalis qsub db "aln_sub$tag" "sub$tag.tsv" --format-output "$FMT" --threads "$th" > "$O/sub_conv$tag.log" 2>&1 || fail "subset convertalis ($th threads)"
  c=$(date +%s)
  phase "sub_search${tag}_t${th}_s" $((b - a))
  phase "sub_convertalis${tag}_t${th}_s" $((c - b))
  TMPDIR="$W" sort "sub$tag.tsv" > "sub$tag.sorted.tsv"
  [ -s "sub$tag.sorted.tsv" ] || fail "the subset search ($th threads) wrote no rows"
}
subsearch 2 ""

if [ "$MODE" = probe ]; then
  subsearch "$T" 16
  if cmp -s sub.sorted.tsv sub16.sorted.tsv; then rec subset_2_vs_${T}_threads identical
  else fail "the subset's rows differ between 2 and $T threads"; diff sub.sorted.tsv sub16.sorted.tsv | head -20; fi
  rec subset_rows "$(wc -l < sub.sorted.tsv | tr -d ' ')"
  selfcheck sub.sorted.tsv "$NSUB" "subset_"
  gzip -c sub.sorted.tsv > "$O/sub.sorted.tsv.gz"
else
  # ---------------------------------------------------------------- the whole proteome, all vs all
  t0=$(date +%s)
  foldseek search db db aln tmp -a --threads "$T" > "$O/search.log" 2>&1
  RC=$?; t1=$(date +%s); phase search_s $((t1 - t0))
  [ "$RC" = 0 ] || { tail -30 "$O/search.log"; fail "search exit $RC"; exit 1; }
  foldseek convertalis db db aln aln.tsv --format-output "$FMT" --threads "$T" > "$O/convertalis.log" 2>&1
  RC=$?; t2=$(date +%s); phase convertalis_s $((t2 - t1))
  [ "$RC" = 0 ] || { tail -30 "$O/convertalis.log"; fail "convertalis exit $RC"; exit 1; }
  TMPDIR="$W" sort aln.tsv > aln.sorted.tsv
  [ -s aln.sorted.tsv ] || fail "the search wrote no rows"
  t3=$(date +%s); phase sort_s $((t3 - t2))
  rec rows "$(wc -l < aln.sorted.tsv | tr -d ' ')"
  rm -f aln.tsv

  # ---- the one protein that finds nothing, not even itself. Ataxin-8 (Q156A1) is Met + 79 Gln: a
  # homopolymer, which composition-bias correction scores to nothing. The set of proteins with no
  # hit at all must be exactly that one -- a new member fails the run, and so does its disappearing --
  # and the diagnostics below record what finds it (reported, not asserted).
  cut -f2 db.lookup | sort > names.txt
  cut -f1 aln.sorted.tsv | uniq | sort -u > qnames.txt
  comm -23 names.txt qnames.txt > "$O/no_hits.txt"
  NNH=$(wc -l < "$O/no_hits.txt" | tr -d ' ')
  rec queries_without_any_hit "$NNH"
  if printf 'AF-Q156A1-F1-model_v6.cif.gz\n' | cmp -s - "$O/no_hits.txt"; then rec no_hit_set "exactly Q156A1 (ataxin-8, M+Q79)"
  else fail "the proteins with no hit are not exactly {Q156A1}"; head -5 "$O/no_hits.txt"; fi
  selfcheck aln.sorted.tsv $((NMODELS - NNH)) ""
  t4=$(date +%s); phase selfcheck_s $((t4 - t3))
  awk 'NR==FNR{k[$1]; next} ($2 in k){print $1}' "$O/no_hits.txt" db.lookup > nohit.keys
  for s in "" _ss _ca _h; do foldseek createsubdb nohit.keys "db$s" "qnh$s" >> "$O/nohit.log" 2>&1; done
  nohit() {   # <tag> <extra search flags...>
    local tag=$1; shift
    foldseek search qnh db "aln_nh_$tag" "tmp_nh_$tag" -a --threads "$T" "$@" >> "$O/nohit.log" 2>&1
    foldseek convertalis qnh db "aln_nh_$tag" "nh_$tag.tsv" --format-output "$FMT" >> "$O/nohit.log" 2>&1
    rec "nohit_${tag}_rows" "$(wc -l < "nh_$tag.tsv" | tr -d ' ')"
    awk -F'\t' -v t="$tag" '$1==$2 {printf "nohit_%s_self\t%s\n", t, $0}' "nh_$tag.tsv" | tee -a "$O/score.tsv"
  }
  nohit exhaustive_e_inf --exhaustive-search 1 -e inf
  nohit no_comp_bias_corr --comp-bias-corr 0
  nohit no_comp_bias_corr_e_inf --comp-bias-corr 0 -e inf

  # ---- invariance: a query's hits depend on the query and the database, not on which other
  # queries were in the batch or how many threads ran. The subset at 2 threads must reproduce the
  # full 16-thread run's rows for those queries, byte for byte.
  awk -F'\t' 'NR==FNR {k[$1]=1; next} ($1 in k)' <(cut -f1 sub.sorted.tsv | sort -u) aln.sorted.tsv > full_sub.tsv
  if cmp -s sub.sorted.tsv full_sub.tsv; then rec subset_2_threads_vs_full_run identical
  else fail "the subset's rows differ from the full run's"; diff sub.sorted.tsv full_sub.tsv | head -20; fi
  rec subset_rows "$(wc -l < sub.sorted.tsv | tr -d ' ')"

  # ---- digests for the cross-chip comparison: the hit set, the alignments (integer DP plus the
  # E-value derived from them), and the structure scores (floating point), separately.
  rec sha_sorted_tsv "$(sha256sum < aln.sorted.tsv | cut -c1-64)"
  rec sha_hits "$(cut -f1,2 aln.sorted.tsv | sha256sum | cut -c1-64)"
  rec sha_alignments "$(cut -f1-12 aln.sorted.tsv | sha256sum | cut -c1-64)"
  rec sha_evalue_bits "$(cut -f1,2,13,14 aln.sorted.tsv | sha256sum | cut -c1-64)"
  rec sha_structure_scores "$(cut -f1,2,15-19 aln.sorted.tsv | sha256sum | cut -c1-64)"
  gzip -c aln.sorted.tsv > "$O/aln.sorted.tsv.gz"
  t5=$(date +%s); phase digests_s $((t5 - t4))
fi
for f in /sys/fs/cgroup/memory.peak; do
  [ -r "$f" ] && awk '{printf "cgroup_peak_gib\t%.2f\n", $1/2^30}' "$f" | tee -a "$O/phases.tsv"
done
df -h /var/tmp /tmp | tee -a "$O/host.txt"
du -sh "$W" | tee -a "$O/host.txt"
[ "$FAIL" = 0 ] || { echo "FOLDSEEK REAL FAILED"; exit 1; }
echo "FOLDSEEK REAL OK"
