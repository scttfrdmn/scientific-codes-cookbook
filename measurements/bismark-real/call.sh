#!/usr/bin/env bash
# bismark-real, task 4 (MethylDackel image): the second caller, on both aligners' BAMs -- the
# recipe's 03-methyldackel-compare and 05-aligner-compare, at a whole sample's depth.
#
#   bash call.sh <threads>
#
# Needs /tmp/genome/<fa>(.fai), /tmp/bismark.sorted.bam(.bai) + /tmp/bismark.CpG.cov.gz (tasks 1-2),
# /tmp/bwameth.sorted.bam(.bai) (task 3). Two comparisons, each holding one thing fixed:
#   callers   Bismark's extractor vs MethylDackel on the SAME Bismark BAM, per cytosine. MethylDackel
#             is run with Bismark's own filters (-q 0: Bismark already kept only unique alignments;
#             -p 1: the lowest base-quality floor MethylDackel accepts), so this is like with like.
#   aligners  per CpG, Bismark vs bwa-meth, same caller, where both are sure: MAPQ >= 10 on both
#             BAMs (16% of Bismark's "unique best" hits carry MAPQ 0-1) and >= 10 calls in both.
# Runs on aarch.bio's arm64 image and bioconda's x86 one (BusyBox userland), so the glue is POSIX
# sh and awk. Writes /tmp/out/.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
export TMPDIR=/var/tmp HOME=/tmp
T=$1
O=/tmp/out
FA=/tmp/genome/GRCh38_no_alt_analysis_set.fa
mkdir -p "$O"
cd /tmp
FAILS=0
row() { printf '%-52s %-34s %s\n' "$1" "$2" "$3" | tee -a "$O/checks-call.txt"; }
check() { if [ "$1" = 1 ]; then row "$2" "$3" "ok   ($4)"; else row "$2" "$3" "FAIL ($4)"; FAILS=$((FAILS+1)); fi; }
{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "methyldackel $(MethylDackel --version 2>&1 | sed -n 1p)"
} | tee "$O/host-call.txt"
W=/var/tmp/call; mkdir -p "$W"

md() {  # md <name> <bam> <mapq>: one MethylDackel extract, timed
  local t0 t1 rc
  t0=$(date +%s)
  MethylDackel extract -q "$3" -p 1 -@ "$T" -o "$W/$1" "$FA" "$2" > "$O/md-$1.log" 2>&1
  rc=$?
  t1=$(date +%s)
  echo "md_$1 rc $rc wall_s $((t1-t0))" | tee -a "$O/times-call.txt"
  check "$([ "$rc" = 0 ] && [ -s "$W/$1_CpG.bedGraph" ] && echo 1)" "MethylDackel extract $1" "exit 0, bedGraph written" "$rc"
  grep -v '^track' "$W/$1_CpG.bedGraph" > "$W/$1.bg"
  sha256sum "$W/$1.bg" | awk -v n="$1" '{print n "_bedGraph_sha256", $1}' >> "$O/fingerprints-call.txt"
}
( while : ; do echo "$(date +%s) $(awk '/^anon /{print $2; exit}' /sys/fs/cgroup/memory.stat 2>/dev/null) $(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)" >> "$O/samples-call.txt"; sleep 5; done ) &
SPID=$!
md bismark bismark.sorted.bam 0
md bismarkq10 bismark.sorted.bam 10
md bwameth bwameth.sorted.bam 10

echo "== callers: Bismark's extractor vs MethylDackel, same BAM, per cytosine =="
gzip -dc bismark.CpG.cov.gz > "$W/bcov.txt"
# MethylDackel's bedGraph is 0-based (start of the C); Bismark's .cov is the 1-based C
awk -v D="$O/caller-diffs.txt" '
  FNR==NR { b[$1 ":" $2] = $5 " " $6; nb++; sb += $5 + $6; next }
  { k = $1 ":" ($2 + 1); nm++; sm += $5 + $6
    if (k in b) { both++; if (b[k] == $5 " " $6) eq++; else { ne++; if (ne <= 50) print "DIFF", k, "bismark", b[k], "md", $5, $6 > D }; delete b[k] }
    else { om++; if (om <= 50) print "ONLY_MD", k, $5, $6 > D } }
  END { for (k in b) { ob++; if (ob <= 50) print "ONLY_BISMARK", k, b[k] > D }
        printf "bismark_positions %.0f md_positions %.0f both %.0f equal %.0f differ %.0f only_bismark %.0f only_md %.0f bismark_calls %.0f md_calls %.0f\n", nb, nm, both, eq, ne, ob, om, sb, sm }
' "$W/bcov.txt" "$W/bismark.bg" | tee "$O/callers.txt"
read -r _ NB _ NM _ BOTH _ EQ _ NE _ OB _ OM _ SB _ SM < "$O/callers.txt"
check "$([ "$NB" -gt 0 ] && [ "$NB" = "$NM" ] && [ "$OB" = 0 ] && [ "$OM" = 0 ] && echo 1)" "callers: same cytosines reported" "IDENTICAL position sets" "$NB / $NM, only $OB / $OM"
check "$([ "$EQ" = "$NB" ] && [ "$NE" = 0 ] && echo 1)" "callers: per-cytosine counts" "EXACT at every position" "$EQ equal, $NE differ"
check "$([ "$SB" = "$SM" ] && echo 1)" "callers: total calls" "CONSERVED" "$SB / $SM"

echo "== aligners: Bismark vs bwa-meth, same caller, where both are sure =="
awk '
  FNR==NR { if ($5 + $6 >= 10) b[$1 ":" $2] = $5 " " $6; next }
  { k = $1 ":" $2; if ($5 + $6 >= 10 && (k in b)) { split(b[k], a, " ")
      x = a[1] / (a[1] + a[2]); y = $5 / ($5 + $6); n++
      sx += x; sy += y; sxx += x*x; syy += y*y; sxy += x*y; d = x - y; if (d < 0) d = -d; sd += d
      if (d > 0.25) big++; if (d == 0) same++
      # what two INDEPENDENT samples at these depths would differ by: E|X-Y| for two binomial
      # proportions, normal approximation, at the pooled level
      p = (a[1] + $5) / (a[1] + a[2] + $5 + $6); sb += sqrt(2 / 3.14159265) * sqrt(p * (1 - p) * (1 / (a[1] + a[2]) + 1 / ($5 + $6))) } }
  END { r = (n*sxy - sx*sy) / sqrt((n*sxx - sx*sx) * (n*syy - sy*sy))
        printf "cpgs_ge10_both %.0f pearson_r %.6f mean_abs_dbeta %.6f sampling_mean_abs_dbeta %.6f frac_dbeta_gt_0.25 %.6f frac_identical_beta %.6f mean_beta_bismark %.6f mean_beta_bwameth %.6f\n", n, r, sd/n, sb/n, big/n, same/n, sx/n, sy/n }
' "$W/bismarkq10.bg" "$W/bwameth.bg" | tee "$O/aligners.txt"

read -r _ AN _ AR _ AD _ AS _ < "$O/aligners.txt"
# Two aligners reading the same molecules can only disagree through reads one of them places and
# the other does not, so they must agree better than two independent samples of the site could.
check "$(awk -v n="$AN" -v d="$AD" -v s="$AS" 'BEGIN{print (n >= 1000 && d < s) ? 1 : 0}')" "aligners: mean |dbeta|, >=10x in both" "< independent-sampling E|dbeta|" "$AD vs $AS at $AN CpGs, r $AR"

echo "== MethylDackel perRead: observed, not asserted =="
# perRead.c declares the getopt return as `char c` and loops while it is >= 0. char is unsigned on
# aarch64, so getopt's -1 becomes 255 and every invocation ends in "Invalid option" -- on ARM only.
MethylDackel perRead -r chr22 "$FA" bismark.sorted.bam > "$W/chr22.perread" 2> "$O/perread.log"
echo "perread_chr22 rc $? lines $(wc -l < "$W/chr22.perread") first_stderr $(sed -n 1p "$O/perread.log")" | tee -a "$O/times-call.txt"
kill $SPID 2>/dev/null
awk 'NR==1{t0=$1; u0=$3} $2>x{x=$2} {t=$1; u=$3} END{printf "call_peak_anon_gib %.2f call_avg_busy_cores %.2f\n", x/1073741824, (t>t0)?(u-u0)/1e6/(t-t0):0}' "$O/samples-call.txt" | tee -a "$O/times-call.txt"
cat "$O/fingerprints-call.txt"
cp "$W/bismark.bg" "$O/methyldackel.bismark.CpG.bedGraph"; cp "$W/bismarkq10.bg" "$O/methyldackel.bismark.q10.CpG.bedGraph"; cp "$W/bwameth.bg" "$O/methyldackel.bwameth.q10.CpG.bedGraph"
gzip -f "$O"/methyldackel.*.bedGraph
rm -rf "$W"
echo "failures $FAILS" | tee -a "$O/checks-call.txt"
[ "$FAILS" = 0 ] || exit 1
echo "CALL OK"
