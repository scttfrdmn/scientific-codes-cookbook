#!/usr/bin/env bash
# mash-real, task 01 (the mash image, on every chip): sketch every genome GTDB R232 places in the
# genus, all-vs-all distances, a representative screen, and every check. The images carry no python,
# so the checks are awk and sort, written to run on both mawk (aarch.bio) and BusyBox (bioconda x86).
#
#   bash run.sh <threads>          reads /tmp/genomes.tar /tmp/genomes.tsv /tmp/species.tsv
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
export LC_ALL=C TMPDIR=/var/tmp
T=${1:?threads}
K=21 S=10000
O=/tmp/out W=/var/tmp/m
mkdir -p "$O" "$W/g"
R="$O/results.txt" C="$O/checks.txt"
: > "$R"; : > "$C"
rec() { printf '%-24s %s\n' "$1" "$2" | tee -a "$R"; }
now() { awk '{print $1}' /proc/uptime; }
row() { printf '%-26s %-62s %s\n' "$1" "$2" "$3" | tee -a "$C"; }
check() { if [ "$1" = 1 ]; then row "$2" "$3" "ok   ($4)"; else row "$2" "$3" "FAIL ($4)"; fi; }
# <name> <cmd...>: run one timed step; wall from /proc/uptime, peak from the process's own VmHWM
# (read each second -- a high-water mark, so only growth in the final second can be missed)
step() {
  local n=$1; shift
  local t0 t1 pid hwm=0 h
  t0=$(now)
  "$@" 2> "$O/$n.log" &
  pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    h=$(awk '/^VmHWM/{print $2}' "/proc/$pid/status" 2>/dev/null)
    [ -n "$h" ] && [ "$h" -gt "$hwm" ] && hwm=$h
    sleep 1
  done
  wait "$pid"; local rc=$?
  t1=$(now)
  rec "${n}_s" "$(awk -v a="$t0" -v b="$t1" 'BEGIN{printf "%.2f", b-a}')"
  rec "${n}_vmhwm_kib" "$hwm"
  rec "${n}_exit" "$rc"
  return $rc
}

MASH=$(command -v mash)
rec arch "$(uname -m)"
rec mash_version "$(mash --version 2>&1)"
# ELF e_machine of the binary == this host's architecture: native, not emulated
EM=$(od -An -tu2 -j18 -N2 "$MASH" | tr -d ' ')
case "$(uname -m)" in aarch64) WANT=183 ;; x86_64) WANT=62 ;; *) WANT=x ;; esac
check "$([ "$EM" = "$WANT" ] && echo 1)" "binary architecture" "ELF e_machine == uname -m" "$EM on $(uname -m)"
rec cpu_model "$(awk -F': ' '/^model name/{print $2; exit} /^CPU part/{p=$2} END{if(p!="") print "CPU part " p}' /proc/cpuinfo | awk 'NR==1')"
rec nproc "$(nproc)"
rec threads_per_core "$(awk -F, '{n=0; for(i=1;i<=NF;i++){ if (split($i,r,"-")==2) n+=r[2]-r[1]+1; else n++ } print n}' /sys/devices/system/cpu/cpu0/topology/thread_siblings_list)"
rec threads "$T"

cd "$W/g"
tar -xf /tmp/genomes.tar
ls | sort > ../files.txt
N=$(awk 'END{print NR}' ../files.txt)
NT=$(awk 'END{print NR}' /tmp/genomes.tsv)
cut -f1 /tmp/genomes.tsv | sort | cmp -s - ../files.txt; rc=$?
check "$([ "$rc" = 0 ] && [ "$N" = "$NT" ] && echo 1)" "genomes" "the tar's files == genomes.tsv, one each" "$N"
awk -F'\t' '$5=="t"{print $1}' /tmp/genomes.tsv | sort > ../reps.txt
NR_=$(awk 'END{print NR}' ../reps.txt)
NSP=$(awk 'END{print NR}' /tmp/species.tsv)
check "$([ "$NR_" = "$NSP" ] && echo 1)" "representatives" "== GTDB species clusters" "$NR_"

# --- the workload: sketch, all-vs-all, and a GTDB-Tk-style screen of every genome against the reps
step sketch  mash sketch -p "$T" -k $K -s $S -l ../files.txt -o "$W/all"
step sketch2 mash sketch -p "$T" -k $K -s $S -l ../files.txt -o "$W/all2"
step reps_p1 mash sketch -p 1   -k $K -s $S -l ../reps.txt  -o "$W/reps1"
step dist    sh -c "exec mash dist -p $T $W/all.msh $W/all.msh > $W/dist.tsv"
step screen  sh -c "exec mash dist -p $T $W/reps1.msh $W/all.msh > $W/screen.tsv"
cd "$W"
# the sketch itself is the claim: deterministic in k, s and seed, so bytes, not just distances
cmp -s all.msh all2.msh; rc=$?
check "$([ "$rc" = 0 ] && echo 1)" "sketch reproducible" "two sketches of all genomes, byte-identical" "$(sha256sum all.msh | cut -c1-16)"
mash info -t all.msh | awk -F'\t' -v s=$S 'NR>1 { n++; if ($1 != s) bad++ } END { print n+0, bad+0 }' > info.txt
read -r NI BADI < info.txt
check "$([ "$NI" = "$N" ] && [ "$BADI" = 0 ] && echo 1)" "sketches" "one per genome, each full ($S hashes)" "$NI sketches, $BADI short"

# --- identities over all N*N rows, one streaming pass
#   self: d == 0 with S/S shared.   closed form: d == -ln(2j/(1+j))/k, j = shared/S, at print precision
#   (6 significant digits); 0 shared hashes prints d == 1.
awk -F'\t' -v k=$K -v s=$S '
  { n++
    split($5, h, "/"); x=h[1]+0; y=h[2]+0
    if (y != s) badtot++
    if ($1 == $2) { self++; if ($3 != 0 || x != s) badself++; next }
    if (x == 0) { if ($3 != 1) badcf++; zero++; next }
    # two different genomes with identical sketches: d must print exactly 0 (and c below would be 0)
    if (x == y) { if ($3 != 0) badcf++; same++; next }
    j = x / y; c = -log(2*j/(1+j)) / k
    e = $3 - c; if (e < 0) e = -e
    if (e > 5.001e-6 * c + 1e-12) badcf++
    if (e/c > maxrel) maxrel = e/c }
  END { printf "%d %d %d %d %d %d %.3g %d\n", n, self, badself+0, badcf+0, badtot+0, zero+0, maxrel, same+0 }' dist.tsv > ident.txt
read -r ROWS SELF BADSELF BADCF BADTOT ZERO MAXREL SAME < ident.txt
check "$([ "$ROWS" = $((N*N)) ] && echo 1)" "rows" "exactly N*N ordered pairs" "$ROWS"
check "$([ "$SELF" = "$N" ] && [ "$BADSELF" = 0 ] && echo 1)" "self distance" "exactly 0, all $S hashes shared, every genome" "$SELF, $BADSELF not"
check "$([ "$BADCF" = 0 ] && [ "$BADTOT" = 0 ] && echo 1)" "closed form" "d == -ln(2j/(1+j))/k at print precision, every pair" "$BADCF off, max rel $MAXREL"
rec zero_shared_pairs "$ZERO"
rec identical_sketch_pairs "$SAME (ordered, distinct genomes)"

# symmetry: d(a,b) and d(b,a) are computed separately; each unordered pair must appear twice, identical
awk -F'\t' 'NR==FNR { id[$1]=NR; next } $1 != $2 { a=id[$1]; b=id[$2]
  if (a < b) print a "\t" b "\t" $3 "\t" $5; else print b "\t" a "\t" $3 "\t" $5 }' files.txt dist.tsv \
  | sort -k1,1n -k2,2n -k3,3 -k4,4 | uniq -c | awk '{ if ($1 == 2) ok++; else bad++ } END { print ok+0, bad+0 }' > sym.txt
read -r SYMOK SYMBAD < sym.txt
check "$([ "$SYMOK" = $((N*(N-1)/2)) ] && [ "$SYMBAD" = 0 ] && echo 1)" "symmetry" "d(a,b) == d(b,a) and same shared count, every pair" "$SYMOK pairs, $SYMBAD asymmetric"

# batch + thread invariance: the reps sketched alone at 1 thread, screened against all, reproduce
# exactly the rows of the all-vs-all whose reference is a rep (same order: query-major)
awk -F'\t' 'NR==FNR { r[$1]=1; next } ($1 in r)' reps.txt dist.tsv | cmp -s - screen.tsv; rc=$?
check "$([ "$rc" = 0 ] && echo 1)" "batch/thread invariance" "reps at -p 1 vs all == those rows of the -p $T all-vs-all" "$(awk 'END{print NR}' screen.tsv) rows"

# --- GTDB: nearest representative by mash == the representative GTDB assigned (skani ANI, AF)
# genomes.tsv: file accession species rep_accession is_rep length genome_size
awk -F'\t' '
  FILENAME == ARGV[1] { rep[$1] = $4 ".fna.gz"; isrep[$1] = ($5 == "t"); sp[$1] = $3; next }
  $1 != $2 && isrep[$1] {
    q = $2; d = $3 + 0
    if (!(q in best) || d < bd[q]) { best[q] = $1; bd[q] = d; tie[q] = 0 }
    else if (d == bd[q]) tie[q]++
    if ($1 == rep[q]) dr[q] = d }
  END { for (q in best) if (!isrep[q])
          printf "%s\t%s\t%s\t%s\t%.7g\t%.7g\t%d\n", q, sp[q], rep[q], best[q], bd[q], (q in dr) ? dr[q] : 1, tie[q] }' \
  /tmp/genomes.tsv screen.tsv | sort > "$O/nearest.tsv"
# also each rep's nearest OTHER rep, the spacing GTDB's species boundaries are drawn at
awk -F'\t' 'NR==FNR { if ($5=="t") isrep[$1]=1; next } $1 != $2 && isrep[$2] {
  q=$2; if (!(q in bd) || $3+0 < bd[q]) { bd[q]=$3+0; best[q]=$1 } }
  END { for (q in bd) printf "%s\t%s\t%.7g\n", q, best[q], bd[q] }' /tmp/genomes.tsv screen.tsv | sort > "$O/rep-spacing.tsv"
awk -F'\t' '{ n++; if ($3 == $4) ok++; if ($7 > 0) ties++; if ($6 <= 0.05) in95++; if ($6 > mx) mx = $6 }
  END { printf "%d %d %d %d %.6f %.6f\n", n, ok, ties+0, in95, ok/n, mx }' "$O/nearest.tsv" > nn.txt
read -r NQ NOK NTIE N95 FRAC MAXD < nn.txt
check "$([ "$NQ" = $((N - NR_)) ] && echo 1)" "screened genomes" "every non-representative, once" "$NQ"
# floor fixed before the run: a backstop for a broken sketch or screen, not a fit to this value
check "$(awk -v f="$FRAC" 'BEGIN{print (f ~ /^[0-9.]+$/ && f+0 >= 0.95) ? 1 : 0}')" "nearest rep == GTDB's" ">= 0.95 of non-representative genomes" "$NOK of $NQ = $FRAC"
rec nearest_ties "$NTIE"
rec member_d_to_rep_le_0.05 "$N95 of $NQ"
rec member_d_to_rep_max "$MAXD"
awk -F'\t' '{ if ($3 < 0.05) c++ } END { print c+0 }' "$O/rep-spacing.tsv" > rs.txt
rec reps_with_another_rep_within_0.05 "$(cat rs.txt) of $NR_"

# --- published ANI: per species, mean 100(1-d) of members to their rep vs GTDB's mean intra-species
# ANI (skani), ranked across species (mash's distance mixes in alignment fraction, so rank, not value)
awk -F'\t' '
  FILENAME == ARGV[1] { sp = $1; ani[sp] = $5; aniMin[sp] = $6; af[sp] = $7; ncl[sp] = $9; next }
  { r = substr($3, 1, length($3) - 7); s = "RS_" r; if (!(s in ani)) s = "GB_" r
    sum[s] += 100 * (1 - $6); cnt[s]++; if (!(s in mn) || 100*(1-$6) < mn[s]) mn[s] = 100*(1-$6) }
  END { for (s in cnt) printf "%s\t%d\t%.4f\t%s\t%.4f\t%s\t%s\n", s, ncl[s], sum[s]/cnt[s], ani[s], mn[s], aniMin[s], af[s] }' \
  /tmp/species.tsv "$O/nearest.tsv" | sort > "$O/species-ani.tsv"
# Spearman with average ranks for ties: rank each column, then Pearson on the ranks
spearman() {  # <file> <col x> <col y>  -> rho n
  awk -F'\t' -v cx="$2" '{ print $cx "\t" NR }' "$1" | sort -k1,1g | awk -F'\t' '{ v[NR]=$1; i[NR]=$2 } END {
    a = 1; while (a <= NR) { b = a; while (b < NR && v[b+1] == v[a]) b++
      for (t = a; t <= b; t++) print i[t] "\t" (a + b) / 2; a = b + 1 } }' | sort -k1,1n > rx.tmp
  awk -F'\t' -v cy="$3" '{ print $cy "\t" NR }' "$1" | sort -k1,1g | awk -F'\t' '{ v[NR]=$1; i[NR]=$2 } END {
    a = 1; while (a <= NR) { b = a; while (b < NR && v[b+1] == v[a]) b++
      for (t = a; t <= b; t++) print i[t] "\t" (a + b) / 2; a = b + 1 } }' | sort -k1,1n > ry.tmp
  paste rx.tmp ry.tmp | awk -F'\t' '{ x=$2; y=$4; n++; sx+=x; sy+=y; sxx+=x*x; syy+=y*y; sxy+=x*y }
    END { q = (n*sxx - sx*sx) * (n*syy - sy*sy); if (q > 0) printf "%.4f %d\n", (n*sxy - sx*sy) / sqrt(q), n; else printf "nan %d\n", n }'
}
awk -F'\t' '$2 >= 3' "$O/species-ani.tsv" > sp3.tsv
read -r RHO NRHO <<< "$(spearman sp3.tsv 3 4)"
check "$(awk -v r="$RHO" 'BEGIN{print (r ~ /^-?[0-9.]+$/ && r+0 >= 0.6) ? 1 : 0}')" "tracks GTDB's mean ANI" "Spearman >= 0.6, species with >= 3 genomes" "rho $RHO over $NRHO species"
read -r RHOMIN NRHOMIN <<< "$(spearman sp3.tsv 5 6)"
rec spearman_min_ani "$RHOMIN over $NRHOMIN species (observation)"
awk -F'\t' '{ e = $3 - $4; if (e < 0) e = -e; print e }' sp3.tsv | sort -g | awk '{ v[NR]=$1 } END { printf "%.3f\n", v[int((NR+1)/2)] }' > med.txt
rec median_abs_ani_diff "$(cat med.txt) ANI points (mash 1-d vs skani)"

cp all.msh "$O/all.msh"
# --- fingerprints: compared across chips by collect.sh
sha256sum all.msh reps1.msh dist.tsv screen.tsv "$O/nearest.tsv" "$O/rep-spacing.tsv" "$O/species-ani.tsv" \
  | sed 's|/tmp/out/||' > "$O/fingerprints.txt"
cat "$O/fingerprints.txt"
rec memory_peak_bytes "$(cat /sys/fs/cgroup/memory.peak 2>/dev/null || echo na)"
NF_=$(grep -c FAIL "$C")
rec failures "$NF_"
[ "$NF_" = 0 ] || { echo "CHECKS FAILED"; exit 1; }
echo "ALL CHECKS PASSED"
