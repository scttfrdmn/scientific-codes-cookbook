#!/usr/bin/env bash
# Cross-chip comparison of the two genome runs, and a local copy of their small results.
#   bash compare.sh <bucket>
set -euo pipefail
B="s3://${1:?pass your bucket}"
D="$(cd "$(dirname "$0")" && pwd)/results"
A="$B/runs/plink-real/genome"; X="$B/runs-x86_64/plink-real/genome"
for f in results.tsv footprint.txt id-all.txt chrom-wall.txt pca.eigenval assoc-na.txt repeated-records.txt; do
  mkdir -p "$D/arm64" "$D/x86_64"
  aws s3 cp --only-show-errors "$A/$f" "$D/arm64/$f"
  aws s3 cp --only-show-errors "$X/$f" "$D/x86_64/$f"
done
echo "== byte-identical across chips? =="
for f in pca.eigenvec pca.eigenval adj.top1000 unadj.top1000 pheno.txt id-all.txt assoc-na.txt repeated-records.txt; do
  a=$(aws s3 cp --only-show-errors "$A/$f" - | shasum -a 256 | cut -c1-16)
  x=$(aws s3 cp --only-show-errors "$X/$f" - | shasum -a 256 | cut -c1-16)
  [ "$a" = "$x" ] && s=identical || s=DIFFER
  printf '%-22s %s %s %s\n' "$f" "$a" "$x" "$s"
done
echo "== results.tsv rows that differ (timings and host expected) =="
diff <(cut -f1,2 "$D/arm64/results.tsv") <(cut -f1,2 "$D/x86_64/results.tsv") || true
