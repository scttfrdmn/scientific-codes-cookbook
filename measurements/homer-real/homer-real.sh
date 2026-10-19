#!/usr/bin/env bash
# homer-real: HOMER on a whole ENCODE CTCF ChIP-seq peak set (GM12878, IDR conservative) against
# all of GRCh38 -- the recipe's checks at the scale a ChIP-seq user runs them.
#
#   bash homer-real.sh <threads> [chrom]
#
# With [chrom], only that chromosome's peaks are used (a wiring canary; the genome is still whole).
#
# Three findMotifsGenome.pl runs, all at summit +-100 bp, all on the same staged bytes:
#   genome    what a user runs: known + de novo, against HOMER's GC/CpG-matched random genomic
#             background (a fresh draw every run, so its background numbers are a path)
#   offset    the control: the same regions moved 100 kb, known motifs, random background
#   vsoffset  the peaks against the offset set as an explicit -bg: no random draw anywhere, so
#             the whole knownResults.txt is a function of fixed input (a fixed point across chips)
# Runs on aarch.bio's arm64 image and bioconda's x86 one (BusyBox userland): the glue is POSIX
# sh and awk. Needs /tmp/GRCh38_no_alt_analysis_set.fa(.fai), /tmp/ENCFF796WRU.bed.gz,
# /tmp/encode.md5. Writes /tmp/out/.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH TMPDIR=/tmp HOME=/tmp
T=${1:?threads}
CHR=${2:-}
O=/tmp/out
FA=/tmp/GRCh38_no_alt_analysis_set.fa
mkdir -p "$O" /tmp/preparsed && chmod 1777 /tmp/preparsed
cd /tmp
FAILS=0
row() { printf '%-58s %-40s %s\n' "$1" "$2" "$3" | tee -a "$O/checks.txt"; }
check() { if [ "$1" = 1 ]; then row "$2" "$3" "ok   ($4)"; else row "$2" "$3" "FAIL ($4)"; FAILS=$((FAILS+1)); fi; }

# bioconda-recipes#70249 (see recipes/homer): point the loader at the env's own lib/.
PFX=$(dirname "$(dirname "$(readlink -f "$(command -v perl)")")")
export LD_LIBRARY_PATH="$PFX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
homer2 2>&1 | grep -q "homer2 <command>" \
  || { echo "FAIL: homer2 does not load ($(homer2 2>&1 | sed -n 1p))" | tee -a "$O/checks.txt"; exit 1; }

{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "cpu_model $(awk -F': ' '/^model name/{print $2; exit}' /proc/cpuinfo)"
  echo "cpu_part $(awk -F': ' '/^CPU part/{print $2; exit}' /proc/cpuinfo)"
  echo "cpu0_thread_siblings $(cat /sys/devices/system/cpu/cpu0/topology/thread_siblings_list 2>/dev/null)"
  command -v lscpu >/dev/null 2>&1 && lscpu | grep -iE '^(Thread|Core|Socket|Model name|NUMA node\(s\))'
  echo "mem_total_kb $(awk '/^MemTotal/{print $2}' /proc/meminfo)"
  echo "homer_prefix $PFX"
  echo "homer_config $(ls -d "$PFX"/share/homer* 2>/dev/null | sed -n 1p)"
} > "$O/host.txt" 2>&1
cat "$O/host.txt"

md5sum -c encode.md5 > "$O/md5.txt" 2>&1; R=$?
check "$([ "$R" = 0 ] && echo 1)" "ENCFF796WRU.bed.gz == ENCODE's published md5" "md5sum -c" "$(tr '\n' ' ' < "$O/md5.txt")"
[ "$R" = 0 ] || exit 1
sha256sum ENCFF796WRU.bed.gz | tee "$O/staged.sha256"

gzip -dc ENCFF796WRU.bed.gz > peaks.all 2>/dev/null || zcat ENCFF796WRU.bed.gz > peaks.all
if [ -n "$CHR" ]; then awk -v c="$CHR" '$1 == c' peaks.all > peaks.narrowPeak; MIN=100; else cp peaks.all peaks.narrowPeak; MIN=10000; fi
NROW=$(wc -l < peaks.narrowPeak | tr -d ' ')
# 200 bp around each summit, as in recipes/homer. ENCODE's IDR file repeats a summit when two
# replicate peaks merge onto it, so de-duplicate by chrom:summit before counting targets.
awk -v FAI="$FA.fai" -v DUP="$O/peaks.dups" -v EDGE="$O/peaks.edge" 'BEGIN{OFS="\t"; while ((getline l < FAI) > 0) { split(l, f, "\t"); L[f[1]] = f[2] } }
  { s = $2 + $10; k = $1 ":" s
    if (!($1 in L) || s - 100 < 0 || s + 100 > L[$1]) { print k > EDGE; next }
    if (k in seen) { print k > DUP; next }
    seen[k] = 1; n++; print "p" n, $1, s - 100, s + 100, "+" }' peaks.narrowPeak > peaks.txt
touch "$O/peaks.dups" "$O/peaks.edge"
# The control: each region moved 100 kb downstream, or upstream where that would leave the
# chromosome, so it has the same count, size and chromosome as the peaks and only the binding moves.
awk -v FAI="$FA.fai" 'BEGIN{OFS="\t"; while ((getline l < FAI) > 0) { split(l, f, "\t"); L[f[1]] = f[2] } }
  { d = ($4 + 100000 <= L[$2]) ? 100000 : -100000; print $1 "_off", $2, $3 + d, $4 + d, "+" }' peaks.txt > offset.txt
NP=$(wc -l < peaks.txt | tr -d ' '); NO=$(wc -l < offset.txt | tr -d ' ')
ND=$(wc -l < "$O/peaks.dups" | tr -d ' '); NE=$(wc -l < "$O/peaks.edge" | tr -d ' ')
echo "rows $NROW unique $NP duplicate_summits $ND off_chrom_or_edge $NE offset $NO" | tee "$O/counts.txt"
check "$([ "$NROW" = $((NP + ND + NE)) ] && [ "$NO" = "$NP" ] && [ "$NP" -gt "$MIN" ] && echo 1)" \
  "rows == unique + duplicate + edge; offset == unique" "accounting" "$NROW = $NP + $ND + $NE; offset $NO"
sha256sum peaks.txt offset.txt | tee -a "$O/fingerprints.txt"
# HOMER drops a sequence that is >70% N. Count those ourselves, from homerTools' own extraction of
# the same regions, so HOMER's target count can be asserted exactly rather than as a band.
masked() { homerTools extract "$1" "$FA" -fa 2> /dev/null \
  | awk '/^>/ { if (s != "") { t++; x = s; m += (gsub(/[Nn]/, "", x) / length(s) > 0.7) }; s = ""; next } { s = s $0 }
         END { if (s != "") { t++; x = s; m += (gsub(/[Nn]/, "", x) / length(s) > 0.7) }; print m + 0, t + 0 }'; }
set -- $(masked peaks.txt); MP=$1; XP=$2
set -- $(masked offset.txt); MO=$1; XO=$2
echo "n_masked peaks $MP of $XP, offset $MO of $XO" | tee -a "$O/counts.txt"
check "$([ "$XP" = "$NP" ] && [ "$XO" = "$NO" ] && echo 1)" "homerTools extract returns every region" "peaks, offset" "$XP of $NP, $XO of $NO"

# Peak memory: the container cgroup's own memory.peak at the end (anon + what the container wrote
# to tmpfs), plus a 1 s sampler of anon memory and /tmp use as a profile. CPU from cpu.stat.
( while : ; do echo "$(date +%s) $(awk '/^anon /{print $2; exit}' /sys/fs/cgroup/memory.stat 2>/dev/null) $(df -k /tmp | awk 'NR==2{print $3}')" >> "$O/samples.txt"; sleep 1; done ) &
SPID=$!
usec() { awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null; }
fmg() {  # fmg <name> <regions> <extra args...>
  local name=$1 reg=$2 t0 t1 u0 u1 rc; shift 2
  t0=$(date +%s); u0=$(usec)
  findMotifsGenome.pl "$reg" "$FA" "$name" -size given -p "$T" -preparsedDir /tmp/preparsed "$@" > "$O/$name.log" 2>&1
  rc=$?
  t1=$(date +%s); u1=$(usec)
  echo "$name rc $rc wall_s $((t1 - t0)) cpu_s $(( (u1 - u0) / 1000000 ))" | tee -a "$O/times.txt"
  check "$([ "$rc" = 0 ] && [ "$(wc -l < "$name/knownResults.txt" 2>/dev/null || echo 0)" -gt 100 ] && echo 1)" \
    "findMotifsGenome.pl $name" "exit 0, knownResults written" "rc $rc"
  cp "$name/knownResults.txt" "$O/$name.knownResults.txt" 2>/dev/null
}
fmg genome   peaks.txt
fmg offset   offset.txt -nomotif
fmg vsoffset peaks.txt  -nomotif -bg offset.txt
kill "$SPID" 2>/dev/null
echo "memory_peak_bytes $(cat /sys/fs/cgroup/memory.peak 2>/dev/null)" | tee -a "$O/times.txt"
awk '{ if ($2 > a) a = $2; if ($3 > t) t = $3 } END { printf "anon_peak_mib %d\ntmp_peak_mib %d\n", a / 1048576, t / 1024 }' "$O/samples.txt" | tee -a "$O/times.txt"
mkdir -p "$O/genome-denovo"
cp genome/homerResults/motif[0-9].motif genome/homerResults/motif[0-9][0-9].motif genome/homerMotifs.all.motifs "$O/genome-denovo/" 2>/dev/null

# The checks. knownResults.txt columns: name, consensus, p, logp, q, #targets(of N), %targets,
# #bg(of M), %bg. Rank is the row order HOMER writes (by p).
known() {  # known <file>: rank, name, target count, target %, bg %, enrichment of the vertebrate CTCF row; then the top row
  awk -F'\t' 'function p(x) { gsub(/%/, "", x); return x + 0 }
    NR == 1 { match($6, /of [0-9]+/); nt = substr($6, RSTART + 3, RLENGTH - 3); match($8, /of [0-9]+/); nb = substr($8, RSTART + 3, RLENGTH - 3); next }
    NR == 2 { top = $1 }
    !c && $1 ~ /^CTCF\(Zf\)\/CD4/ { c = 1; printf "ctcf_rank %d\nctcf_targets %s\nctcf_target_pct %s\nctcf_bg_pct %s\nctcf_enrichment %.2f\n", NR - 1, $6, p($7), p($9), p($7) / (p($9) > 0 ? p($9) : 1e-9) }
    END { printf "n_targets %s\nn_background %s\nn_motifs %d\ntop %s\n", nt, nb, NR - 1, top }' "$1"
}
for r in genome offset vsoffset; do known "$O/$r.knownResults.txt" | sed "s/^/$r /"; done | tee "$O/known.txt"
g() { awk -v k="$1 $2" '$1 " " $2 == k { $1 = ""; $2 = ""; sub(/^ +/, ""); print; exit }' "$O/known.txt"; }
DN=$(sed -n 1p genome/homerResults/motif1.motif 2>/dev/null)
DN_CONS=$(printf '%s' "$DN" | awk -F'\t' '{sub(/^>/, "", $1); print $1}')
DN_BEST=$(printf '%s' "$DN" | awk -F'\t' '{n = $2; sub(/.*BestGuess:/, "", n); print n}')
DN_LOGP=$(printf '%s' "$DN" | awk -F'\t' '{print $4}')
echo "denovo_motif1 $DN_CONS logp $DN_LOGP best_guess $DN_BEST" | tee -a "$O/known.txt"

# The targets HOMER kept: the sum of the "# Targets" column of the GC-bin table in its log. (The
# "(of N)" in the knownResults header is a sum of autonormalisation weights, not a sequence count:
# measured 44,221 for 44,217 sequences.)
kept() { awk -F'\t' '/Bin\t# Targets/ { f = 1; next } f && $2 ~ /^[0-9]+$/ { s += $3; next } f { exit } END { print s + 0 }' "$O/$1.log"; }
KG=$(kept genome); KO=$(kept offset); KV=$(kept vsoffset)
echo "kept genome $KG offset $KO vsoffset $KV" | tee -a "$O/counts.txt"
check "$([ "$KG" = $((NP - MP)) ] && echo 1)" "HOMER's targets == peaks - N-masked (genome bg)" "every scannable peak scanned" "$KG = $NP - $MP"
check "$([ "$KO" = $((NO - MO)) ] && echo 1)" "HOMER's targets == offset - N-masked" "every scannable control scanned" "$KO = $NO - $MO"
check "$(g genome top | grep -qE '^(CTCF|BORIS)' && echo 1)" "top known motif, random genomic background" "a CTCF-family motif" "$(g genome top)"
check "$(g vsoffset top | grep -qE '^(CTCF|BORIS)' && echo 1)" "top known motif, offset as background" "a CTCF-family motif" "$(g vsoffset top)"
check "$(awk -v e="$(g genome ctcf_enrichment)" 'BEGIN{print (e + 0 >= 10) ? 1 : 0}')" "CTCF enrichment in peaks, genomic background" ">= 10x" "$(g genome ctcf_enrichment)x, rank $(g genome ctcf_rank)"
check "$(awk -v e="$(g offset ctcf_enrichment)" 'BEGIN{print (e + 0 <= 3) ? 1 : 0}')" "control: CTCF enrichment 100 kb away" "<= 3x" "$(g offset ctcf_enrichment)x, rank $(g offset ctcf_rank)"
check "$(printf '%s' "$DN_BEST" | grep -qE '^(CTCF|BORIS)' && echo 1)" "de novo motif 1's best match" "a CTCF-family motif" "$DN_CONS -> $DN_BEST"
# Monotonicity: a known motif's target count is a scan of the targets at that motif's fixed
# threshold. With an explicit -bg HOMER also drops targets that overlap a background region or sit
# in a GC bin the background does not fill, so vsoffset's targets are a SUBSET of genome's; every
# motif's count must fall by at least 0 and at most the number of targets dropped.
# HOMER's library lists a few motifs twice (same name and consensus); number the copies after a
# value sort, so each is paired with its counterpart (sorted pairing preserves the bound).
tc() { awk -F'\t' 'NR > 1 { print $1 "|" $2 "\t" $6 + 0 }' "$1" | sort -t "$(printf '\t')" -k1,1 -k2,2n \
  | awk -F'\t' '{ print $1 "#" (++c[$1]) "\t" $2 }'; }
tc "$O/genome.knownResults.txt" > tg.txt
tc "$O/vsoffset.knownResults.txt" > tv.txt
DROP=$((KG - KV))
set -- $(awk -F'\t' -v D="$DROP" 'FNR == NR { a[$1] = $2; n++; next }
  { m++; if (!($1 in a)) { bad++; next }; d = a[$1] - $2; if (d < 0 || d > D) bad++; if (d == 0) same++ }
  END { print bad + 0, m + 0, n + 0, same + 0 }' tg.txt tv.txt)
check "$([ "$1" = 0 ] && [ "$2" = "$3" ] && [ "$2" -gt 100 ] && [ "$DROP" -ge 0 ] && echo 1)" \
  "per motif: 0 <= targets(genome) - targets(vsoffset) <= dropped" "subset monotonicity, all motifs" "$1 of $2 violate; $DROP dropped; $4 unchanged"
sha256sum tg.txt | awk '{print "target_counts_sha256", $1}' >> "$O/fingerprints.txt"
sha256sum "$O/vsoffset.knownResults.txt" | awk '{print "vsoffset_knownResults_sha256", $1}' >> "$O/fingerprints.txt"
tc "$O/offset.knownResults.txt" | sha256sum | awk '{print "offset_target_counts_sha256", $1}' >> "$O/fingerprints.txt"
cat "$O/fingerprints.txt"
echo "fails $FAILS" | tee -a "$O/checks.txt"
[ "$FAILS" = 0 ] || exit 1
echo "HOMER-REAL OK" | tee -a "$O/checks.txt"
