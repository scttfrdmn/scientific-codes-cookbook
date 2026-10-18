#!/usr/bin/env bash
# Compare the three chips' outputs by sha256 (each task writes outputs.sha256 beside them).
# Every file must match except mle.gene_summary.txt, whose permutation p-value / fdr columns are
# drawn from an unseeded np.random; mle.fixed.tsv (beta, z, Wald p, Wald fdr) must match.
#   compare.sh <bucket> [run label, default r3]
set -euo pipefail
B="${1:?pass your bucket}"; R="${2:-r3}"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
for p in runs runs-x86_64 runs-amd; do
  aws s3 cp "s3://$B/$p/mageck-real/$R/outputs.sha256" "$T/$p" --only-show-errors
done
for p in runs-x86_64 runs-amd; do
  echo "== Graviton4 vs $p =="
  diff "$T/runs" "$T/$p" && echo "all $(wc -l < "$T/runs") files identical" || true
done
