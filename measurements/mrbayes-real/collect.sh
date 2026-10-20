#!/usr/bin/env bash
# Copy each chip's evidence into results/<chip>/ (the .t tree samples stay in the bucket), and
# compare the chains across chips.
#
#   bash collect.sh <bucket>
set -euo pipefail
B="s3://${1:?pass your bucket}"
HERE="$(cd "$(dirname "$0")" && pwd)"
for c in arm64 x86_64 amd; do
  case $c in arm64) r=runs ;; *) r=runs-$c ;; esac
  aws s3 cp --only-show-errors --recursive --exclude "*.t.gz" "$B/$r/mrbayes-real/" "$HERE/results/$c/"
done
R="$HERE/results"
echo "== chains, Intel and AMD against Graviton4 (.p without the per-run [ID] line) =="
for c in x86_64 amd; do
  for n in 1 2; do
    if cmp -s "$R/arm64/mcmc/mb.run$n.p" "$R/$c/mcmc/mb.run$n.p"; then s=identical; else s=differs; fi
    echo "$c run$n.p: $s"
  done
  for f in tfile-md5.txt asdsf.trace; do
    if cmp -s "$R/arm64/mcmc/$f" "$R/$c/mcmc/$f"; then s=identical; else s=differs; fi
    echo "$c $f: $s"
  done
done
for c in arm64 x86_64 amd; do echo "== $c"; grep -h -E 'FAIL|result' "$R/$c"/*/checks.txt "$R/$c"/*/score.tsv || true; done
