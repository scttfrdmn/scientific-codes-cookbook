#!/usr/bin/env bash
# Pull the three chips' outputs, keep the small evidence files under results/<chip>/, and write the
# cross-chip comparison to results/compare.txt.
#
#   bash collect.sh <bucket> <scratch dir>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
SCR="${2:?pass a scratch directory}"
HERE="$(cd "$(dirname "$0")" && pwd)"
LABELS="s5t8 s5t8-rep s5t1 s5t1-rep align align-rep"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs ;; *) p=runs-$chip ;; esac
  mkdir -p "$SCR/$chip" "$HERE/results/$chip"
  aws s3 cp --recursive --only-show-errors "$BUCKET/$p/muscle-real/pf01255/" "$SCR/$chip/"
  for f in checks.txt summary.txt times.txt host.txt; do cp "$SCR/$chip/$f" "$HERE/results/$chip/"; done
done
# the scorer real.sh ran, verbatim: SP/TC of one alignment against another
sed -n "/^cat > score.awk <<'AWK'$/,/^AWK$/p" "$HERE/real.sh" | sed '1d;$d' > "$SCR/score.awk"
sha() { shasum -a 256 "$1" | cut -c1-16; }
{
  echo "== sha256 (first 16) per alignment, per chip"
  printf '%-14s %-18s %-18s %s\n' label arm64 x86_64 amd
  for l in $LABELS; do
    printf '%-14s %-18s %-18s %s\n' "$l" "$(sha "$SCR/arm64/$l.fa")" "$(sha "$SCR/x86_64/$l.fa")" "$(sha "$SCR/amd/$l.fa")"
  done
  for l in s5t8 s5t1 align; do
    for chip in x86_64 amd; do
      if ! cmp -s "$SCR/arm64/$l.fa" "$SCR/$chip/$l.fa"; then
        echo "== $l: $chip scored against Graviton4's $l (SP/TC of one alignment against the other)"
        awk -f "$SCR/score.awk" "$SCR/arm64/$l.fa" "$SCR/$chip/$l.fa" | grep -E '^(sp|tc) '
      fi
    done
  done
} | tee "$HERE/results/compare.txt"
