#!/usr/bin/env bash
# Pull each chip's small outputs into results/<chip>/ and compare them across chips.
# The per-file table (4.5 MB a chip) is compared in place and not kept.
set -euo pipefail
B="${1:?pass your bucket}"
HERE="$(cd "$(dirname "$0")" && pwd)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
for pair in arm64:runs intel:runs-x86_64 amd:runs-amd; do
  c="${pair%%:*}"; p="${pair#*:}"
  mkdir -p "$HERE/results/$c"
  for f in gsw-argo.log gsw-results.tsv gsw-years.tsv cpu.txt; do
    aws s3 cp --only-show-errors "s3://$B/$p/gsw-real/r1/$f" "$HERE/results/$c/$f"
  done
  aws s3 cp --only-show-errors "s3://$B/$p/gsw-real/r1/gsw-perfile.tsv" "$tmp/$c.tsv"
done
aws s3 cp --only-show-errors "s3://$B/runs/gsw-real/compare/compare.txt" "$HERE/results/compare.txt"
{
  for c in intel amd; do
    paste <(cut -f1,3,7 "$tmp/arm64.tsv") <(cut -f1,7 "$tmp/$c.tsv") | awk -F'\t' -v c="$c" '
      NR > 1 { if ($1 != $4) bad++; n++; if ($3 == $5) { same++; if ($2 > 0) samefull++ } }
      END { printf "arm64_vs_%s\tfiles %d\tdigest-identical %d (with converted levels: %d)\tname mismatches %d\n", c, n, same, samefull, bad }'
  done
  paste <(cut -f1,7 "$tmp/intel.tsv") <(cut -f1,7 "$tmp/amd.tsv") | awk -F'\t' '
    NR > 1 { n++; if ($2 == $4) same++ } END { printf "intel_vs_amd\tfiles %d\tdigest-identical %d\n", n, same }'
  for c in intel amd; do
    printf "results_tsv_arm64_vs_%s\t" "$c"
    diff <(grep -vE 'wall|maxrss|cgroup|slowest' "$HERE/results/arm64/gsw-results.tsv") \
         <(grep -vE 'wall|maxrss|cgroup|slowest' "$HERE/results/$c/gsw-results.tsv") | grep '^[<>]' | tr '\n' ' ' || true
    echo
  done
} | tee "$HERE/results/digests.txt"
