#!/usr/bin/env bash
# Task 02 (Java image, Graviton4, once): RTG Tools vcfeval -- the haplotype-aware comparison GIAB's
# benchmarking uses (hap.py's vcfeval engine) -- on each chip's Clair3 VCF against GIAB v4.2.1,
# inside the benchmark's own confident regions. One scorer, the same jar bytes, for all three VCFs,
# so a score that differs across chips differs because the calls did.
set +e
set -uo pipefail
cd /tmp
. ./lib.sh
machine
RTG="java -Xmx6g -jar /tmp/RTG.jar"
rec java_version "$(java -version 2>&1 | head -1)"
rec rtg_version "$($RTG version 2>&1 | awk '/Product:/{print; exit}')"
$RTG version > "$O/rtg.version.txt" 2>&1

awk -F'\t' '$1=="chr20"' bench.bed > "$W/bench.chr20.bed"
rec benchmark_chr20_bases "$(awk '{s+=$3-$2} END{print s}' "$W/bench.chr20.bed")"
cp bench.chr20.vcf.gz "$W/truth.vcf.gz"
$RTG index -f vcf "$W/truth.vcf.gz" > /dev/null 2>&1
chk "truth indexed" "$([ -s "$W/truth.vcf.gz.tbi" ] && echo yes)" yes
tic; $RTG format -o "$W/chr20.sdf" chr20.fa > "$O/format.log" 2>&1; chk "rtg format exit" "$?" 0; toc rtg_format

for c in arm64 x86_64 amd; do
  cp "$c.vcf.gz" "$W/$c.vcf.gz"; cp "$c.vcf.gz.tbi" "$W/$c.vcf.gz.tbi"
  tic
  $RTG vcfeval -b "$W/truth.vcf.gz" -c "$W/$c.vcf.gz" -t "$W/chr20.sdf" -e "$W/bench.chr20.bed" \
    --threads 2 -o "$O/$c" > "$O/vcfeval.$c.log" 2>&1
  chk "vcfeval $c exit" "$?" 0
  toc "vcfeval_$c"
  cat "$O/$c/summary.txt"
  # the "None" row: every call that PASSes, no score threshold -- Clair3's own operating point
  awk -v c="$c" '$1=="None"{printf "%s_summary_tp_baseline\t%s\n%s_summary_tp_call\t%s\n%s_summary_fp\t%s\n%s_summary_fn\t%s\n%s_summary_precision\t%s\n%s_summary_recall\t%s\n%s_summary_f1\t%s\n",
     c,$2,c,$3,c,$4,c,$5,c,$6,c,$7,c,$8}' "$O/$c/summary.txt" >> "$O/results.tsv"
done
finish EVAL
